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
                           event_type = ?, actions_json = ?, metadata_json = ?, updated_at = ?
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

    async def should_deliver_to_bridge(self, notification_id: int, bridge_id: str) -> bool:
        """Return whether a bridge should receive this notification.

        The notification row is the durable web-inbox event; delivery to each
        external bridge is tracked separately.  This prevents app startup from
        replaying every unread web notification to Discord/Telegram/WhatsApp on
        every launch while still allowing retries for bridges that were offline
        or failed previously.
        """
        cursor = await self._db.execute(
            """SELECT status FROM notification_deliveries
               WHERE notification_id = ? AND bridge_id = ?""",
            (int(notification_id), str(bridge_id)),
        )
        row = await cursor.fetchone()
        return not row or str(row["status"] or "") != "delivered"

    async def record_bridge_delivery_attempt(self, notification_id: int, bridge_id: str) -> None:
        """Create/update the bridge-delivery row before attempting send."""
        now = self._now()
        await self._db.execute(
            """INSERT INTO notification_deliveries
               (notification_id, bridge_id, status, attempts, created_at, updated_at)
               VALUES (?, ?, 'pending', 1, ?, ?)
               ON CONFLICT(notification_id, bridge_id) DO UPDATE SET
                   status = CASE
                       WHEN notification_deliveries.status = 'delivered' THEN notification_deliveries.status
                       ELSE 'pending'
                   END,
                   attempts = CASE
                       WHEN notification_deliveries.status = 'delivered' THEN notification_deliveries.attempts
                       ELSE notification_deliveries.attempts + 1
                   END,
                   updated_at = CASE
                       WHEN notification_deliveries.status = 'delivered' THEN notification_deliveries.updated_at
                       ELSE excluded.updated_at
                   END""",
            (int(notification_id), str(bridge_id), now, now),
        )
        await self._db.commit()

    async def record_bridge_delivery_success(self, notification_id: int, bridge_id: str) -> None:
        """Mark a notification as delivered to one external bridge target."""
        now = self._now()
        await self._db.execute(
            """INSERT INTO notification_deliveries
               (notification_id, bridge_id, status, attempts, delivered_at, last_error, created_at, updated_at)
               VALUES (?, ?, 'delivered', 1, ?, '', ?, ?)
               ON CONFLICT(notification_id, bridge_id) DO UPDATE SET
                   status = 'delivered',
                   delivered_at = excluded.delivered_at,
                   last_error = '',
                   updated_at = excluded.updated_at""",
            (int(notification_id), str(bridge_id), now, now, now),
        )
        await self._db.commit()

    async def record_bridge_delivery_failure(self, notification_id: int, bridge_id: str, error: str) -> None:
        """Record a failed bridge delivery without suppressing future retry."""
        now = self._now()
        await self._db.execute(
            """INSERT INTO notification_deliveries
               (notification_id, bridge_id, status, attempts, last_error, created_at, updated_at)
               VALUES (?, ?, 'failed', 1, ?, ?, ?)
               ON CONFLICT(notification_id, bridge_id) DO UPDATE SET
                   status = CASE
                       WHEN notification_deliveries.status = 'delivered' THEN notification_deliveries.status
                       ELSE 'failed'
                   END,
                   last_error = CASE
                       WHEN notification_deliveries.status = 'delivered' THEN notification_deliveries.last_error
                       ELSE excluded.last_error
                   END,
                   updated_at = CASE
                       WHEN notification_deliveries.status = 'delivered' THEN notification_deliveries.updated_at
                       ELSE excluded.updated_at
                   END""",
            (int(notification_id), str(bridge_id), str(error or "")[:1000], now, now),
        )
        await self._db.commit()

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
