"""
Event bus for LJS.

ShipEventBus is the central hub that pushes all system events (download
stats, lifecycle changes, system notifications) to connected WebSocket
clients through the ConnectionManager.
"""

import asyncio

from fastapi import WebSocket

from src.core.task_supervisor import TaskSupervisor
from src.web.websocket_manager import ConnectionManager


class ShipEventBus:
    """Central event bus that pushes all system events to connected WebSocket clients."""

    def __init__(self, supervisor: TaskSupervisor | None = None) -> None:
        self._manager = ConnectionManager()
        self._supervisor = supervisor

    async def connect(self, websocket: WebSocket) -> None:
        """Accept a WebSocket connection into the event bus."""
        await self._manager.connect(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection from the event bus."""
        self._manager.disconnect(websocket)

    def emit(self, event_type: str, data: dict) -> None:
        """Broadcast an event to all connected clients via supervisor."""
        payload = {"type": event_type, **data}
        if self._supervisor:
            self._supervisor.spawn_one_shot(
                f"broadcast_{event_type}_{hash(str(data)) % 1000000}",
                self._manager.broadcast(payload),
            )
        else:
            asyncio.create_task(self._manager.broadcast(payload))

    def emit_dl_stats(self, download_id: str, stats: dict) -> None:
        """Broadcast live download progress stats."""
        self.emit("dl_stats", {"id": download_id, "stats": stats})

    def emit_dl_event(self, event_type: str, download_id: str, data: dict | None = None) -> None:
        """Broadcast download lifecycle events (status_changed, added, removed)."""
        payload = {"subtype": event_type, "id": download_id}
        if data:
            payload.update(data)
        self.emit("dl_event", payload)

    def emit_system(self, subevent: str, data: dict | None = None) -> None:
        """Broadcast system-level events (category_item_added, category_item_removed, etc.)."""
        payload = {"subtype": subevent}
        if data:
            payload.update(data)
        self.emit("system", payload)
