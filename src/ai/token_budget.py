"""
Token budget manager for LJS.

The normal policy is **compress-first**, not drop-first.  The model's
context window includes the response, so every call first reserves output
space, then assembles a prompt from:

- irreducible system/current-turn context;
- a raw recent-history reserve;
- compressed older history / bulky tool payloads.

Dropping content is permitted only as a final safety fallback when even the
compressed prompt cannot fit the selected model cap.
"""

from __future__ import annotations

import json
from loguru import logger
from typing import Any


# Conservative token estimation: 1 token ≈ 4 characters for normal prose,
# 1 token ≈ 2 characters for JSON/tool payloads.
CHARS_PER_TOKEN_NORMAL = 4.0
CHARS_PER_TOKEN_COMPACT = 2.0

# Safety margin multiplier applied to estimates to avoid undercounting.
TOKEN_SAFETY_MARGIN = 1.3

from src.llm_providers.context_limits import FALLBACK_CONTEXT_LIMIT
from src.ai.tool_result_compactor import ToolResultCompactor

# Default context limit when no model info is available.
DEFAULT_CONTEXT_LIMIT = FALLBACK_CONTEXT_LIMIT

# Default output tokens reserved for the model's response.
DEFAULT_OUTPUT_RESERVE = 1024

# Maximum characters for tool result compaction.
DEFAULT_MAX_TOOL_RESULT_CHARS = 6000

# Maximum characters for web page content in tool results.
MAX_WEB_PAGE_CHARS = 4000

# Maximum number of torrent results to include in LLM prompts.
MAX_TORRENT_RESULTS = 10

# Default share of prompt budget reserved for uncompressed recent turns.
DEFAULT_RAW_RECENT_CONTEXT_PERCENT = 30


