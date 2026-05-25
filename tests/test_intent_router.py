"""Tests for the LJS AI intent router.

Verifies fast keyword routing, plural keyword matches, and the restored
LLM fallback routing path under mock completions.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from src.ai.intent_router import route_intent_fast, IntentRouter, Intent


class TestFastRouting:
    """Tests the fast regex keyword-matching path."""

    def test_download_intent(self):
        """Verify singular forms match DOWNLOAD intent."""
        intent, confidence = route_intent_fast("download the latest episode")
        assert intent == Intent.DOWNLOAD
        intent, confidence = route_intent_fast("find and download stranger things")
        assert intent == Intent.DOWNLOAD
        intent, confidence = route_intent_fast("get me a torrent for house of the dragon")
        assert intent == Intent.DOWNLOAD

    def test_download_intent_plurals(self):
        """Verify plural forms (episodes, seasons) match DOWNLOAD intent."""
        intent, confidence = route_intent_fast("download the missing episodes")
        assert intent == Intent.DOWNLOAD
        intent, confidence = route_intent_fast("get the remaining episodes")
        assert intent == Intent.DOWNLOAD
        intent, confidence = route_intent_fast("fill the remaining seasons")
        assert intent == Intent.DOWNLOAD

    def test_search_intent(self):
        """Verify SEARCH intent keyword matches."""
        intent, confidence = route_intent_fast("search for the last of us ratings")
        assert intent == Intent.SEARCH
        intent, confidence = route_intent_fast("when does the new episode air?")
        assert intent == Intent.SEARCH
        intent, confidence = route_intent_fast("what's the IMDB rating of breaking bad")
        assert intent == Intent.SEARCH

    def test_search_intent_plurals(self):
        """Verify plural forms match SEARCH intent."""
        intent, confidence = route_intent_fast("info about the new shows")
        assert intent == Intent.SEARCH
        intent, confidence = route_intent_fast("are there new episodes of severance?")
        assert intent == Intent.SEARCH

    def test_config_intent(self):
        """Verify CONFIG intent keyword matches."""
        intent, confidence = route_intent_fast("change the model to gpt-4")
        assert intent == Intent.CONFIG

    def test_config_intent_plurals(self):
        """Verify CONFIG intent matches plural forms."""
        intent, confidence = route_intent_fast("add the shows to tracked list")
        assert intent == Intent.CONFIG
        intent, confidence = route_intent_fast("remove shows")
        assert intent == Intent.CONFIG

    def test_ambiguous_prefers_download(self):
        """Verify download intent priority when matching multiple keywords."""
        intent, confidence = route_intent_fast("find and download the show")
        assert intent == Intent.DOWNLOAD

    def test_unknown_returns_none(self):
        """Verify unmatched strings return None for fast routing, triggering LLM fallback."""
        intent, confidence = route_intent_fast("hello there")
        assert intent is None


@pytest.mark.asyncio
class TestLLMFallbackRouting:
    """Tests the fallback routing through the LLM when fast matching yields no keyword matches."""

    async def test_llm_fallback_download(self):
        """Verify fallback to LLM routing classifies DOWNLOAD correctly when keywords do not match."""
        # Create a mock LLM client returning a mock completion
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="DOWNLOAD"))]
        
        mock_llm = MagicMock()
        mock_llm.completion = AsyncMock(return_value=mock_response)

        router = IntentRouter(llm_client=mock_llm)
        # Message has no standard fast-match keywords
        intent = await router.route("I want to obtain the files for that season")

        assert intent == Intent.DOWNLOAD
        mock_llm.completion.assert_called_once()

    async def test_llm_fallback_chat_on_none(self):
        """Verify fallback to LLM routing defaults to CHAT on conversational queries."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="CHAT"))]
        
        mock_llm = MagicMock()
        mock_llm.completion = AsyncMock(return_value=mock_response)

        router = IntentRouter(llm_client=mock_llm)
        intent = await router.route("tell me a pirate joke")

        assert intent == Intent.CHAT
        mock_llm.completion.assert_called_once()