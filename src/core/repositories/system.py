"""
System repository for LJS.
Handles conversation history, behavior log, and scheduled tasks.
"""

from typing import Any, Optional
from src.core.models import ScheduledTask
from src.core.repositories.base import BaseRepository


class SystemRepository(BaseRepository):
    """Repository for system-related data."""

    async def get_preference(self, key: str, default: str = "") -> str:
        """Get a preference value by key."""
        cursor = await self._db.execute(
            "SELECT value FROM preferences WHERE key = ?", (key,)
        )
        row = await cursor.fetchone()
        return row["value"] if row else default

    async def set_preference(self, key: str, value: str) -> None:
        """Set a preference value."""
        await self._db.execute(
            "INSERT OR REPLACE INTO preferences (key, value) VALUES (?, ?)",
            (key, value),
        )
        await self._db.commit()

    async def delete_preference(self, key: str) -> None:
        """Delete a preference by key."""
        await self._db.execute(
            "DELETE FROM preferences WHERE key = ?", (key,)
        )
        await self._db.commit()

    async def get_all_preferences(self) -> dict[str, str]:
        """Get all preferences as a dictionary."""
        cursor = await self._db.execute("SELECT key, value FROM preferences")
        rows = await cursor.fetchall()
        return {r["key"]: r["value"] for r in rows}

    async def add_conversation_turn(self, session_id: str, role: str,
                                     content: str, tool_call_id: str | None = None) -> int:
        """Add a conversation turn to the history and return its ID."""
        cursor = await self._db.execute(
            """INSERT INTO conversation_history
            (session_id, role, content, tool_call_id, created_at)
            VALUES (?, ?, ?, ?, datetime('now'))""",
            (session_id, role, content, tool_call_id),
        )
        await self._db.commit()
        return int(cursor.lastrowid or 0)

    async def get_conversation_history(self, session_id: str,
                                        limit: int = 50) -> list[dict]:
        """Get recent conversation turns."""
        cursor = await self._db.execute(
            """SELECT * FROM conversation_history
            WHERE session_id = ?
            ORDER BY id DESC LIMIT ?""",
            (session_id, limit),
        )
        rows = await cursor.fetchall()
        return list(reversed([dict(r) for r in rows]))

    async def get_conversation_turn_count(self, session_id: str) -> int:
        """Count conversation turns."""
        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM conversation_history WHERE session_id = ?",
            (session_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def delete_conversation_history(self, session_id: str) -> None:
        """Delete all conversation history for a session."""
        await self._db.execute("DELETE FROM conversation_history WHERE session_id = ?", (session_id,))
        await self._db.commit()

    async def delete_conversation_turns_before(self, session_id: str,
                                                before_id: int) -> int:
        """Delete conversation turns before a specific ID."""
        cursor = await self._db.execute(
            "DELETE FROM conversation_history WHERE session_id = ? AND id < ?",
            (session_id, before_id),
        )
        await self._db.commit()
        return cursor.rowcount

    async def get_active_session_ids(self, days: int = 7) -> list[str]:
        """Get list of active session IDs from the past N days."""
        cursor = await self._db.execute(
            """SELECT DISTINCT session_id FROM conversation_history 
               WHERE session_id IS NOT NULL AND session_id != ''
               AND created_at > datetime('now', ?)""",
            (f"-{days} days",),
        )
        rows = await cursor.fetchall()
        return [r["session_id"] for r in rows]

    async def list_conversation_turns(self, limit: int = 10000) -> list[dict[str, Any]]:
        """Return conversation turns newest-first for maintenance/reindex jobs."""
        cursor = await self._db.execute(
            """SELECT id, session_id, role, content, tool_call_id, created_at
               FROM conversation_history
               ORDER BY id DESC
               LIMIT ?""",
            (int(limit),),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def delete_preferences_by_prefix(self, prefix: str) -> int:
        """Delete preferences whose key starts with the supplied prefix."""
        cursor = await self._db.execute(
            "DELETE FROM preferences WHERE key LIKE ?",
            (f"{prefix}%",),
        )
        await self._db.commit()
        return int(cursor.rowcount or 0)

    async def upsert_taste_signal(self, signal: dict[str, Any]) -> int:
        """Insert or update a category-scoped taste signal.

        Signals are immutable-ish evidence events from conversation, library,
        downloads, or explicit review.  Preference profiles are derived from
        this log plus item metadata; item facts are never treated as preference
        by themselves.
        """
        import json

        user_id = str(signal.get("user_id") or "")
        category_id = str(signal.get("category_id") or "")
        item_id = str(signal.get("item_id") or signal.get("display_name") or "").strip()
        display_name = str(signal.get("display_name") or item_id).strip()
        signal_type = str(signal.get("signal_type") or "mention").strip().lower()
        polarity = str(signal.get("polarity") or "neutral").strip().lower()
        source = str(signal.get("source") or "conversation").strip().lower()
        metadata = signal.get("metadata") or {}
        interpreted_facets = signal.get("interpreted_facets") or {}
        if not isinstance(metadata, dict):
            metadata = {"value": metadata}
        if not isinstance(interpreted_facets, dict):
            interpreted_facets = {"value": interpreted_facets}
        cursor = await self._db.execute(
            """INSERT INTO category_taste_signals
               (user_id, category_id, item_id, display_name, signal_type, polarity,
                strength, weight, source, confidence, metadata_json,
                interpreted_facets_json, evidence_text, notes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
               ON CONFLICT(user_id, category_id, item_id, signal_type, source) DO UPDATE SET
                    display_name = excluded.display_name,
                    polarity = excluded.polarity,
                    strength = excluded.strength,
                    weight = excluded.weight,
                    confidence = excluded.confidence,
                    metadata_json = excluded.metadata_json,
                    interpreted_facets_json = excluded.interpreted_facets_json,
                    evidence_text = excluded.evidence_text,
                    notes = excluded.notes,
                    updated_at = datetime('now')
            """,
            (
                user_id, category_id, item_id, display_name, signal_type, polarity,
                float(signal.get("strength", 0.0)), float(signal.get("weight", 1.0)), source,
                float(signal.get("confidence", 1.0)),
                json.dumps(metadata, ensure_ascii=False),
                json.dumps(interpreted_facets, ensure_ascii=False),
                str(signal.get("evidence_text") or ""),
                str(signal.get("notes") or ""),
            ),
        )
        await self._db.commit()
        if cursor.lastrowid:
            return int(cursor.lastrowid)
        lookup = await self._db.execute(
            """SELECT id FROM category_taste_signals
               WHERE user_id = ? AND category_id = ? AND item_id = ?
                 AND signal_type = ? AND source = ?""",
            (user_id, category_id, item_id, signal_type, source),
        )
        row = await lookup.fetchone()
        return int(row["id"]) if row else 0

    async def list_taste_signals(
        self,
        user_id: str | None = None,
        category_id: str | None = None,
        signal_types: list[str] | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Return category-scoped taste signals, newest first."""
        import json

        conditions: list[str] = []
        params: list[Any] = []
        if user_id is not None:
            conditions.append("user_id = ?")
            params.append(user_id)
        if category_id:
            conditions.append("category_id = ?")
            params.append(category_id)
        if signal_types:
            placeholders = ",".join("?" for _ in signal_types)
            conditions.append(f"signal_type IN ({placeholders})")
            params.extend(signal_types)

        query = "SELECT * FROM category_taste_signals"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY updated_at DESC, id DESC LIMIT ?"
        params.append(int(limit))
        cursor = await self._db.execute(query, params)
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            data = dict(row)
            try:
                data["metadata"] = json.loads(data.get("metadata_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                data["metadata"] = {}
            try:
                data["interpreted_facets"] = json.loads(data.get("interpreted_facets_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                data["interpreted_facets"] = {}
            result.append(data)
        return result

    async def replace_taste_facet_scores(
        self,
        user_id: str,
        category_id: str,
        scores: list[dict[str, Any]],
    ) -> None:
        """Replace derived facet scores for one user/category snapshot."""
        import json

        normalized_user = user_id or ""
        await self._db.execute(
            "DELETE FROM category_taste_facet_scores WHERE user_id = ? AND category_id = ?",
            (normalized_user, category_id),
        )
        for score in scores:
            await self._db.execute(
                """INSERT INTO category_taste_facet_scores
                   (user_id, category_id, facet_key, facet_value, affinity,
                    positive_score, negative_score, confidence, evidence_count,
                    source_signal_ids_json, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                (
                    normalized_user,
                    category_id,
                    str(score.get("facet_key") or ""),
                    str(score.get("facet_value") or ""),
                    float(score.get("affinity") or 0.0),
                    float(score.get("positive_score") or 0.0),
                    float(score.get("negative_score") or 0.0),
                    float(score.get("confidence") or 0.0),
                    int(score.get("evidence_count") or 0),
                    json.dumps(score.get("source_signal_ids") or [], ensure_ascii=False),
                ),
            )
        await self._db.commit()

    async def list_taste_facet_scores(
        self,
        user_id: str | None = None,
        category_id: str | None = None,
        facet_key: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Return derived facet scores for review/debugging."""
        import json

        conditions: list[str] = []
        params: list[Any] = []
        if user_id is not None:
            conditions.append("user_id = ?")
            params.append(user_id)
        if category_id:
            conditions.append("category_id = ?")
            params.append(category_id)
        if facet_key:
            conditions.append("facet_key = ?")
            params.append(facet_key)
        query = "SELECT * FROM category_taste_facet_scores"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY ABS(affinity) DESC, evidence_count DESC LIMIT ?"
        params.append(int(limit))
        cursor = await self._db.execute(query, params)
        rows = await cursor.fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            try:
                data["source_signal_ids"] = json.loads(data.get("source_signal_ids_json") or "[]")
            except (TypeError, json.JSONDecodeError):
                data["source_signal_ids"] = []
            result.append(data)
        return result

    async def upsert_taste_profile_snapshot(
        self,
        user_id: str,
        category_id: str,
        profile: dict[str, Any],
        summary: str = "",
        evidence_count: int = 0,
    ) -> None:
        """Persist a reviewable profile snapshot derived from signal evidence."""
        import json

        await self._db.execute(
            """INSERT INTO category_taste_profile_snapshots
               (user_id, category_id, profile_json, summary, evidence_count, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))
               ON CONFLICT(user_id, category_id) DO UPDATE SET
                    profile_json = excluded.profile_json,
                    summary = excluded.summary,
                    evidence_count = excluded.evidence_count,
                    updated_at = datetime('now')""",
            (
                user_id or "",
                category_id,
                json.dumps(profile, ensure_ascii=False, default=str),
                summary or "",
                int(evidence_count),
            ),
        )
        await self._db.commit()

    async def get_taste_profile_snapshot(
        self,
        user_id: str | None,
        category_id: str,
    ) -> dict[str, Any] | None:
        """Return one stored taste profile snapshot, if present."""
        import json

        cursor = await self._db.execute(
            """SELECT * FROM category_taste_profile_snapshots
               WHERE user_id = ? AND category_id = ?""",
            (user_id or "", category_id),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        data = dict(row)
        try:
            data["profile"] = json.loads(data.get("profile_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            data["profile"] = {}
        return data

    async def delete_taste_signal(self, signal_id: int, user_id: str | None = None) -> bool:
        """Delete one category taste signal, optionally scoped to a user."""
        params: list[Any] = [int(signal_id)]
        query = "DELETE FROM category_taste_signals WHERE id = ?"
        if user_id is not None:
            query += " AND user_id = ?"
            params.append(user_id)
        cursor = await self._db.execute(query, params)
        await self._db.commit()
        return int(cursor.rowcount or 0) > 0

    async def update_taste_signal_confidence(
        self,
        signal_id: int,
        confidence: float,
        weight: float | None = None,
        user_id: str | None = None,
    ) -> bool:
        """Adjust confidence/weight for reviewable taste evidence."""
        params: list[Any] = [max(0.0, min(1.0, float(confidence)))]
        set_clause = "confidence = ?, updated_at = datetime('now')"
        if weight is not None:
            set_clause = "confidence = ?, weight = ?, updated_at = datetime('now')"
            params.append(float(weight))
        params.append(int(signal_id))
        query = f"UPDATE category_taste_signals SET {set_clause} WHERE id = ?"
        if user_id is not None:
            query += " AND user_id = ?"
            params.append(user_id)
        cursor = await self._db.execute(query, params)
        await self._db.commit()
        return int(cursor.rowcount or 0) > 0

    async def log_behavior(self, user_id: str, action: str, **kwargs: Any) -> None:
        """Record a user behavior event."""
        item_name = kwargs.get("item_name") or ""
        await self._db.execute(
            """INSERT INTO behavior_log
            (user_id, action, category_id, item_id, item_name, resolution, codec,
             release_group, file_size_mb, quality_score, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (
                user_id, action, kwargs.get("category_id", ""),
                kwargs.get("item_id", item_name), item_name,
                kwargs.get("resolution"), kwargs.get("codec"),
                kwargs.get("release_group"), kwargs.get("file_size_mb"),
                kwargs.get("quality_score"),
            ),
        )
        await self._db.commit()

    async def get_behavior_log(self, user_id: str, action: str | None = None,
                                limit: int = 100) -> list[dict]:
        """Get behavior events."""
        if action:
            cursor = await self._db.execute(
                "SELECT * FROM behavior_log WHERE user_id = ? AND action = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, action, limit),
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM behavior_log WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def upsert_scheduled_task(self, task: ScheduledTask) -> None:
        """Insert or update a scheduled task."""
        last_run = task.last_run_at.isoformat() if task.last_run_at else None
        created = task.created_at.isoformat() if task.created_at else None
        await self._db.execute(
            """INSERT OR REPLACE INTO scheduled_tasks
               (id, prompt, interval_minutes, user_id, channel, enabled, last_run_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (task.id, task.prompt, task.interval_minutes, task.user_id,
             task.channel, 1 if task.enabled else 0, last_run, created),
        )
        await self._db.commit()

    async def delete_scheduled_task(self, task_id: str) -> None:
        """Delete a scheduled task."""
        await self._db.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))
        await self._db.commit()

    async def get_scheduled_tasks(self, user_id: str | None = None,
                                   enabled_only: bool = False) -> list[dict]:
        """Get scheduled tasks."""
        query = "SELECT * FROM scheduled_tasks"
        params = []
        conditions = []
        if user_id is not None:
            conditions.append("user_id = ?")
            params.append(user_id)
        if enabled_only:
            conditions.append("enabled = 1")
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        cursor = await self._db.execute(query, params)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def add_deletion_log(self, title: str, media_type: str,
                                file_path: str,
                                season: int | None = None,
                                episode: int | None = None,
                                category_id: str = "",
                                item_id: str = "",
                                item_name: str | None = None) -> None:
        """Record that a file was deleted in category-neutral storage."""
        resolved_name = item_name or title
        await self._db.execute(
            """INSERT INTO deletion_log
               (title, media_type, category_id, item_id, item_name, season, episode, file_path, deleted_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (title, media_type, category_id, item_id or resolved_name, resolved_name, season, episode, file_path),
        )
        await self._db.commit()

    async def get_deletion_log(self, limit: int = 50) -> list[dict]:
        """Get recent deletion log entries."""
        cursor = await self._db.execute(
            "SELECT * FROM deletion_log ORDER BY deleted_at DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