class TokenBudgetManager:
    """Estimates and compresses messages before LLM calls.

    The manager is intentionally conservative and ordering-safe.  It avoids
    deleting history as normal behavior: older/unprotected history is replaced
    by a compact summary system message, tool payloads are compacted, and only
    an impossible over-budget prompt triggers last-resort dropping.
    """

    def __init__(self, default_context_limit: int = DEFAULT_CONTEXT_LIMIT):
        """Initialize with a default context limit.

        Args:
            default_context_limit: Context limit used when no model-specific
                limit is available. Defaults to the shared fallback context limit.
        """
        self._default_context_limit = default_context_limit
        self._tool_result_compactor = ToolResultCompactor()

    def estimate_tokens_for_text(self, text: str, is_json: bool = False) -> int:
        """Estimate tokens for text using a conservative character ratio."""
        if not text:
            return 0
        chars_per_token = CHARS_PER_TOKEN_COMPACT if is_json else CHARS_PER_TOKEN_NORMAL
        raw_estimate = len(text) / chars_per_token
        return int(raw_estimate * TOKEN_SAFETY_MARGIN)

    def estimate_messages(self, messages: list[dict]) -> int:
        """Estimate total tokens for a list of chat messages."""
        total = 0
        for msg in messages:
            total += 4  # role/formatting overhead
            content = msg.get("content") or ""
            if content:
                is_json = content.strip().startswith(("{", "["))
                total += self.estimate_tokens_for_text(content, is_json=is_json)
            if "tool_calls" in msg:
                for tc in msg.get("tool_calls", []):
                    func = tc.get("function", {})
                    total += self.estimate_tokens_for_text(
                        func.get("arguments", ""), is_json=True
                    )
                    total += 2
        return total

    def trim_messages(
        self,
        messages: list[dict],
        context_limit: int | None = None,
        reserved_output_tokens: int = DEFAULT_OUTPUT_RESERVE,
        raw_recent_context_percent: int = DEFAULT_RAW_RECENT_CONTEXT_PERCENT,
    ) -> list[dict]:
        """Compatibility wrapper for the old name.

        Existing callers/tests still invoke ``trim_messages``.  The behavior is
        now compression-first; the method name is retained to avoid a broad API
        rename while the architecture docs forbid drop-first budgeting.
        """
        return self.compress_messages(
            messages,
            context_limit=context_limit,
            reserved_output_tokens=reserved_output_tokens,
            raw_recent_context_percent=raw_recent_context_percent,
        )

    def compress_messages(
        self,
        messages: list[dict],
        context_limit: int | None = None,
        reserved_output_tokens: int = DEFAULT_OUTPUT_RESERVE,
        raw_recent_context_percent: int = DEFAULT_RAW_RECENT_CONTEXT_PERCENT,
    ) -> list[dict]:
        """Compress messages to fit a model context budget.

        The model context limit includes output tokens.  This method subtracts
        the response reserve before evaluating prompt size, then:

        1. compacts bulky tool results;
        2. keeps a raw recent-turn slice according to
           ``raw_recent_context_percent``;
        3. compresses older non-critical history into a system packet;
        4. progressively compresses remaining oversized packets;
        5. drops only as an explicit last-resort safety fallback.
        """
        limit = self._default_context_limit if context_limit is None else int(context_limit)
        available = max(0, limit - int(reserved_output_tokens or 0))
        if not messages:
            return messages

        if available <= 0:
            return self._irreducible_prompt(messages)

        working = [dict(m) for m in messages]
        working = self._compact_tool_payloads(working)

        if self.estimate_messages(working) <= available:
            return working

        raw_percent = max(0, min(100, int(raw_recent_context_percent or 0)))
        raw_recent_budget = int(available * (raw_percent / 100.0))
        compressed_budget = max(128, available - raw_recent_budget)

        compressed = self._compress_old_history(
            working,
            available=available,
            raw_recent_budget=raw_recent_budget,
            compressed_budget=compressed_budget,
        )
        if self.estimate_messages(compressed) <= available:
            return compressed

        # If the compressed history packet is still too large, shrink it before
        # touching raw recent/current-turn content.
        compressed = self._shrink_compressed_packets(compressed, available)
        if self.estimate_messages(compressed) <= available:
            return compressed

        # Compact non-primary system/category packets and non-current turns.
        compressed = self._compress_large_unprotected_messages(compressed, available)
        if self.estimate_messages(compressed) <= available:
            return compressed

        # Last resort only: keep the primary system prompt, current user turn,
        # and live tool exchange where possible.  This should be rare and logs a
        # warning so it is visible during testing.
        final = self._last_resort_drop(compressed, available)
        final_estimate = self.estimate_messages(final)
        if final_estimate > available:
            logger.warning(
                f"Token budget still over limit after last-resort safety fallback: "
                f"{final_estimate} tokens vs {available} prompt budget"
            )
        return final

    def compact_tool_result(
        self, tool_name: str, result: Any, max_chars: int = DEFAULT_MAX_TOOL_RESULT_CHARS,
    ) -> str:
        """Serialize and compact tool results for model input.

        ToolCallExecutor normally compacts results before they reach the message
        list. This method is the second safety net for persisted older tool
        messages and for any direct callers that still pass raw payloads.
        """
        parsed = result
        if isinstance(result, str):
            try:
                parsed = json.loads(result)
            except (TypeError, ValueError, json.JSONDecodeError):
                parsed = result
        text = self._tool_result_compactor.compact_for_message(tool_name, parsed)
        if len(text) <= max_chars:
            return text
        if tool_name in ("read_web_page", "browse_page", "browser_read_selected"):
            return self._middle_compress_text(text, MAX_WEB_PAGE_CHARS, label="web page compressed")
        return self._middle_compress_text(text, max_chars, label="tool result compressed")

    def _irreducible_prompt(self, messages: list[dict]) -> list[dict]:
        """Return only the irreducible system/current-turn scaffold.

        Used when the user explicitly sets the context cap to 0.  Optional
        history is disabled, but a provider call still needs the primary system
        instruction and current user turn.
        """
        if not messages:
            return []
        first_system_idx = next((i for i, m in enumerate(messages) if m.get("role") == "system"), None)
        last_user_idx = next((i for i in range(len(messages) - 1, -1, -1) if messages[i].get("role") == "user"), None)
        keep: list[int] = []
        if first_system_idx is not None:
            keep.append(first_system_idx)
        if last_user_idx is not None and last_user_idx not in keep:
            keep.append(last_user_idx)
            keep.extend(i for i in range(last_user_idx + 1, len(messages)) if i not in keep)
        if not keep:
            keep = [len(messages) - 1]
        return [dict(messages[i]) for i in sorted(set(keep))]

    def _compact_tool_payloads(self, messages: list[dict]) -> list[dict]:
        compacted = []
        for msg in messages:
            if msg.get("role") != "tool":
                compacted.append(msg)
                continue
            content = msg.get("content") or ""
            if len(content) > DEFAULT_MAX_TOOL_RESULT_CHARS:
                compacted.append({
                    **msg,
                    "content": self.compact_tool_result(
                        msg.get("name", "unknown"),
                        content,
                        max_chars=DEFAULT_MAX_TOOL_RESULT_CHARS,
                    ),
                })
            else:
                compacted.append(msg)
        return compacted

    def _protected_indices(self, messages: list[dict]) -> set[int]:
        first_system_idx = next((i for i, m in enumerate(messages) if m.get("role") == "system"), None)
        last_user_idx = next((i for i in range(len(messages) - 1, -1, -1) if messages[i].get("role") == "user"), None)
        protected: set[int] = set()
        if first_system_idx is not None:
            protected.add(first_system_idx)
        if last_user_idx is not None:
            protected.add(last_user_idx)
            # Anything after the current user is part of the live tool exchange
            # for this turn and must not be summarized away.
            protected.update(range(last_user_idx, len(messages)))
        for i, msg in enumerate(messages):
            content = str(msg.get("content") or "")
            if msg.get("role") == "system" and (
                content.startswith("PENDING ACTION CONTEXT")
                or "CATEGORY LIBRARY CONTEXT PACKET" in content
                or content.startswith("ACTIVE CATEGORY LIBRARY CONTEXT PACKET")
            ):
                protected.add(i)
        return protected

    def _compress_old_history(
        self,
        messages: list[dict],
        *,
        available: int,
        raw_recent_budget: int,
        compressed_budget: int,
    ) -> list[dict]:
        protected = self._protected_indices(messages)
        last_user_idx = next((i for i in range(len(messages) - 1, -1, -1) if messages[i].get("role") == "user"), len(messages) - 1)

        raw_recent: set[int] = set(protected)
        raw_estimate = sum(self.estimate_messages([messages[i]]) for i in raw_recent if 0 <= i < len(messages))

        # Preserve the most recent non-protected messages raw until the raw
        # reserve is used. Older non-protected messages are compressed.
        for i in range(last_user_idx - 1, -1, -1):
            if i in raw_recent:
                continue
            msg_estimate = self.estimate_messages([messages[i]])
            if raw_estimate + msg_estimate <= raw_recent_budget or not any(j not in protected for j in raw_recent):
                raw_recent.add(i)
                raw_estimate += msg_estimate
            else:
                break

        older_indices = [i for i in range(len(messages)) if i not in raw_recent]
        if not older_indices:
            return messages

        older_messages = [messages[i] for i in older_indices]
        compressed_text = self._summarize_messages_deterministically(
            older_messages,
            max_tokens=max(128, compressed_budget),
        )
        compressed_packet = {
            "role": "system",
            "content": "COMPRESSED EARLIER CONVERSATION CONTEXT:\n" + compressed_text,
        }

        result: list[dict] = []
        inserted = False
        for i, msg in enumerate(messages):
            if i in older_indices:
                if not inserted:
                    # Insert the compressed packet where the first older message
                    # used to be, preserving chronological placement.
                    result.append(compressed_packet)
                    inserted = True
                continue
            result.append(msg)
        return result

    def _summarize_messages_deterministically(self, messages: list[dict], max_tokens: int) -> str:
        max_chars = max(600, int((max_tokens / TOKEN_SAFETY_MARGIN) * CHARS_PER_TOKEN_NORMAL))
        lines: list[str] = []
        for msg in messages:
            role = msg.get("role", "message")
            content = msg.get("content")
            if content is None and msg.get("tool_calls"):
                try:
                    calls = [tc.get("function", {}).get("name", "tool") for tc in msg.get("tool_calls", [])]
                    content = "tool calls: " + ", ".join(calls)
                except Exception:
                    content = "tool calls"
            text = str(content or "").strip()
            if not text:
                continue
            # Keep identifiers and recent conclusions more than raw verbosity.
            text = " ".join(text.split())
            if len(text) > 1200:
                text = self._middle_compress_text(text, 1200, label="turn compressed")
            lines.append(f"- {role}: {text}")

        joined = "\n".join(lines) if lines else "No earlier context content."
        if len(joined) <= max_chars:
            return joined
        return self._middle_compress_text(joined, max_chars, label="earlier context compressed")

    def _middle_compress_text(self, text: str, max_chars: int, *, label: str) -> str:
        if len(text) <= max_chars:
            return text
        if max_chars < 200:
            return text[:max_chars]
        head = int(max_chars * 0.65)
        tail = max(80, max_chars - head - 80)
        omitted = len(text) - head - tail
        return (
            text[:head].rstrip()
            + f"\n...[{label}: {omitted} chars represented by summary/omission]...\n"
            + text[-tail:].lstrip()
        )

    def _shrink_compressed_packets(self, messages: list[dict], available: int) -> list[dict]:
        working = [dict(m) for m in messages]
        for target_chars in (6000, 4000, 2500, 1200, 600):
            changed = False
            for i, msg in enumerate(working):
                content = str(msg.get("content") or "")
                if content.startswith("COMPRESSED EARLIER CONVERSATION CONTEXT") and len(content) > target_chars:
                    working[i] = {**msg, "content": self._middle_compress_text(content, target_chars, label="compressed context shrunk")}
                    changed = True
            if changed and self.estimate_messages(working) <= available:
                return working
        return working

    def _compress_large_unprotected_messages(self, messages: list[dict], available: int) -> list[dict]:
        working = [dict(m) for m in messages]
        protected = self._protected_indices(working)
        for target_chars in (6000, 4000, 2500, 1200):
            changed = False
            for i, msg in enumerate(working):
                if i in protected:
                    continue
                content = str(msg.get("content") or "")
                if len(content) > target_chars:
                    working[i] = {**msg, "content": self._middle_compress_text(content, target_chars, label="message compressed")}
                    changed = True
            if changed and self.estimate_messages(working) <= available:
                return working

        # If even protected category/pending packets are impossibly huge, compact
        # them but preserve labels/IDs near the head and tail.
        for target_chars in (6000, 4000, 2500):
            changed = False
            for i, msg in enumerate(working):
                content = str(msg.get("content") or "")
                if i in protected and i != next((j for j, m in enumerate(working) if m.get("role") == "system"), -1):
                    if len(content) > target_chars:
                        working[i] = {**msg, "content": self._middle_compress_text(content, target_chars, label="protected context compressed")}
                        changed = True
            if changed and self.estimate_messages(working) <= available:
                return working
        return working

    def _last_resort_drop(self, messages: list[dict], available: int) -> list[dict]:
        working = [dict(m) for m in messages]
        protected = self._protected_indices(working)
        logger.warning(
            "Context compression could not fit the prompt; applying last-resort "
            "oldest-message drop. This should be investigated."
        )
        changed = True
        while changed and self.estimate_messages(working) > available and len(working) > 2:
            changed = False
            protected = self._protected_indices(working)
            for i, _msg in enumerate(list(working)):
                if i in protected:
                    continue
                working.pop(i)
                changed = True
                break
        return working
