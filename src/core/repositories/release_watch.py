"""Repository for durable category release-watch/retry rows."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from src.core.repositories.base import BaseRepository


_TERMINAL_STATUSES = {"completed", "cancelled", "expired"}
_DORMANT_STATUSES = {"queued", "candidate_found"}
_RETRYABLE_STATUSES = {"pending", "failed_retryable"}


class ReleaseWatchRepository(BaseRepository):
    """Tracks concrete category units that should be retried until available.

    The repository is category-neutral: it stores timing, retry state, and a
    category-provided requirements snapshot.  TV decides that a unit is SxxEyy;
    future categories can use the same rows for replay/event/book-release
    watches with different payloads.
    """

    @staticmethod
    def _now_dt() -> datetime:
        return datetime.now(timezone.utc)

    @classmethod
    def _now(cls) -> str:
        return cls._now_dt().isoformat()

    @staticmethod
    def _parse_iso(value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            # Date-only values are common from metadata providers.
            try:
                parsed = datetime.fromisoformat(text[:10])
            except ValueError:
                return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _iso(value: datetime | None) -> str:
        return value.astimezone(timezone.utc).isoformat() if value else ""

    @classmethod
    def due_in_hours(cls, hours: float) -> str:
        """Return an ISO timestamp at least a few minutes in the future."""
        return cls._iso(cls._now_dt() + timedelta(hours=max(float(hours), 0.1)))

    @classmethod
    def _initial_next_check(cls, *, watch_start_at: str = "", interval_hours: float = 2.0) -> str:
        """Choose the first retry time for a watch.

        Future release windows should not poll immediately.  If the category
        provides a watch start in the future, use it.  If the window is already
        open, schedule the first retry soon instead of pushing it out by a full
        interval so newly added active items are checked promptly.
        """
        now = cls._now_dt()
        start = cls._parse_iso(watch_start_at)
        if start and start > now:
            return cls._iso(start)
        return cls._iso(now + timedelta(minutes=5))

    async def upsert(
        self,
        *,
        category_id: str,
        item_id: str,
        unit_key: str,
        preferred_language: str = "",
        interval_hours: float = 2.0,
        expected_air_at: str = "",
        watch_start_at: str = "",
        expires_at: str = "",
        cadence_profile: str = "unknown",
        requirements: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        status: str = "pending",
    ) -> int:
        """Create/update one durable release watch.

        Repeated RSS detections for the same unit must not keep pushing
        ``next_check_at`` into the future.  Existing retryable watches keep their
        due time unless they were dormant/terminal or the category supplies a
        future watch window earlier than the current due time.
        """
        now = self._now()
        payload_json = json.dumps(payload or {}, ensure_ascii=False, default=str)
        requirements_json = json.dumps(requirements or {}, ensure_ascii=False, default=str)
        existing = await self._db.execute_fetchall(
            "SELECT * FROM release_watches WHERE category_id = ? AND item_id = ? AND unit_key = ?",
            (category_id, item_id, unit_key),
        )
        initial_next = self._initial_next_check(watch_start_at=watch_start_at, interval_hours=interval_hours)
        if existing:
            row = existing[0]
            existing_status = str(row["status"] or "")
            existing_next = str(row["next_check_at"] or "")
            next_check_at = existing_next
            if existing_status not in _RETRYABLE_STATUSES or not existing_next:
                next_check_at = initial_next
            else:
                new_start = self._parse_iso(initial_next)
                old_due = self._parse_iso(existing_next)
                now_dt = self._now_dt()
                if new_start and old_due and new_start < old_due:
                    # A category discovered an earlier valid release window.
                    next_check_at = initial_next
                elif new_start and old_due and new_start > now_dt and old_due < new_start:
                    # Upgrade legacy/immediate watches to an air-date-aware
                    # future start instead of polling before the episode airs.
                    next_check_at = initial_next
            await self._db.execute(
                """UPDATE release_watches
                   SET preferred_language = ?, status = ?, next_check_at = ?, interval_hours = ?,
                       expected_air_at = ?, watch_start_at = ?, expires_at = ?, cadence_profile = ?,
                       requirements_json = ?, payload_json = ?, updated_at = ?
                   WHERE id = ?""",
                (
                    preferred_language,
                    status,
                    next_check_at,
                    interval_hours,
                    expected_air_at,
                    watch_start_at,
                    expires_at,
                    cadence_profile,
                    requirements_json,
                    payload_json,
                    now,
                    row["id"],
                ),
            )
            await self._db.commit()
            return int(row["id"])

        next_check_at = initial_next
        cursor = await self._db.execute(
            """INSERT INTO release_watches
               (category_id, item_id, unit_key, preferred_language, status, next_check_at,
                interval_hours, expected_air_at, watch_start_at, expires_at, cadence_profile,
                attempts, last_error, payload_json, requirements_json, last_candidate_summary_json,
                last_outcome_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, '', ?, ?, '{}', '{}', ?, ?)""",
            (
                category_id,
                item_id,
                unit_key,
                preferred_language,
                status,
                next_check_at,
                interval_hours,
                expected_air_at,
                watch_start_at,
                expires_at,
                cadence_profile,
                payload_json,
                requirements_json,
                now,
                now,
            ),
        )
        await self._db.commit()
        return int(cursor.lastrowid or 0)


    async def list(self, *, status: str | None = None, category_id: str | None = None, item_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        """Return release watches for diagnostics/UI without exposing category internals.

        This is intentionally a read-side helper.  Generic callers may display
        the category-provided payload/requirements and typed state, but they do
        not interpret TV-specific unit keys.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(str(status))
        if category_id:
            clauses.append("category_id = ?")
            params.append(str(category_id))
        if item_id:
            clauses.append("item_id = ?")
            params.append(str(item_id))
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(max(1, min(int(limit or 200), 500)))
        cursor = await self._db.execute(
            f"""SELECT * FROM release_watches
                {where}
                ORDER BY
                    CASE status
                        WHEN 'pending' THEN 0
                        WHEN 'failed_retryable' THEN 1
                        WHEN 'queued' THEN 2
                        WHEN 'candidate_found' THEN 3
                        WHEN 'completed' THEN 4
                        ELSE 5
                    END,
                    next_check_at ASC, updated_at DESC
                LIMIT ?""",
            tuple(params),
        )
        rows = await cursor.fetchall()
        return [self._row_to_dict(row) for row in rows]

    async def stale_queued(self, *, older_than_minutes: float = 30.0, limit: int = 50) -> list[dict[str, Any]]:
        """Return queued watches that need reconciliation against downloads.

        A queued watch is dormant while the related download is active.  If the
        queued/download row disappears or fails before import, the scheduler
        should return the watch to retryable instead of leaving it stuck forever.
        """
        cutoff = self._iso(self._now_dt() - timedelta(minutes=max(float(older_than_minutes), 1.0)))
        cursor = await self._db.execute(
            """SELECT * FROM release_watches
               WHERE status = 'queued' AND updated_at <= ?
               ORDER BY updated_at ASC LIMIT ?""",
            (cutoff, max(1, min(int(limit or 50), 200))),
        )
        rows = await cursor.fetchall()
        return [self._row_to_dict(row) for row in rows]

    async def reset_to_retryable(
        self,
        category_id: str,
        item_id: str,
        unit_key: str,
        *,
        error: str = "queued download is no longer active",
        interval_hours: float = 2.0,
        outcome: dict[str, Any] | None = None,
    ) -> None:
        """Return a dormant queued watch to retryable after queue/download failure."""
        now = self._now()
        await self._db.execute(
            """UPDATE release_watches
               SET status = 'failed_retryable', last_error = ?, next_check_at = ?,
                   last_outcome_json = ?, updated_at = ?
               WHERE category_id = ? AND item_id = ? AND unit_key = ?
                 AND status = 'queued'""",
            (
                error,
                self.due_in_hours(interval_hours),
                json.dumps(outcome or {"status": "queued_recovered_to_retryable"}, ensure_ascii=False, default=str),
                now,
                category_id,
                item_id,
                unit_key,
            ),
        )
        await self._db.commit()

    async def cancel_unit(
        self,
        category_id: str,
        item_id: str,
        unit_key: str,
        *,
        error: str = "cancelled",
        outcome: dict[str, Any] | None = None,
    ) -> None:
        """Cancel one release watch unit so retries do not resurrect user-cancelled work."""
        now = self._now()
        await self._db.execute(
            """UPDATE release_watches
               SET status = 'cancelled', next_check_at = '', last_error = ?,
                   last_outcome_json = ?, updated_at = ?
               WHERE category_id = ? AND item_id = ? AND unit_key = ?
                 AND status NOT IN ('completed', 'expired')""",
            (
                error,
                json.dumps(outcome or {}, ensure_ascii=False, default=str),
                now,
                category_id,
                item_id,
                unit_key,
            ),
        )
        await self._db.commit()

    async def retire_missing_for_item(
        self,
        category_id: str,
        item_id: str,
        active_unit_keys: set[str],
        *,
        error: str = "no longer present in category watch plan",
    ) -> int:
        """Cancel nonterminal watches omitted by the category's latest plan.

        Categories rebuild watch plans from canonical library/provider state. If
        a previously watched unit disappears from that plan, the old retry row is
        stale and must not keep searching/queueing in the background.
        """
        now = self._now()
        rows = await self.list(category_id=category_id, item_id=item_id, limit=500)
        active = {str(key or "").strip() for key in active_unit_keys if str(key or "").strip()}
        cancelled = 0
        for row in rows:
            status = str(row.get("status") or "")
            unit_key = str(row.get("unit_key") or "").strip()
            if status in _TERMINAL_STATUSES or unit_key in active:
                continue
            await self._db.execute(
                """UPDATE release_watches
                   SET status = 'cancelled', next_check_at = '', last_error = ?,
                       last_outcome_json = ?, updated_at = ?
                   WHERE id = ?""",
                (
                    error,
                    json.dumps({"status": "retired_by_watch_plan", "active_unit_keys": sorted(active)}, ensure_ascii=False),
                    now,
                    int(row.get("id") or 0),
                ),
            )
            cancelled += 1
        if cancelled:
            await self._db.commit()
        return cancelled

    async def due(self, *, limit: int = 25) -> list[dict[str, Any]]:
        """Return retryable watches whose retry time has arrived."""
        now = self._now()
        cursor = await self._db.execute(
            """SELECT * FROM release_watches
               WHERE status IN ('pending', 'failed_retryable')
                 AND next_check_at <= ?
                 AND (expires_at = '' OR expires_at > ?)
               ORDER BY next_check_at ASC LIMIT ?""",
            (now, now, max(1, min(int(limit or 25), 100))),
        )
        rows = await cursor.fetchall()
        return [self._row_to_dict(row) for row in rows]

    async def expire_overdue(self, *, limit: int = 100) -> int:
        """Mark retryable watches past their expiry window as expired."""
        now = self._now()
        cursor = await self._db.execute(
            """UPDATE release_watches
               SET status = 'expired', next_check_at = '', updated_at = ?,
                   last_error = CASE WHEN last_error = '' THEN 'watch expired' ELSE last_error END
               WHERE status IN ('pending', 'failed_retryable')
                 AND expires_at != '' AND expires_at <= ?""",
            (now, now),
        )
        await self._db.commit()
        return int(cursor.rowcount or 0)

    async def record_attempt(
        self,
        watch_id: int,
        *,
        status: str = "pending",
        error: str = "",
        interval_hours: float | None = None,
        candidate_summary: dict[str, Any] | None = None,
        outcome: dict[str, Any] | None = None,
    ) -> None:
        """Persist the outcome of one retry attempt."""
        now = self._now()
        if status in _RETRYABLE_STATUSES:
            hours = interval_hours or 2.0
            next_check_at = self.due_in_hours(hours)
        else:
            next_check_at = ""
        await self._db.execute(
            """UPDATE release_watches
               SET status = ?, attempts = attempts + 1, last_error = ?, next_check_at = ?,
                   last_candidate_summary_json = ?, last_outcome_json = ?, updated_at = ?
               WHERE id = ?""",
            (
                status,
                error,
                next_check_at,
                json.dumps(candidate_summary or {}, ensure_ascii=False, default=str),
                json.dumps(outcome or {}, ensure_ascii=False, default=str),
                now,
                watch_id,
            ),
        )
        await self._db.commit()

    async def complete(self, category_id: str, item_id: str, unit_key: str) -> None:
        """Mark a release watch as completed after the requested unit is imported/present."""
        now = self._now()
        await self._db.execute(
            """UPDATE release_watches SET status = 'completed', next_check_at = '', updated_at = ?
               WHERE category_id = ? AND item_id = ? AND unit_key = ?""",
            (now, category_id, item_id, unit_key),
        )
        await self._db.commit()

    async def mark_queued(self, category_id: str, item_id: str, unit_key: str, *, outcome: dict[str, Any] | None = None) -> None:
        """Mark a watch as queued; completion should be confirmed by import/library state."""
        now = self._now()
        await self._db.execute(
            """UPDATE release_watches
               SET status = 'queued', next_check_at = '', last_outcome_json = ?, updated_at = ?
               WHERE category_id = ? AND item_id = ? AND unit_key = ?""",
            (json.dumps(outcome or {}, ensure_ascii=False, default=str), now, category_id, item_id, unit_key),
        )
        await self._db.commit()

    @staticmethod
    def _loads_json(value: Any) -> dict[str, Any]:
        try:
            parsed = json.loads(value or "{}")
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    @classmethod
    def _row_to_dict(cls, row: Any) -> dict[str, Any]:
        data = dict(row)
        data["payload"] = cls._loads_json(data.get("payload_json"))
        data["requirements"] = cls._loads_json(data.get("requirements_json"))
        data["last_candidate_summary"] = cls._loads_json(data.get("last_candidate_summary_json"))
        data["last_outcome"] = cls._loads_json(data.get("last_outcome_json"))
        return data
