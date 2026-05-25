"""
WebSocket connection manager for LJS.

Tracks active WebSocket connections and provides a broadcast primitive
for pushing real-time events to all connected clients.
"""

from fastapi import WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder


class ConnectionManager:
    """Manages WebSocket connections for real-time updates."""

    def __init__(self) -> None:
        self.active: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        """Accept and register a new WebSocket connection."""
        await websocket.accept()
        self.active.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection from the active list."""
        if websocket in self.active:
            self.active.remove(websocket)

    async def broadcast(self, message: dict) -> None:
        """Send a JSON-serialisable message to every connected client.

        Dead connections are removed silently.
        """
        dead: list[WebSocket] = []
        serializable_msg = jsonable_encoder(message)
        for ws in self.active:
            try:
                await ws.send_json(serializable_msg)
            except (WebSocketDisconnect, ConnectionError, RuntimeError):
                dead.append(ws)
        for ws in dead:
            try:
                self.active.remove(ws)
            except ValueError:
                pass

    @property
    def connected_count(self) -> int:
        """Number of currently connected WebSocket clients."""
        return len(self.active)
