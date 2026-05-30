"""Repository for durable release watch/retry rows."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from src.core.repositories.base import BaseRepository


class ReleaseWatchRepository(BaseRepository):
    """Tracks specific category units that should be retried until available."""

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def due_in_hours(hours: float) -> str:
        """Return an ISO timestamp at least a few minutes in the future."""
        return (datetime.now(timezone.utc) + timedelta(hours=max(float(hours), 0.1))).isoformat()

    async def upsert(
        self,
        *,
        category_id: str,
        item_id: str,
        unit_key: str,
        preferred_language: str = "",
        interval_hours: float = 2.0,
        payload: dict[str, Any] | None = None,
        status: str = "pending",
    ) -> int:
        """Create/update one active release watch.

        Repeated RSS detections for the same unit must not keep pushing
        ``next_check_at`` into the future.  Otherwise a frequently-seen release
        can starve its own retry loop forever.
        """
        now = self._now()
        payload_json = json.dumps(payload or {}, ensure_ascii=False, default=str)
        existing = await self._db.execute_fetchall(
            "SELECT * FROM release_watches WHERE category_id = ? AND item_id = ? AND unit_key = ?",
            (category_id, item_id, unit_key),
        )
        if existing:
            row = existing[0]
            existing_status = str(row["status"] or "")
            existing_next = str(row["next_check_at"] or "")
            next_check_at = (
                existing_next
                if existing_status == "pending" and existing_next
                else self.due_in_hours(interval_hours)
            )
            await self._db.execute(
                """UPDATE release_watches
                   SET preferred_language = ?, status = ?, next_check_at = ?, interval_hours = ?,
                       payload_json = ?, updated_at = ?
                   WHERE id = ?""",
                (preferred_language, status, next_check_at, interval_hours, payload_json, now, row["id"]),
            )
            await self._db.commit()
            return int(row["id"])

        next_check_at = self.due_in_hours(interval_hours)
        cursor = await self._db.execute(
            """INSERT INTO release_watches
               (category_id, item_id, unit_key, preferred_language, status, next_check_at,
                interval_hours, attempts, payload_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)""",
            (category_id, item_id, unit_key, preferred_language, status, next_check_at, interval_hours, payload_json, now, now),
        )
        await self._db.commit()
        return int(cursor.lastrowid or 0)

    async def due(self, *, limit: int = 25) -> list[dict[str, Any]]:
        """Return pending watches whose retry time has arrived."""
        now = self._now()
        cursor = await self._db.execute(
            """SELECT * FROM release_watches
               WHERE status = 'pending' AND next_check_at <= ?
               ORDER BY next_check_at ASC LIMIT ?""",
            (now, max(1, min(int(limit or 25), 100))),
        )
        rows = await cursor.fetchall()
        return [self._row_to_dict(row) for row in rows]

    async def record_attempt(self, watch_id: int, *, status: str = "pending", error: str = "", interval_hours: float | None = None) -> None:
        """Persist the outcome of one retry attempt."""
        now = self._now()
        if status == "pending":
            hours = interval_hours or 2.0
            next_check_at = self.due_in_hours(hours)
        else:
            next_check_at = ""
        await self._db.execute(
            """UPDATE release_watches
               SET status = ?, attempts = attempts + 1, last_error = ?, next_check_at = ?, updated_at = ?
               WHERE id = ?""",
            (status, error, next_check_at, now, watch_id),
        )
        await self._db.commit()

    async def complete(self, category_id: str, item_id: str, unit_key: str) -> None:
        """Mark a release watch as completed."""
        now = self._now()
        await self._db.execute(
            """UPDATE release_watches SET status = 'completed', next_check_at = '', updated_at = ?
               WHERE category_id = ? AND item_id = ? AND unit_key = ?""",
            (now, category_id, item_id, unit_key),
        )
        await self._db.commit()

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        data = dict(row)
        raw = data.get("payload_json") or "{}"
        try:
            data["payload"] = json.loads(raw)
        except Exception:
            data["payload"] = {}
        return data
