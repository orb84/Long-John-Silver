"""
Notification service for LJS.

Notifications are persisted first so users without Discord/Telegram/WhatsApp
still get a web inbox. Bridges are delivery channels, not the source of truth.
"""

from __future__ import annotations

import asyncio

from loguru import logger
from typing import Any

from src.core.models import NotificationMessage


class NotificationService:
    """Persist notifications and fan them out through configured bridges."""

    def __init__(self, db: Any | None = None) -> None:
        self._bridges: dict[str, Any] = {}
        self._db = db
        self._event_bus: Any | None = None

    def set_database(self, db: Any | None) -> None:
        """Attach the initialized database facade after construction."""
        self._db = db

    def set_event_bus(self, event_bus: Any | None) -> None:
        """Attach the web event bus used for live inbox updates."""
        self._event_bus = event_bus

    def register_bridge(self, bridge: Any, bridge_id: str | None = None) -> None:
        """Register or replace a communication bridge (Discord, Telegram, etc.).

        Bridge registration is idempotent because Settings can restart bridges
        while the app is running.  Replacing by bridge id prevents stale stopped
        bridge objects from receiving future notification fanout.
        """
        key = str(bridge_id or getattr(bridge, "bridge_id", "") or getattr(bridge, "name", "") or bridge.__class__.__name__)
        self._bridges[key] = bridge
        logger.info(f"Registered notification bridge: {key} ({bridge.__class__.__name__})")
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._replay_recent_unread_to_bridge(key, bridge))
        except RuntimeError:
            # register_bridge can be called by tests outside an event loop.
            pass

    def unregister_bridge(self, bridge_id: str) -> None:
        """Remove a bridge from fanout when a comms adapter stops."""
        self._bridges.pop(str(bridge_id), None)

    async def _replay_recent_unread_to_bridge(self, bridge_id: str, bridge: Any, *, limit: int = 20) -> None:
        """Best-effort replay of unread notifications missed while a bridge started."""
        repo = getattr(self._db, "notifications", None) if self._db else None
        if not repo:
            return
        try:
            rows = await repo.list(status="unread", limit=limit)
        except Exception as exc:
            logger.debug(f"Notification replay skipped for {bridge_id}: {exc}")
            return
        for row in reversed(rows):
            # Stop replaying if this bridge was replaced while the task was running.
            if self._bridges.get(bridge_id) is not bridge:
                return
            try:
                msg = NotificationMessage(
                    title=str(row.get("title") or "Notification"),
                    body=self._bridge_body(str(row.get("body") or ""), row.get("actions") or []),
                    level=str(row.get("level") or "info"),
                )
                await bridge.send_notification(msg)
            except Exception as exc:
                logger.debug(f"Notification replay to {bridge_id} failed: {exc}")
                return

    @staticmethod
    def _bridge_body(body: str, actions: list[dict[str, Any]] | None = None) -> str:
        """Render a bridge-safe notification body.

        The durable web inbox owns executable actions. External bridges get a
        clear pointer to the inbox instead of pretending that plain text messages
        are interactive approvals.
        """
        actions = actions or []
        labels = [str(action.get("label") or action.get("title") or action.get("key") or "").strip() for action in actions]
        labels = [label for label in labels if label]
        if labels:
            suffix = "Actions available in the LJS web inbox: " + ", ".join(labels[:4])
            return f"{body}\n\n{suffix}" if body else suffix
        return body

    async def notify(
        self,
        message: NotificationMessage,
        *,
        category_id: str = "",
        item_id: str = "",
        event_type: str = "general",
        actions: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
        dedupe_key: str = "",
        bridge: bool = True,
    ) -> int | None:
        """Persist a notification and optionally send it through bridges."""
        notification_id: int | None = None
        repo = getattr(self._db, "notifications", None) if self._db else None
        if repo:
            try:
                created_result = await repo.create(
                    title=message.title,
                    body=message.body,
                    level=message.level,
                    category_id=category_id,
                    item_id=item_id,
                    event_type=event_type,
                    actions=actions,
                    metadata=metadata,
                    dedupe_key=dedupe_key,
                    return_status=True,
                )
                if isinstance(created_result, tuple):
                    notification_id, inserted = created_result
                else:
                    notification_id, inserted = int(created_result or 0), True
                if self._event_bus:
                    try:
                        unread = await repo.unread_count()
                        self._event_bus.emit_system("notifications_updated", {"id": notification_id, "unread": unread})
                    except Exception:
                        pass
            except Exception as exc:
                logger.warning(f"Failed to persist notification '{message.title}': {exc}")
        # Deduplicated RSS/provider events should update the durable inbox without
        # spamming bridges on every poll.  New rows are replayed to bridges that
        # register a little later, so this remains safe during startup races.
        if bridge and (not dedupe_key or locals().get("inserted", True)):
            bridge_message = NotificationMessage(
                title=message.title,
                body=self._bridge_body(message.body, actions),
                level=message.level,
            )
            for bridge_id, bridge_obj in list(self._bridges.items()):
                try:
                    await bridge_obj.send_notification(bridge_message)
                except Exception as e:
                    logger.error(f"Failed to notify via {bridge_id}: {e}")
        return notification_id

    async def send_download_complete(self, item_name: str, season: int | None = None,
                                     episode: int | None = None) -> None:
        """Notify about a completed download."""
        if season is not None and episode is not None:
            body = f"Download complete: {item_name} S{season:02d}E{episode:02d}"
        else:
            body = f"Download complete: {item_name}"

        await self.notify(NotificationMessage(
            title="Download Complete",
            body=body,
            level="success",
        ), event_type="download_complete", category_id="tv" if season is not None else "", item_id=item_name)

    async def send_error(self, error: str, context: str = "") -> None:
        """Notify about an error."""
        body = f"{context}: {error}" if context else error
        await self.notify(NotificationMessage(
            title="Error",
            body=body,
            level="error",
        ), event_type="error")

    async def send_message(self, text: str, title: str = "LJS", level: str = "info") -> None:
        """Send a general notification message through all bridges."""
        await self.notify(NotificationMessage(
            title=title,
            body=text,
            level=level,
        ))
