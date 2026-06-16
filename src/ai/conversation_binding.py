"""
Conversation binding for LJS.

Builds conversation context messages for the agent by loading
recent history and semantically relevant past context from
the ConversationManager.
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

from src.core.conversation import ConversationManager


class ConversationBinding:
    """Builds conversation context messages for the agent.

    Loads recent conversation history from ConversationManager and
    optionally injects semantically relevant context from past
    conversations using the vector store.
    """

    def __init__(self, conversation_manager: Optional[ConversationManager] = None) -> None:
        """Initialize the binding with an optional conversation manager.

        Args:
            conversation_manager: Manager for conversation history and vector search.
        """
        self._conversation = conversation_manager

    async def build_context_messages(
        self,
        session_id: str | None,
        user_id: str | None,
        user_prompt: str | None = None,
        max_turns: int | None = None,
        max_tokens: int | None = None,
        raw_recent_tokens: int | None = None,
        compressed_history_tokens: int | None = None,
        fresh_download_request: bool = False,
    ) -> list[dict]:
        """Build conversation context messages from memory.

        If a ConversationManager is available, loads recent history plus
        any semantically relevant context. Otherwise returns an empty list.

        Args:
            session_id: The session identifier for conversation memory.
            user_id: The user ID for per-user behavioral context (reserved).
            user_prompt: The current user message, used for semantic search.
            max_turns: Optional maximum recent full turns to include.
            max_tokens: Optional total history budget.
            raw_recent_tokens: Reserved token budget for uncompressed recent turns.
            compressed_history_tokens: Token budget for compressed older history.

        Returns:
            List of message dicts to prepend before the current exchange.
        """
        if not session_id or not self._conversation:
            return []
        if max_turns == 0 or max_tokens == 0:
            return []

        context_messages = await self._conversation.get_context(
            session_id,
            max_turns=self._conversation.MAX_FULL_TURNS if max_turns is None else max_turns,
            max_tokens=4000 if max_tokens is None else max_tokens,
            raw_recent_tokens=raw_recent_tokens,
            compressed_history_tokens=compressed_history_tokens,
        )

        if fresh_download_request:
            original_count = len(context_messages)
            context_messages = self._fresh_request_recent_tail(context_messages)
            dropped = max(0, original_count - len(context_messages))
            if dropped:
                logger.info(
                    "ConversationBinding: suppressed {} older context message(s) for guarded DOWNLOAD request and kept {} latest message(s) session_id={}",
                    dropped,
                    len(context_messages),
                    session_id,
                )
            return context_messages

        if user_prompt and max_tokens != 0 and self._conversation.has_vector_store():
            relevant = await self._conversation.get_relevant_context(
                session_id, user_prompt, top_k=3,
            )
            if relevant:
                context_parts = []
                for ctx in relevant:
                    similarity = ctx.get("similarity", 0)
                    if similarity >= 0.5:
                        role = ctx.get("role", "user")
                        content = str(ctx.get("content", ""))
                        if len(content) > 1200:
                            content = content[:800].rstrip() + "\n...[relevant context compressed]...\n" + content[-300:].lstrip()
                        context_parts.append(f"[{role}]: {content}")
                if context_parts:
                    relevant_text = "\n".join(context_parts)
                    # Semantic recalls are useful but optional; keep them compact
                    # so they do not consume the raw recent-turn reserve.
                    if len(relevant_text) > 3000:
                        relevant_text = relevant_text[:2000].rstrip() + "\n...[relevant context compressed]...\n" + relevant_text[-700:].lstrip()
                    context_messages.insert(0, {
                        "role": "system",
                        "content": f"COMPRESSED RELEVANT PAST CONTEXT:\n{relevant_text}",
                    })

        return context_messages

    @staticmethod
    def _fresh_request_recent_tail(context_messages: list[dict]) -> list[dict]:
        """Keep immediate history for guarded download requests.

        Older transcript and semantic recalls can drag stale candidates into a
        genuinely fresh search. Dropping the whole transcript is worse: the
        model loses state-changing facts from the immediately previous turns,
        such as a queued or cancelled download it must acknowledge.
        """
        if not context_messages:
            return []
        recent = []
        for message in context_messages[-8:]:
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            content = str(message.get("content") or "")
            if role == "system" and content.startswith("COMPRESSED RELEVANT PAST CONTEXT"):
                continue
            recent.append(message)
        return recent[-6:]



    async def build_intent_routing_context(
        self,
        session_id: str | None,
        *,
        pending_action_context: str | None = None,
        max_turns: int = 8,
        max_tokens: int = 1800,
        max_chars: int = 4200,
    ) -> str:
        """Build compact context specifically for intent routing.

        Intent routing must understand short semantic follow-ups such as
        "I meant released movie" without re-injecting the whole library, tool
        traces, or category packets.  This packet keeps only recent human and
        assistant turns plus pending machine-readable action handles.
        """
        parts: list[str] = []
        if pending_action_context:
            parts.append(pending_action_context.strip())

        if not session_id or not self._conversation:
            return "\n\n".join(part for part in parts if part)[:max_chars]

        try:
            messages = await self._conversation.get_context(
                session_id,
                max_turns=max_turns,
                max_tokens=max_tokens,
                raw_recent_tokens=int(max_tokens * 0.85),
                compressed_history_tokens=int(max_tokens * 0.15),
            )
        except Exception:
            messages = []

        lines: list[str] = []
        for msg in messages:
            role = str(msg.get("role") or "").lower()
            if role not in {"user", "assistant", "system"}:
                continue
            content = msg.get("content")
            if not content or str(content).startswith("__TOOL_CALLS__:"):
                continue
            text = " ".join(str(content).split())
            if not text:
                continue
            if len(text) > 700:
                text = text[:500].rstrip() + " … " + text[-140:].lstrip()
            label = "context" if role == "system" else role
            lines.append(f"[{label}] {text}")

        if lines:
            parts.append(
                "RECENT CONVERSATION CONTEXT FOR INTENT ROUTING:\n"
                "Use this only to understand semantic follow-ups, corrections, and refinements.\n"
                + "\n".join(lines[-max_turns:])
            )

        return "\n\n".join(part for part in parts if part)[:max_chars]

    async def record_turn(
        self,
        session_id: str | None,
        role: str,
        content: str,
        tool_call_id: str | None = None,
    ) -> None:
        """Record a conversation turn if a conversation manager is available.

        Args:
            session_id: The session identifier.
            role: The role ('user', 'assistant', 'tool', etc.).
            content: The message content.
            tool_call_id: Optional tool call ID.
        """
        if session_id and self._conversation:
            await self._conversation.add_turn(session_id, role, content, tool_call_id=tool_call_id)

    async def record_message(
        self,
        session_id: str | None,
        message: dict,
    ) -> None:
        """Record a conversation message including roles and tool data.

        Handles serialization of tool_calls for assistant messages.
        """
        if not session_id or not self._conversation:
            return

        role = message.get("role", "user")
        content = message.get("content") or ""
        tool_call_id = message.get("tool_call_id")

        if role == "assistant" and "tool_calls" in message:
            content = self._serialize_tool_calls(message["tool_calls"])

        await self._conversation.add_turn(session_id, role, content, tool_call_id=tool_call_id)

    def _serialize_tool_calls(self, tool_calls: list) -> str:
        """Serialize list of tool calls to custom string prefix format."""
        import json
        serialized_tcs = []
        for tc in tool_calls:
            if hasattr(tc, "model_dump"):
                serialized_tcs.append(tc.model_dump())
            elif isinstance(tc, dict):
                serialized_tcs.append(tc)
            else:
                serialized_tcs.append({
                    "id": getattr(tc, "id", None),
                    "type": getattr(tc, "type", "function"),
                    "function": {
                        "name": getattr(getattr(tc, "function", None), "name", None),
                        "arguments": getattr(getattr(tc, "function", None), "arguments", None),
                    }
                })
        return f"__TOOL_CALLS__:{json.dumps(serialized_tcs)}"
