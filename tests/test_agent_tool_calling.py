"""
Real tests that verify the AI agent actually calls tools via the LLM.

Unlike the existing agent interaction tests (which only check
``len(response) > N`` and pass on "I can't help with that"),
these tests directly exercise the completion pipeline and verify:
1. The LLM returns tool_calls when given tool definitions
2. The agent loop executes tool calls and returns results
3. The streaming assembler correctly builds tool calls from deltas
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from loguru import logger

from tests.harness import ServerTestHarness


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.environ.get("LJS_RUN_LIVE_TESTS") != "1",
        reason="requires running LJS server and external LLM/network access",
    ),
]


async def _get_harness() -> ServerTestHarness:
    """Connect to the running server."""
    h = ServerTestHarness("http://localhost:8088")
    await h.connect()
    return h


class TestLLMToolCalling:
    """Verify the LLM actually calls tools, not just responds with text."""

    async def test_model_returns_tool_calls_when_given_tools(self):
        """The 120B model MUST return tool_calls, not text, when tools are provided.

        This is the minimum bar — if this fails, the agent can never work.
        """
        import httpx

        settings_path = Path("config/settings.local.yaml")
        import yaml
        with open(settings_path) as f:
            s = yaml.safe_load(f)

        api_key = s["llm"]["api_key"]
        api_base = s["llm"]["api_base"]
        model_name = s["llm"]["model"]

        tools = [{
            "type": "function",
            "function": {
                "name": "search_torrents",
                "description": "Search for torrents across all providers",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                    },
                    "required": ["query"],
                },
            },
        }]

        payload = {
            "model": model_name,
            "messages": [{
                "role": "user",
                "content": "Search for Firefly season 1 episode 1 torrents in Italian",
            }],
            "tools": tools,
            "stream": False,
            "max_tokens": 200,
            "temperature": 0.1,
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{api_base}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text[:300]}"

            data = resp.json()
            msg = data["choices"][0]["message"]
            tool_calls = msg.get("tool_calls", [])

            assert len(tool_calls) >= 1, (
                f"Model returned zero tool_calls. Instead got: "
                f"content='{msg.get('content', '')[:200]}' "
                f"finish_reason={data['choices'][0].get('finish_reason')}"
            )

            call = tool_calls[0]["function"]
            assert call["name"] == "search_torrents", f"Wrong tool: {call['name']}"
            args = json.loads(call["arguments"])
            assert "Firefly" in args.get("query", ""), f"Query missing Firefly: {args}"
            logger.info(f"Tool call verified: {call['name']}({json.dumps(args)})")


class TestAgentToolExecution:
    """Verify the agent loop actually executes tool calls end-to-end."""

    async def test_download_intent_actually_searches_torrents(self):
        """DOWNLOAD intent with torrent search should NOT return 'I can't help'.

        If the LLM supports tool calling, it should call search_torrents
        and return actual results, not a refusal.

        This replaces the fraudulent test in test_agent_interactions.py
        that passes on len(resp) > 20 regardless of content.
        """
        harness = await _get_harness()
        try:
            resp = await harness.send_chat(
                "Search for torrents of Firefly S01E01 in English language. "
                "Use the search_torrents tool."
            )
            logger.info(f"Agent response ({len(resp)} chars): {resp[:400]}")

            # A real tool call response should contain torrent-related info,
            # NOT a refusal message.
            refusal_patterns = [
                "can't help", "cannot help", "I'm sorry", "not able",
                "not possible", "unable to",
            ]
            for pattern in refusal_patterns:
                assert pattern.lower() not in resp.lower(), (
                    f"Agent refused: '{pattern}' found in response. "
                    f"LLM is NOT executing tool calls."
                )

            # Should have some meaningful content (not just a couple of words)
            assert len(resp) > 50, (
                f"Response too short: {len(resp)} chars — likely empty tool result"
            )

            # Should mention Firefly or the search query
            assert "Firefly" in resp or "S01E01" in resp or "torrent" in resp.lower(), (
                f"Response doesn't mention the search topic"
            )
        finally:
            await harness.disconnect()

    async def test_search_intent_uses_tmdb_tools(self):
        """SEARCH intent for show info should call movie.resolve_metadata.

        The agent should NOT return a refusal text response.
        """
        harness = await _get_harness()
        try:
            resp = await harness.send_chat(
                "Look up Firefly on TMDB. What genres does it have? "
                "Use the movie.resolve_metadata tool."
            )
            logger.info(f"Agent response ({len(resp)} chars): {resp[:400]}")

            refusal_patterns = [
                "can't help", "cannot help", "I'm sorry", "not able",
                "I've not received", "I can't tell",
            ]
            for pattern in refusal_patterns:
                assert pattern.lower() not in resp.lower(), (
                    f"Agent refused: '{pattern}' found in response."
                )

            assert len(resp) > 50, f"Response too short: {len(resp)} chars"
            # Should mention genres, Firefly, or Sci-Fi
            assert any(w in resp.lower() for w in ["genre", "sci-fi", "drama", "firefly", "action"]), (
                f"Response doesn't contain expected show info"
            )
        finally:
            await harness.disconnect()

    async def test_config_intent_adds_and_verifies_show(self):
        """CONFIG intent MUST persist show changes AND verify them.

        The existing test only checks 'not False' — useless.
        This test actually verifies the round-trip.
        """
        harness = await _get_harness()
        try:
            # Use a unique show name to avoid conflicts
            resp = await harness.send_chat(
                "Add a show called 'Test Agent Verify Show 2026' to my watchlist"
            )
            logger.info(f"Agent response: {resp[:200]}")

            # Verify it was actually added
            detail = await harness.get_category_item_detail("Test Agent Verify Show 2026")
            assert detail is not None, "Show was not found after agent addition"
            show_obj = detail.get("show", {}) or detail
            assert show_obj.get("name") == "Test Agent Verify Show 2026" or show_obj.get("key") == "Test Agent Verify Show 2026"

            # Clean up
            await harness.remove_category_item("Test Agent Verify Show 2026")
        finally:
            await harness.disconnect()
