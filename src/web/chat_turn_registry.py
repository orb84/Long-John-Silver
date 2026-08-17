"""Compatibility import for the transport-neutral chat turn registry.

The canonical implementation lives in ``src.ai.chat_turn_registry`` because
turn ownership is shared agent-execution state, not browser-specific state.
"""

from src.ai.chat_turn_registry import ActiveChatTurn, ChatTurnRegistry

__all__ = ["ActiveChatTurn", "ChatTurnRegistry"]
