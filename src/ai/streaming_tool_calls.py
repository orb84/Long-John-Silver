"""
Streaming tool call assembly for LJS.

Correctly assembles provider streaming tool-call deltas by index
and tool call ID, handling interleaved chunks, missing IDs on
early deltas, and multiple parallel tool calls. Replaces the
fragile single-current_tool_call approach in AIAssistant.run_stream().
"""

from loguru import logger
from typing import Optional

from src.core.models import AssembledToolCall


class StreamingToolCallAssembler:
    """Assembles provider streaming tool-call deltas by index and ID.

    LiteLLM providers may emit tool call deltas with interleaved
    indices (0, 1, 0, 1, ...) when multiple tool calls are generated
    in parallel. This assembler correctly tracks each tool call by
    its index, concatenating argument fragments in order.

    Usage:
        assembler = StreamingToolCallAssembler()
        async for chunk in stream_response:
            # ... handle content delta ...
            if hasattr(delta, "tool_calls") and delta.tool_calls:
                assembler.add_delta(delta.tool_calls)
        calls = assembler.complete_calls()
    """

    def __init__(self):
        """Initialize with empty pending tool calls."""
        self._pending: dict[int, dict] = {}

    def add_delta(self, tool_call_deltas: list) -> None:
        """Add one or more streaming tool-call deltas.

        Handles three cases:
        1. New tool call (has a name): starts a new pending call.
        2. Argument continuation (name is empty, arguments append): extends existing call.
        3. ID fragment: appends to the existing call's ID.

        Args:
            tool_call_deltas: List of tool call delta objects (dicts or LiteLLM types).
        """
        for tc_chunk in tool_call_deltas:
            if not tc_chunk:
                continue

            # Support both dict-style and object-style tool call deltas
            # LiteLLM may return raw dicts or ChatCompletionDeltaToolCall objects
            index = tc_chunk.get("index", None) if isinstance(tc_chunk, dict) else getattr(tc_chunk, "index", None)
            if index is None:
                if self._pending and len(self._pending) == 1:
                    index = list(self._pending.keys())[0]
                else:
                    continue

            chunk_id = tc_chunk.get("id", None) if isinstance(tc_chunk, dict) else getattr(tc_chunk, "id", None)

            func = None
            if isinstance(tc_chunk, dict):
                func = tc_chunk.get("function")
            elif hasattr(tc_chunk, "function"):
                func = tc_chunk.function
            func_name = func.get("name", None) if isinstance(func, dict) else (func.name if func else None)
            func_args = func.get("arguments", None) if isinstance(func, dict) else (func.arguments if func else None)

            # Case 1: New tool call starts (has a function name)
            if func_name and func_name:
                self._pending[index] = {
                    "id": chunk_id or "",
                    "name": func_name,
                    "arguments": func_args or "",
                }
            # Case 2: Argument continuation for an existing call
            elif func_args and index in self._pending:
                self._pending[index]["arguments"] += func_args
                if chunk_id:
                    self._pending[index]["id"] = chunk_id
            # Case 3: ID-only delta (some providers send these)
            elif chunk_id and index in self._pending:
                if not self._pending[index]["id"]:
                    self._pending[index]["id"] = chunk_id

    def complete_calls(self) -> list[AssembledToolCall]:
        """Return all assembled tool calls after the stream finishes.

        Filters out empty calls (no name) and assigns fallback IDs
        to any calls missing them.

        Returns:
            List of AssembledToolCall objects.
        """
        calls = []
        for index in sorted(self._pending.keys()):
            call = self._pending[index]
            if not call.get("name"):
                continue
            # Assign fallback ID if missing
            call_id = call.get("id", "") or f"call_{index}_{call['name']}"
            calls.append(AssembledToolCall(
                id=call_id,
                name=call["name"],
                arguments=call["arguments"],
            ))
        logger.debug(f"Assembled {len(calls)} tool calls from streaming deltas")
        return calls