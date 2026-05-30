"""Persistent notification repository for LJS web/bridge notifications."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from src.core.repositories.base import BaseRepository


class NotificationRepository(BaseRepository):
    """Stores durable user notifications and optional user actions."""

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    async def create(
        self,
        *,
        title: str,
        body: str,
        level: str = "info",
        category_id: str = "",
        item_id: str = "",
        event_type: str = "general",
        actions: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
        dedupe_key: str = "",
        return_status: bool = False,
    ) -> int | tuple[int, bool]:
        """Create or update a notification and return its id.

        When ``return_status`` is true, also return whether this call inserted a
        new row.  NotificationService uses that to avoid bridge spam for
        deduplicated RSS events while still updating the durable web inbox.
        """
        now = self._now()
        actions_json = json.dumps(actions or [], ensure_ascii=False, default=str)
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False, default=str)
        if dedupe_key:
            cursor = await self._db.execute(
                "SELECT id FROM notifications WHERE dedupe_key = ? AND status IN ('unread', 'read')",
                (dedupe_key,),
            )
            row = await cursor.fetchone()
            if row:
                await self._db.execute(
                    """UPDATE notifications
                       SET title = ?, body = ?, level = ?, category_id = ?, item_id = ?,
                           event_type = ?, status = 'unread', read_at = NULL,
                           actions_json = ?, metadata_json = ?, updated_at = ?
                       WHERE id = ?""",
                    (title, body, level, category_id, item_id, event_type, actions_json, metadata_json, now, row["id"]),
                )
                await self._db.commit()
                notification_id = int(row["id"])
                return (notification_id, False) if return_status else notification_id
        cursor = await self._db.execute(
            """INSERT INTO notifications
               (title, body, level, category_id, item_id, event_type, status,
                actions_json, metadata_json, dedupe_key, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'unread', ?, ?, ?, ?, ?)""",
            (title, body, level, category_id, item_id, event_type, actions_json, metadata_json, dedupe_key, now, now),
        )
        await self._db.commit()
        notification_id = int(cursor.lastrowid or 0)
        return (notification_id, True) if return_status else notification_id

    async def list(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent notifications."""
        safe_limit = max(1, min(int(limit or 50), 200))
        params: list[Any] = []
        query = "SELECT * FROM notifications"
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(safe_limit)
        cursor = await self._db.execute(query, tuple(params))
        rows = await cursor.fetchall()
        return [self._row_to_dict(row) for row in rows]

    async def unread_count(self) -> int:
        """Return the number of unread notifications."""
        cursor = await self._db.execute("SELECT COUNT(*) AS cnt FROM notifications WHERE status = 'unread'")
        row = await cursor.fetchone()
        return int(row["cnt"] if row else 0)

    async def mark_read(self, notification_id: int) -> bool:
        """Mark one notification as read."""
        now = self._now()
        cursor = await self._db.execute(
            "UPDATE notifications SET status = 'read', read_at = ?, updated_at = ? WHERE id = ?",
            (now, now, notification_id),
        )
        await self._db.commit()
        return bool(cursor.rowcount)

    async def mark_all_read(self) -> int:
        """Mark every unread notification as read and return count changed."""
        now = self._now()
        cursor = await self._db.execute(
            "UPDATE notifications SET status = 'read', read_at = ?, updated_at = ? WHERE status = 'unread'",
            (now, now),
        )
        await self._db.commit()
        return int(cursor.rowcount or 0)

    async def get(self, notification_id: int) -> dict[str, Any] | None:
        """Return one notification by id, or None."""
        cursor = await self._db.execute("SELECT * FROM notifications WHERE id = ?", (notification_id,))
        row = await cursor.fetchone()
        return self._row_to_dict(row) if row else None

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        data = dict(row)
        for key in ("actions_json", "metadata_json"):
            raw = data.pop(key, "[]" if key == "actions_json" else "{}")
            try:
                parsed = json.loads(raw or ("[]" if key == "actions_json" else "{}"))
            except Exception:
                parsed = [] if key == "actions_json" else {}
            data["actions" if key == "actions_json" else "metadata"] = parsed
        return data
