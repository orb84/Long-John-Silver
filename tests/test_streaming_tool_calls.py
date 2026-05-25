"""
Tests for StreamingToolCallAssembler — correct assembly of
streaming tool-call deltas by index and ID.
"""

import pytest
from unittest.mock import MagicMock

from src.ai.streaming_tool_calls import StreamingToolCallAssembler
from src.core.models import AssembledToolCall


class TestStreamingToolCallAssembler:
    """Tests for assembling streaming tool call deltas."""

    def test_single_tool_call(self):
        """A single tool call should assemble correctly."""
        assembler = StreamingToolCallAssembler()

        # Simulate a single tool call: name + start of arguments
        delta1 = MagicMock()
        delta1.index = 0
        delta1.id = "call_abc123"
        delta1.function = MagicMock()
        delta1.function.name = "search_torrents"
        delta1.function.arguments = '{"query":'

        # Second chunk: rest of arguments
        delta2 = MagicMock()
        delta2.index = 0
        delta2.id = None
        delta2.function = MagicMock()
        delta2.function.name = None
        delta2.function.arguments = '"Severance"}'

        assembler.add_delta([delta1])
        assembler.add_delta([delta2])

        calls = assembler.complete_calls()
        assert len(calls) == 1

    def test_two_parallel_tool_calls(self):
        """Two interleaved tool calls should assemble correctly."""
        assembler = StreamingToolCallAssembler()

        # First tool call starts
        tc1_start = MagicMock()
        tc1_start.index = 0
        tc1_start.id = "call_001"
        tc1_start.function = MagicMock()
        tc1_start.function.name = "movie.resolve_metadata"
        tc1_start.function.arguments = '{"title":'

        # Second tool call starts
        tc2_start = MagicMock()
        tc2_start.index = 1
        tc2_start.id = "call_002"
        tc2_start.function = MagicMock()
        tc2_start.function.name = "search_torrents"
        tc2_start.function.arguments = '{"query":'

        # First call argument continuation
        tc1_args = MagicMock()
        tc1_args.index = 0
        tc1_args.id = None
        tc1_args.function = MagicMock()
        tc1_args.function.name = None
        tc1_args.function.arguments = '"Severance"}'

        # Second call argument continuation
        tc2_args = MagicMock()
        tc2_args.index = 1
        tc2_args.id = None
        tc2_args.function = MagicMock()
        tc2_args.function.name = None
        tc2_args.function.arguments = '"Severance S02E01"}'

        assembler.add_delta([tc1_start])
        assembler.add_delta([tc2_start])
        assembler.add_delta([tc1_args])
        assembler.add_delta([tc2_args])

        calls = assembler.complete_calls()
        assert len(calls) == 2
        names = {c.name for c in calls}
        assert "movie.resolve_metadata" in names
        assert "search_torrents" in names

    def test_missing_id_gets_fallback(self):
        """Tool calls without IDs should get fallback IDs."""
        assembler = StreamingToolCallAssembler()

        delta = MagicMock()
        delta.index = 0
        delta.id = None
        delta.function = MagicMock()
        delta.function.name = "movie.resolve_metadata"
        delta.function.arguments = '{"title":"Test"}'

        assembler.add_delta([delta])
        calls = assembler.complete_calls()

        assert len(calls) == 1
        # Should have a fallback ID
        assert calls[0].id != ""
        assert calls[0].name == "movie.resolve_metadata"

    def test_split_arguments_concatenated(self):
        """Arguments split across chunks should be concatenated."""
        assembler = StreamingToolCallAssembler()

        # Chunk 1: name + start of arguments
        chunk1 = MagicMock()
        chunk1.index = 0
        chunk1.id = "call_123"
        chunk1.function = MagicMock()
        chunk1.function.name = "web_search"
        chunk1.function.arguments = '{"query":"test'

        # Chunk 2: rest of arguments
        chunk2 = MagicMock()
        chunk2.index = 0
        chunk2.id = None
        chunk2.function = MagicMock()
        chunk2.function.name = None
        chunk2.function.arguments = '"}'

        assembler.add_delta([chunk1])
        assembler.add_delta([chunk2])

        calls = assembler.complete_calls()
        assert len(calls) == 1
        assert calls[0].arguments == '{"query":"test"}'

    def test_no_tool_calls_returns_empty(self):
        """If no deltas are added, complete_calls should return empty list."""
        assembler = StreamingToolCallAssembler()
        calls = assembler.complete_calls()
        assert calls == []

    def test_empty_name_filtered(self):
        """Deltas with no name should be filtered out."""
        assembler = StreamingToolCallAssembler()

        # A delta with no name and no arguments should be ignored
        delta = MagicMock()
        delta.index = 0
        delta.id = None
        delta.function = MagicMock()
        delta.function.name = None
        delta.function.arguments = None

        assembler.add_delta([delta])
        calls = assembler.complete_calls()
        assert len(calls) == 0


class TestAssembledToolCall:
    """Tests for the AssembledToolCall model."""

    def test_default_values(self):
        call = AssembledToolCall()
        assert call.id == ""
        assert call.name == ""
        assert call.arguments == ""

    def test_custom_values(self):
        call = AssembledToolCall(
            id="call_abc",
            name="search_torrents",
            arguments='{"query":"Severance"}',
        )
        assert call.id == "call_abc"
        assert call.name == "search_torrents"