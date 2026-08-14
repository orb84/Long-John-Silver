"""Tests for the LJS AI intent router.

Verifies fast keyword routing, plural keyword matches, and the restored
LLM fallback routing path under mock completions.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from src.ai.intent_router import route_intent_fast, IntentRouter, Intent


class TestFastRouting:
    """The compatibility seam must never classify free-form text by keywords."""

    @pytest.mark.parametrize(
        "message",
        [
            "download the latest episode",
            "get the remaining episodes",
            "search for ratings",
            "when does the new episode air?",
            "change the model to gpt-4",
            "remove shows",
            "hello there",
            "scarica il prossimo episodio",
        ],
    )
    def test_fast_routing_is_disabled(self, message):
        """All natural-language requests must flow through contextual LLM routing."""
        intent, confidence = route_intent_fast(message)
        assert intent is None
        assert confidence == 0.0


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