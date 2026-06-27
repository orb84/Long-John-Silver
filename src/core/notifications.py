"""
Notification service for LJS.

Notifications are persisted first so users without Discord/Telegram/WhatsApp
still get a web inbox. Bridges are delivery channels, not the source of truth.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import datetime, timezone

from loguru import logger
from typing import Any

from src.core.models import NotificationMessage


class NotificationService:
    """Persist notifications and fan them out through configured bridges."""

    def __init__(self, db: Any | None = None) -> None:
        self._bridges: dict[str, Any] = {}
        self._db = db
        self._event_bus: Any | None = None
        self._started_at = datetime.now(timezone.utc)

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

    @staticmethod
    def _delivery_target_id(bridge_id: str, bridge: Any) -> str:
        """Return the persisted delivery target for a bridge instance.

        A bridge id alone is not enough for the delivery ledger: Discord channel
        A and Discord channel B are different notification destinations.  Bridge
        adapters may expose ``delivery_id`` as either a property or method.  If
        they do not, we fall back to the registry id for compatibility.
        """
        raw = getattr(bridge, "delivery_id", None)
        try:
            value = raw() if callable(raw) else raw
        except Exception:
            value = None
        value = str(value or "").strip()
        return value or str(bridge_id)

    async def _deliver_to_bridge(
        self,
        *,
        notification_id: int | None,
        bridge_id: str,
        bridge: Any,
        message: NotificationMessage,
    ) -> bool:
        """Deliver one persisted notification to one bridge at most once.

        The web inbox and external channels have different lifecycles.  A
        notification can remain unread in the web UI for days, but Discord must
        not receive the same "Download Complete" message on every app restart.
        Delivery success is therefore recorded per notification and per bridge
        target.  Failures are retryable; successful deliveries are terminal.
        """
        repo = getattr(self._db, "notifications", None) if self._db else None
        delivery_id = self._delivery_target_id(bridge_id, bridge)
        if repo and notification_id:
            try:
                if not await repo.should_deliver_to_bridge(notification_id, delivery_id):
                    logger.debug(
                        "Notification bridge delivery skipped: id={} target={} already delivered",
                        notification_id,
                        delivery_id,
                    )
                    return False
                await repo.record_bridge_delivery_attempt(notification_id, delivery_id)
            except Exception as exc:
                # Do not block live notifications if the ledger is temporarily
                # unavailable, but log loudly enough to diagnose duplicate risk.
                logger.warning(f"Notification delivery ledger unavailable for {delivery_id}: {exc}")
        try:
            delivered = await bridge.send_notification(message)
        except Exception as exc:
            if repo and notification_id:
                try:
                    await repo.record_bridge_delivery_failure(notification_id, delivery_id, str(exc))
                except Exception:
                    pass
            logger.error(f"Failed to notify via {delivery_id}: {exc}")
            return False
        if delivered is False:
            if repo and notification_id:
                try:
                    await repo.record_bridge_delivery_failure(
                        notification_id,
                        delivery_id,
                        "bridge reported no configured/available delivery target",
                    )
                except Exception:
                    pass
            logger.debug(f"Notification bridge delivery skipped by bridge target: id={notification_id} target={delivery_id}")
            return False
        if repo and notification_id:
            try:
                await repo.record_bridge_delivery_success(notification_id, delivery_id)
            except Exception as exc:
                logger.warning(f"Notification delivery success could not be recorded for {delivery_id}: {exc}")
        return True

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
            if not self._created_after_service_start(row.get("created_at")):
                # Round 208 introduced per-bridge delivery state.  Pre-ledger
                # unread notifications must not be replayed once more merely
                # because the app restarted; they remain visible in the web
                # inbox, but bridge replay is only for events missed during the
                # current process lifetime.
                continue
            try:
                notification_id = int(row.get("id") or 0)
                msg = NotificationMessage(
                    title=str(row.get("title") or "Notification"),
                    body=self._bridge_body(str(row.get("body") or ""), row.get("actions") or []),
                    level=str(row.get("level") or "info"),
                )
                await self._deliver_to_bridge(
                    notification_id=notification_id,
                    bridge_id=bridge_id,
                    bridge=bridge,
                    message=msg,
                )
            except Exception as exc:
                logger.debug(f"Notification replay to {bridge_id} failed: {exc}")
                return

    def _created_after_service_start(self, created_at: Any) -> bool:
        """Return whether a notification was created after this service booted."""
        raw = str(created_at or "").strip()
        if not raw:
            return False
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            # SQLite's datetime('now') format is naive UTC: YYYY-MM-DD HH:MM:SS.
            try:
                parsed = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            except ValueError:
                return False
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed >= self._started_at

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
        if bridge:
            bridge_message = NotificationMessage(
                title=message.title,
                body=self._bridge_body(message.body, actions),
                level=message.level,
            )
            for bridge_id, bridge_obj in list(self._bridges.items()):
                await self._deliver_to_bridge(
                    notification_id=notification_id,
                    bridge_id=bridge_id,
                    bridge=bridge_obj,
                    message=bridge_message,
                )
        return notification_id

    @staticmethod
    def _stable_token(value: Any) -> str:
        text = str(value or "").strip().lower()
        text = re.sub(r"[^a-z0-9._:-]+", "_", text)
        return text.strip("_") or "unknown"

    @classmethod
    def _download_complete_dedupe_key(
        cls,
        *,
        item_name: str,
        season: int | None = None,
        episode: int | None = None,
        download_id: str = "",
        category_id: str = "",
    ) -> str:
        """Build a stable event key for one completed download notification."""
        if download_id:
            return f"download_complete:download:{cls._stable_token(download_id)}"
        scope = f"{category_id or 'media'}:{item_name}:s{season or ''}:e{episode or ''}"
        digest = hashlib.sha1(scope.encode("utf-8", errors="ignore")).hexdigest()[:16]
        return f"download_complete:item:{digest}"

    async def send_download_complete(self, item_name: str, season: int | None = None,
                                     episode: int | None = None, *,
                                     download_id: str = "",
                                     category_id: str = "",
                                     unit_label: str = "") -> None:
        """Notify about a completed download using category-owned unit labels."""
        label = str(unit_label or "").strip()
        if label:
            body = f"Download complete: {item_name} {label}"
        elif season is not None and episode is not None:
            # Compatibility for legacy rows that predate category-owned unit
            # descriptors. New rows should pass ``unit_label`` instead of
            # relying on conventional structured coordinates.
            body = f"Download complete: {item_name} S{season:02d}E{episode:02d}"
        else:
            body = f"Download complete: {item_name}"
        resolved_category = category_id or "media"
        dedupe_key = self._download_complete_dedupe_key(
            item_name=item_name,
            season=season,
            episode=episode,
            download_id=download_id,
            category_id=resolved_category,
        )

        await self.notify(NotificationMessage(
            title="Download Complete",
            body=body,
            level="success",
        ), event_type="download_complete", category_id=resolved_category, item_id=item_name, dedupe_key=dedupe_key)

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
