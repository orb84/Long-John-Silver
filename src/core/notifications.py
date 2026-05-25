"""
Notification service for LJS.

Dispatches notifications to configured channels (Discord, Telegram)
with a unified interface.
"""

from loguru import logger
from typing import Any
from src.core.models import NotificationMessage


class NotificationService:
    """Sends notifications through all configured bridges."""

    def __init__(self) -> None:
        self._bridges: list[Any] = []

    def register_bridge(self, bridge: Any) -> None:
        """Register a communication bridge (Discord, Telegram, etc.)."""
        self._bridges.append(bridge)
        logger.info(f"Registered notification bridge: {bridge.__class__.__name__}")

    async def notify(self, message: NotificationMessage) -> None:
        """Send a notification through all registered bridges."""
        for bridge in self._bridges:
            try:
                await bridge.send_notification(message)
            except Exception as e:
                logger.error(f"Failed to notify via {bridge.__class__.__name__}: {e}")

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
        ))

    async def send_error(self, error: str, context: str = "") -> None:
        """Notify about an error."""
        body = f"{context}: {error}" if context else error
        await self.notify(NotificationMessage(
            title="Error",
            body=body,
            level="error",
        ))

    async def send_message(self, text: str, title: str = "LJS", level: str = "info") -> None:
        """Send a general notification message through all bridges."""
        await self.notify(NotificationMessage(
            title=title,
            body=text,
            level=level,
        ))