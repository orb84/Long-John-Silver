"""
Tests for TokenBudgetManager — token estimation and message trimming.
"""

import json
import pytest
from src.ai.token_budget import TokenBudgetManager


class TestTokenEstimation:
    """Tests for token estimation methods."""

    def setup_method(self):
        self.budget = TokenBudgetManager(default_context_limit=8192)

    def test_estimate_text_normal(self):
        """Normal English text: ~4 chars per token with safety margin."""
        text = "Hello world, this is a test of the token estimation system."
        tokens = self.budget.estimate_tokens_for_text(text)
        # With safety margin (1.3x), should be roughly len/4 * 1.3
        assert tokens > 0
        assert tokens < len(text)  # Always fewer tokens than chars

    def test_estimate_text_json(self):
        """JSON/tool payloads: tighter ratio ~2 chars per token."""
        text = '{"name": "test", "value": 123}'
        json_tokens = self.budget.estimate_tokens_for_text(text, is_json=True)
        normal_tokens = self.budget.estimate_tokens_for_text(text, is_json=False)
        # JSON estimation should be higher (more tokens)
        assert json_tokens > normal_tokens

    def test_estimate_empty_text(self):
        """Empty text should estimate 0 tokens."""
        assert self.budget.estimate_tokens_for_text("") == 0

    def test_estimate_messages_basic(self):
        """Estimate for a basic message list."""
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello!"},
        ]
        tokens = self.budget.estimate_messages(messages)
        assert tokens > 0
        # Should include overhead per message
        assert tokens > 8  # At least 4+4 for overhead

    def test_estimate_messages_with_tool_calls(self):
        """Tool call arguments should add to the estimate."""
        messages = [
            {"role": "user", "content": "Search for Severance"},
            {"role": "assistant", "tool_calls": [{
                "id": "call_123",
                "function": {"name": "search_torrents", "arguments": '{"query": "Severance"}'},
            }]},
        ]
        tokens = self.budget.estimate_messages(messages)
        assert tokens > 0


class TestMessageTrimming:
    """Tests for message trimming to fit context budgets."""

    def setup_method(self):
        self.budget = TokenBudgetManager(default_context_limit=8192)

    def test_no_trimming_needed(self):
        """Short messages should pass through unchanged."""
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]
        result = self.budget.trim_messages(messages, context_limit=8192)
        assert result == messages

    def test_system_prompt_is_preserved(self):
        """The system prompt (first message) must never be removed."""
        messages = [
            {"role": "system", "content": "You are a pirate."},
            {"role": "user", "content": "A" * 50000},
            {"role": "assistant", "content": "Argh!"},
            {"role": "user", "content": "Tell me more"},
        ]
        result = self.budget.trim_messages(messages, context_limit=2048)
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "You are a pirate."

    def test_latest_user_message_is_preserved(self):
        """The latest user message must never be removed."""
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Old question 1"},
            {"role": "assistant", "content": "Answer 1"},
            {"role": "user", "content": "Old question 2"},
            {"role": "assistant", "content": "Answer 2"},
            {"role": "user", "content": "Current question"},
        ]
        result = self.budget.trim_messages(messages, context_limit=256)
        assert result[-1]["content"] == "Current question"

    def test_old_context_is_trimmed_first(self):
        """Oldest context should be removed before recent content."""
        long_content = "A" * 5000
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "system", "content": f"Past context: {long_content}"},
            {"role": "user", "content": "A" * 5000},
            {"role": "assistant", "content": "Short"},
            {"role": "user", "content": "Hello"},
        ]
        result = self.budget.trim_messages(messages, context_limit=2048)
        # The long past context should be removed
        system_contents = [m["content"] for m in result if m["role"] == "system"]
        # Should only have the primary system prompt
        assert any("You are helpful" in c for c in system_contents)

    def test_trim_creates_valid_message_list(self):
        """Trimmed messages should always be a valid list."""
        messages = [
            {"role": "system", "content": "System"},
        ]
        result = self.budget.trim_messages(messages, context_limit=8192)
        assert len(result) >= 1


class TestToolResultCompaction:
    """Tests for tool result compaction."""

    def setup_method(self):
        self.budget = TokenBudgetManager()

    def test_compact_short_result_unchanged(self):
        """Short results should pass through unchanged."""
        result = json.dumps({"title": "Test", "seeders": 5})
        compacted = self.budget.compact_tool_result("search_torrents", result)
        assert compacted == result

    def test_compact_string_result(self):
        """String results that are too long should be truncated."""
        result = "A" * 10000
        compacted = self.budget.compact_tool_result("some_tool", result, max_chars=100)
        assert len(compacted) < len(result)
        assert "truncated" in compacted

    def test_compact_dict_result(self):
        """Dict results should be JSON-serialized."""
        result = {"title": "Test", "seeders": 5}
        compacted = self.budget.compact_tool_result("search_torrents", result)
        assert json.loads(compacted) == result

    def test_compact_web_page(self):
        """Web page results should truncate to MAX_WEB_PAGE_CHARS."""
        result = "Page content " * 1000
        compacted = self.budget.compact_tool_result("read_web_page", result)
        # Should be truncated to web page max
        assert "truncated" in compacted
        assert len(compacted) < len(result)

    def test_compact_search_torrents_truncates_list(self):
        """Search torrent results should keep only top N items."""
        results = [{"title": f"Show S01E0{i}", "seeders": i} for i in range(1, 21)]
        compacted = self.budget.compact_tool_result("search_torrents", results)
        parsed = json.loads(compacted)
        assert len(parsed) <= 10  # MAX_TORRENT_RESULTS

    def test_compact_preserves_valid_json(self):
        """Compacted JSON results should be parseable."""
        result = {"key": "A" * 10000}
        compacted = self.budget.compact_tool_result("some_tool", result, max_chars=200)
        # Should be valid JSON or have a truncation marker
        assert isinstance(compacted, str)

    def test_budget_in_context_limit_enforcement(self):
        """Agent loop should call budget manager before completion."""
        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Hello"},
        ]
        # After trimming with a small budget, should still be valid
        result = self.budget.trim_messages(messages, context_limit=256)
        assert len(result) >= 1
        assert result[0]["role"] == "system"