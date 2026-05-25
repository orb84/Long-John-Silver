"""
Tests for ClarificationBuilder — targeted clarification prompts
based on detected intent hints and keyword clues.
"""

import pytest
from src.ai.intent_router import ClarificationBuilder, Intent


class TestClarificationBuilder:
    """Tests for targeted clarification prompt generation."""

    def test_download_keyword_gets_download_clarification(self):
        """A message with 'download' gets a download-specific question."""
        result = ClarificationBuilder.build("download it", intent_hint=Intent.DOWNLOAD)
        assert "download" in result.lower()
        assert "show" in result.lower() or "episode" in result.lower()

    def test_fetch_keyword_gets_download_clarification(self):
        """A message with 'fetch' gets a download-specific question."""
        result = ClarificationBuilder.build("fetch the new episode", intent_hint=Intent.DOWNLOAD)
        assert "fetch" in result.lower() or "download" in result.lower() or "show" in result.lower()

    def test_search_keyword_gets_search_clarification(self):
        """A message with 'search' gets a search-specific question."""
        result = ClarificationBuilder.build("search for it", intent_hint=Intent.SEARCH)
        assert "search" in result.lower() or "find" in result.lower()

    def test_find_keyword_gets_search_clarification(self):
        """A message with 'find' gets a search-specific question."""
        result = ClarificationBuilder.build("find info", intent_hint=Intent.SEARCH)
        assert "show" in result.lower() or "search" in result.lower()

    def test_add_keyword_gets_config_clarification(self):
        """A message with 'add' gets a config-specific question."""
        result = ClarificationBuilder.build("add a new one", intent_hint=Intent.CONFIG)
        assert "add" in result.lower() or "watch list" in result.lower()

    def test_setting_keyword_gets_config_clarification(self):
        """A message with 'setting' gets a config-specific question."""
        result = ClarificationBuilder.build("change settings", intent_hint=Intent.CONFIG)
        assert "setting" in result.lower() or "config" in result.lower()

    def test_download_hint_without_keyword_still_targeted(self):
        """A download hint without matching keyword gets a generic download question."""
        result = ClarificationBuilder.build("do it", intent_hint=Intent.DOWNLOAD)
        assert "download" in result.lower()

    def test_search_hint_without_keyword_still_targeted(self):
        """A search hint without matching keyword gets a generic search question."""
        result = ClarificationBuilder.build("look into it", intent_hint=Intent.SEARCH)
        assert "search" in result.lower() or "show" in result.lower()

    def test_no_hint_gets_generic_clarification(self):
        """No intent hint gets the generic multi-option clarification."""
        result = ClarificationBuilder.build("hmm")
        assert "Download" in result
        assert "Search" in result
        assert "Configure" in result

    def test_none_hint_gets_generic_clarification(self):
        """None hint explicitly gets generic clarification."""
        result = ClarificationBuilder.build("hmm", intent_hint=None)
        assert "Download" in result

    def test_generic_message_with_download_hint_not_matching_keywords(self):
        """Message like 'do it' with DOWNLOAD hint uses the fallback download question."""
        result = ClarificationBuilder.build("do it", intent_hint=Intent.DOWNLOAD)
        assert "show" in result.lower()
        assert "download" in result.lower()