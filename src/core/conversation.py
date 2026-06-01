"""
Conversation memory manager for LJS.

Manages per-session conversation history with automatic summarization
of older turns. Keeps recent messages in full and compresses older
context into a concise summary to maintain coherent conversations
without unbounded token growth.
"""

import re

from loguru import logger
from typing import Optional, TYPE_CHECKING
from src.core.database import Database
from src.core.vector_store import VectorStore
from src.utils.circuit_breaker import CircuitBreaker

if TYPE_CHECKING:
    from src.llm_providers.task_client import TaskLLMClient


class ConversationManager:
    """Manages conversation history per session with auto-summarization.

    Each session (keyed by web/Discord/Telegram channel) gets its own
    conversation history. When the turn count exceeds a threshold,
    older turns are compressed into a summary paragraph via LLM,
    keeping recent context full and detailed.
    """

    MAX_FULL_TURNS = 20
    MAX_TOTAL_TURNS = 40
    SUMMARY_TRIGGER = 30

    def __init__(
        self,
        db: Database,
        vector_store: Optional[VectorStore] = None,
        llm_config: Optional[dict] = None,
        llm_client: "TaskLLMClient | None" = None,
    ):
        """Initialize the conversation manager.

        Args:
            db: Database instance for persisting conversation turns.
            vector_store: Optional vector store for semantic context retrieval.
            llm_config: Optional dict with 'model' and 'api_base' keys for
                LLM calls (legacy, used if llm_client is None).
            llm_client: Optional TaskLLMClient for LLM-based summarization.
                Takes priority over llm_config if provided.
        """
        self._db = db
        self._vector_store = vector_store
        self._llm_config = llm_config or {}
        self._llm_client = llm_client
        self._breaker = CircuitBreaker("summarization", failure_threshold=3, recovery_seconds=60)

    def has_vector_store(self) -> bool:
        """Check whether a functional vector store is available."""
        return self._vector_store is not None and self._vector_store.is_initialized

    async def ensure_session(self, session_id: str, user_id: str | None = None, channel: str = "web") -> dict:
        """Ensure the session row exists before any conversation FK write.

        This is the single conversation-layer invariant used by web and future
        bridge callers.  Reconnected browsers may present a UUID-like session id
        that has never gone through login; upgraded databases can legitimately
        enforce a foreign key from conversation_history to sessions.  Creating a
        reserved local user/session here prevents chat from crashing before the
        agent can even call tools, while preserving referential integrity.
        """
        channel_user_id = str(session_id or "")
        if isinstance(session_id, str) and "_" in session_id:
            channel = session_id.split("_", 1)[0] or channel
        return await self._db.users.ensure_session(session_id, user_id=user_id, channel=channel, channel_user_id=channel_user_id)

    async def add_turn(self, session_id: str, role: str, content: str,
                       tool_call_id: str | None = None) -> int:
        """Add a conversation turn to the session history.

        Args:
            session_id: The session identifier.
            role: One of 'system', 'user', 'assistant', 'tool', 'summary'.
            content: The message content.
            tool_call_id: Optional tool call ID for tool result messages.

        Returns:
            The auto-incremented ID of the new turn.
        """
        await self.ensure_session(session_id)
        turn_id = await self._db.system.add_conversation_turn(
            session_id, role, content, tool_call_id
        )

        # Store embedding for semantic search if vector store is available
        if self._vector_store and self._vector_store.is_initialized and role in ("user", "assistant"):
            await self._vector_store.upsert(
                item_id=turn_id,
                text=content,
                metadata={"session_id": session_id, "role": role},
            )

        # Check if summarization is needed
        turn_count = await self._db.system.get_conversation_turn_count(session_id)
        if turn_count >= self.SUMMARY_TRIGGER:
            await self._summarize_older_turns(session_id)

        return turn_id

    async def get_context(self, session_id: str,
                          max_turns: int = 20,
                          max_tokens: int = 4000,
                          raw_recent_tokens: int | None = None,
                          compressed_history_tokens: int | None = None) -> list[dict]:
        """Build a compression-first context window for the LLM.

        Recent turns are preserved raw up to ``raw_recent_tokens``. Older turns
        are represented by a compressed system packet instead of being silently
        dropped.  This method may still use an already-persisted summary row
        created by ``_summarize_older_turns``; otherwise it performs a
        deterministic compression pass so context assembly never depends on an
        extra LLM call right before the main LLM call.

        Args:
            session_id: The session identifier.
            max_turns: Upper bound for raw recent full turns.
            max_tokens: Total history budget if explicit sub-budgets are absent.
            raw_recent_tokens: Reserved budget for uncompressed recent turns.
            compressed_history_tokens: Budget for compressed older history.

        Returns:
            List of message dicts with compressed older context followed by raw
            recent turns.
        """
        if max_tokens <= 0:
            return []

        await self.ensure_session(session_id)

        raw_budget = raw_recent_tokens
        compressed_budget = compressed_history_tokens
        if raw_budget is None or compressed_budget is None:
            raw_budget = max(0, int(max_tokens * 0.30))
            compressed_budget = max(0, int(max_tokens) - raw_budget)
        raw_budget = max(0, int(raw_budget or 0))
        compressed_budget = max(0, int(compressed_budget or 0))

        # Load enough history to compress older context. ``max_turns`` only
        # limits the raw recent slice, not the amount of history considered for
        # the compressed packet.
        history_limit = max(self.MAX_TOTAL_TURNS, int(max_turns or 0) * 4, 120)
        history = await self._db.system.get_conversation_history(session_id, limit=history_limit)
        if not history:
            return []

        raw_recent: list[dict] = []
        older: list[dict] = []
        raw_estimate = 0
        raw_count = 0

        for turn in reversed(history):
            msg = self._turn_to_message(turn)
            estimate = self._estimate_message_tokens(msg)
            role = turn.get("role", "user")
            # Summary rows are already compressed older context; keep them in
            # the older bucket so they are coalesced with other old history.
            can_be_raw = role != "summary"
            fits_raw_budget = raw_estimate + estimate <= raw_budget
            fits_turn_count = raw_count < max_turns if max_turns is not None else True
            if can_be_raw and fits_turn_count and (fits_raw_budget or not raw_recent):
                raw_recent.insert(0, msg)
                raw_estimate += estimate
                raw_count += 1
            else:
                older.insert(0, turn)

        messages: list[dict] = []
        if older and compressed_budget > 0:
            compressed = self._compress_turns_for_context(older, compressed_budget)
            if compressed:
                messages.append({
                    "role": "system",
                    "content": f"COMPRESSED PAST CONVERSATION CONTEXT:\n{compressed}",
                })

        messages.extend(raw_recent)
        return messages

    def _turn_to_message(self, turn: dict) -> dict:
        """Convert a stored conversation row into a chat message dict."""
        role = turn.get("role", "user")
        content = turn.get("content") or ""
        if role == "summary":
            return {
                "role": "system",
                "content": f"Previous conversation context: {content}",
            }
        msg = {"role": role, "content": content}
        if role == "assistant" and content.startswith("__TOOL_CALLS__:"):
            import json
            try:
                tool_calls_json = content[len("__TOOL_CALLS__:"):]
                msg["tool_calls"] = json.loads(tool_calls_json)
                msg["content"] = None
            except Exception as e:
                logger.warning(f"Failed to deserialize tool_calls: {e}")
        if turn.get("tool_call_id"):
            msg["tool_call_id"] = turn["tool_call_id"]
        return msg

    def _estimate_message_tokens(self, msg: dict) -> int:
        content = msg.get("content") or ""
        base = int(len(str(content)) / 4) + 4
        if msg.get("tool_calls"):
            base += int(len(str(msg.get("tool_calls"))) / 2)
        return max(1, base)

    def _compress_turns_for_context(self, turns: list[dict], max_tokens: int) -> str:
        """Deterministically compress stored turns to fit a token budget."""
        max_chars = max(600, int(max_tokens * 4))
        lines: list[str] = []
        for turn in turns:
            role = turn.get("role", "user")
            content = str(turn.get("content") or "").strip()
            if not content:
                continue
            if role == "summary":
                lines.append(f"- prior summary: {' '.join(content.split())}")
                continue
            content = " ".join(content.split())
            if len(content) > 1000:
                content = self._middle_compress_text(content, 1000)
            lines.append(f"- {role}: {content}")
        text = "\n".join(lines)
        if len(text) <= max_chars:
            return text
        return self._middle_compress_text(text, max_chars)

    @staticmethod
    def _middle_compress_text(text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        if max_chars < 200:
            return text[:max_chars]
        head = int(max_chars * 0.65)
        tail = max(80, max_chars - head - 96)
        omitted = max(0, len(text) - head - tail)
        return (
            text[:head].rstrip()
            + f"\n...[compressed earlier context: {omitted} chars represented]...\n"
            + text[-tail:].lstrip()
        )

    async def get_relevant_context(self, session_id: str, query: str,
                                    top_k: int = 3) -> list[dict]:
        """Find semantically similar past conversations using vector search.

        Useful for injecting relevant past context into the current
        conversation, even if it's from a different session or topic.

        Args:
            session_id: Current session ID (for potential filtering).
            query: The user's current message to find similar context for.
            top_k: Number of similar results to return.

        Returns:
            List of relevant conversation context dicts.
        """
        if not self._vector_store or not self._vector_store.is_initialized:
            return []

        results = await self._vector_store.search(query, top_k=top_k)
        contexts = []
        for result in results:
            meta = result.get("metadata") or {}
            if not meta:
                continue

            # Fetch the actual conversation turn from DB
            turn_id = result["id"]
            history = await self._db.system.get_conversation_history(
                meta.get("session_id", session_id), limit=200
            )
            # Find the turn near this ID
            for turn in history:
                if turn.get("id") == turn_id:
                    contexts.append({
                        "role": turn.get("role", "user"),
                        # Use 'or' instead of default to handle case where content key exists but is None
                        "content": turn.get("content") or "",
                        "similarity": max(0, 1.0 - (result.get("distance") if result.get("distance") is not None else 1.0)),
                    })
                    break

        return contexts

    async def clear_session(self, session_id: str) -> None:
        """Delete all conversation history for a session."""
        if self._vector_store and self._vector_store.is_initialized:
            history = await self._db.system.get_conversation_history(session_id, limit=10000)
            await self._vector_store.delete_many([t.get("id", 0) for t in history if t.get("id")])
        await self._db.system.delete_conversation_history(session_id)
        logger.info(f"Cleared conversation history for session {session_id}")

    async def _summarize_older_turns(self, session_id: str) -> None:
        """Compress older conversation turns into a summary paragraph.

        Keeps the most recent MAX_FULL_TURNS turns in full and replaces
        the rest with a single summary message. This prevents unbounded
        context growth while preserving key information.
        """
        all_turns = await self._db.system.get_conversation_history(
            session_id, limit=200
        )

        if len(all_turns) <= self.MAX_FULL_TURNS:
            return

        # Separate older turns from recent ones
        older = all_turns[:-self.MAX_FULL_TURNS]
        recent = all_turns[-self.MAX_FULL_TURNS:]

        # Find the minimum ID of the recent turns (the boundary)
        cutoff_id = min(t.get("id", 0) for t in recent)

        # Build a text representation of older turns for summarization
        older_text = "\n".join(
            f"{t.get('role', 'user')}: {t.get('content', '')}" for t in older
        )

        summary = await self._generate_summary(older_text)

        # Delete vector rows before removing old turns so semantic memory does not
        # point at vanished conversation rows after summarization.
        if self._vector_store and self._vector_store.is_initialized:
            await self._vector_store.delete_many([t.get("id", 0) for t in older if t.get("id")])

        # Delete older turns using the Database method (no raw SQL)
        deleted = await self._db.system.delete_conversation_turns_before(
            session_id, cutoff_id
        )
        logger.debug(f"Deleted {deleted} older turns for session {session_id}")

        # Insert summary as a system message
        await self._db.system.add_conversation_turn(
            session_id, "summary", summary
        )

        logger.info(
            f"Summarized {len(older)} older turns for session {session_id}"
        )

    def _sanitize_summary_against_source(self, summary: str, source_text: str) -> str:
        """Remove LLM-summary sentences that introduced unsupported dates.

        Conversation summaries are later injected as trusted context.  A compact
        summarizer that invents an episode air date can poison future turns, so
        summary dates must be a subset of dates present in the source text.
        """
        if not summary:
            return summary
        source_dates = self._date_fingerprints(source_text)
        summary_dates = self._date_fingerprints(summary)
        unsupported = summary_dates - source_dates
        if not unsupported:
            return summary

        pieces = re.split(r"(?<=[.!?])\s+|\n+", summary)
        kept = [piece for piece in pieces if not (self._date_fingerprints(piece) & unsupported)]
        cleaned = " ".join(piece.strip() for piece in kept if piece.strip())
        if cleaned:
            cleaned += " [One unsupported date detail was omitted from the compressed summary.]"
            return cleaned
        return "Previous conversation involved media/library work; one unsupported date detail was omitted from the compressed summary."

    @classmethod
    def _date_fingerprints(cls, text: str) -> set[str]:
        """Return canonical date fingerprints from common English/Italian text."""
        if not text:
            return set()
        out: set[str] = set()
        month_names = {
            "jan": 1, "january": 1, "gen": 1, "gennaio": 1,
            "feb": 2, "february": 2, "febbraio": 2,
            "mar": 3, "march": 3, "marzo": 3,
            "apr": 4, "april": 4, "aprile": 4,
            "may": 5, "mag": 5, "maggio": 5,
            "jun": 6, "june": 6, "giu": 6, "giugno": 6,
            "jul": 7, "july": 7, "lug": 7, "luglio": 7,
            "aug": 8, "august": 8, "ago": 8, "agosto": 8,
            "sep": 9, "sept": 9, "september": 9, "set": 9, "settembre": 9,
            "oct": 10, "october": 10, "ott": 10, "ottobre": 10,
            "nov": 11, "november": 11, "novembre": 11,
            "dec": 12, "december": 12, "dic": 12, "dicembre": 12,
        }
        for y, m, d in re.findall(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", text):
            out.add(f"{int(y):04d}-{int(m):02d}-{int(d):02d}")
        month_alt = "|".join(sorted(month_names, key=len, reverse=True))
        for month, day, year in re.findall(rf"\b({month_alt})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?[,]?\s+(20\d{{2}})\b", text, flags=re.IGNORECASE):
            out.add(f"{int(year):04d}-{month_names[month.lower()]:02d}-{int(day):02d}")
        for day, month, year in re.findall(rf"\b(\d{{1,2}})\s+({month_alt})\.?[,]?\s+(20\d{{2}})\b", text, flags=re.IGNORECASE):
            out.add(f"{int(year):04d}-{month_names[month.lower()]:02d}-{int(day):02d}")
        return out

    async def _generate_summary(self, conversation_text: str) -> str:
        """Generate a concise summary of conversation text using LLM.

        Falls back to a truncation-based summary if the LLM call fails.
        Uses TaskLLMClient if available, otherwise legacy llm_config.

        Args:
            conversation_text: The conversation text to summarize.

        Returns:
            A concise summary string.
        """
        prompt = (
            "Summarize the following conversation in 2-3 short sentences, "
            "preserving key facts, preferences, and decisions. Do not add dates, "
            "episode numbers, titles, download statuses, or factual media claims "
            "that are not explicitly present in the text. If a media date/status is "
            "uncertain or tool-supported evidence is missing, mark it as unverified "
            "rather than guessing:\n\n"
            f"{conversation_text[:8000]}\n\nSummary:"
        )

        try:
            if self._llm_client:
                # New production path — TaskLLMClient
                response = await self._breaker.call(
                    self._llm_client.completion,
                    task="summarization",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=200,
                    temperature=0.3,
                )
                return self._sanitize_summary_against_source(
                    response.choices[0].message.content.strip(),
                    conversation_text,
                )

            # Legacy path — direct litellm call
            model = self._llm_config.get("model", "gpt-3.5-turbo")
            api_base = self._llm_config.get("api_base")
            api_key = self._llm_config.get("api_key")

            import litellm
            kwargs = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 200,
                "temperature": 0.3,
            }
            if api_base:
                kwargs["api_base"] = api_base
            if api_key:
                kwargs["api_key"] = api_key

            response = await self._breaker.call(litellm.acompletion, **kwargs)
            return self._sanitize_summary_against_source(
                response.choices[0].message.content.strip(),
                conversation_text,
            )
        except Exception as e:
            logger.warning(f"Summary generation failed: {e}. Using truncation fallback.")
            # Fallback: use last 500 characters of older context
            return f"[Previous conversation context]: {conversation_text[-500:]}"