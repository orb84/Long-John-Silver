"""Authoritative browser chat-turn state broadcasting."""

from __future__ import annotations

from typing import Any


class ChatTurnStateBroadcaster:
    """Publish session-scoped chat state over the shared event channel."""

    def __init__(self, event_bus: Any) -> None:
        self._event_bus = event_bus

    def publish(
        self,
        *,
        state: str,
        session_id: str | None,
        turn_id: str | None,
        message: str = "",
    ) -> None:
        """Broadcast one normalized state update for reconnect reconciliation."""
        normalized = state if state in {"working", "stopping", "failed", "cancelled", "idle"} else "idle"
        self._event_bus.emit("chat_turn_state", {
            "state": normalized,
            "session_id": str(session_id or ""),
            "turn_id": str(turn_id or ""),
            "message": str(message or "")[:500],
        })
