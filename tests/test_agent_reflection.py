"""
Tests for agent reflection — ReasoningPlanner.reflect() integration.

Verifies that reflection is called for SEARCH/DOWNLOAD intents,
that SUFFICIENT stops the loop, and that CHAT never reflects.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.ai.agent_loop_state import (
    AgentLoopState,
    MIN_TOOL_RESULTS_BEFORE_REFLECT,
    MIN_ITERATIONS_BETWEEN_REFLECTIONS,
    INTENTS_ELIGIBLE_FOR_REFLECTION,
)
from src.ai.reasoning import ReasoningPlanner
from src.core.models import Intent
from src.utils.circuit_breaker import CircuitBreaker


def _run(coro):
    """Run an async test coroutine with a fresh event loop."""
    return asyncio.run(coro)


class TestAgentLoopState:
    """Tests for AgentLoopState data model."""

    def test_initial_state(self):
        state = AgentLoopState()
        assert state.tool_results == []
        assert state.force_final_response is False

    def test_add_tool_results(self):
        state = AgentLoopState()
        state.tool_results.append("search_torrents: found 5 results")
        state.tool_results.append("movie.resolve_metadata: Severance S02")
        assert len(state.tool_results) == 2

    def test_force_final_response(self):
        state = AgentLoopState()
        state.force_final_response = True
        assert state.force_final_response is True


class TestReflectionConstants:
    """Tests for reflection policy constants."""

    def test_min_tool_results_before_reflect(self):
        assert MIN_TOOL_RESULTS_BEFORE_REFLECT >= 1

    def test_min_iterations_between_reflections(self):
        assert MIN_ITERATIONS_BETWEEN_REFLECTIONS >= 2

    def test_search_is_eligible(self):
        assert "SEARCH" in INTENTS_ELIGIBLE_FOR_REFLECTION

    def test_download_is_eligible(self):
        assert "DOWNLOAD" in INTENTS_ELIGIBLE_FOR_REFLECTION

    def test_chat_is_not_eligible(self):
        assert "CHAT" not in INTENTS_ELIGIBLE_FOR_REFLECTION

    def test_config_is_not_eligible(self):
        assert "CONFIG" not in INTENTS_ELIGIBLE_FOR_REFLECTION


class TestReasoningPlannerReflect:
    """Tests for ReasoningPlanner.reflect() method."""

    def _make_planner(self, llm_client=None):
        """Create a planner with mock LLM client."""
        return ReasoningPlanner(
            llm_client=llm_client,
            circuit_breaker=CircuitBreaker("test_reasoning", failure_threshold=3, recovery_seconds=30),
        )

    def test_reflect_returns_sufficient(self):
        """When LLM says SUFFICIENT, reflect should return that."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "SUFFICIENT"
        mock_client.completion = AsyncMock(return_value=mock_response)

        planner = self._make_planner(llm_client=mock_client)
        result = _run(
            planner.reflect(
                "Find info on Severance",
                ["movie.resolve_metadata: Severance S02, rating 8.7"],
                task="research",
            )
        )
        assert result is not None
        assert "SUFFICIENT" in result.upper()

    def test_reflect_returns_need_more(self):
        """When LLM says NEED MORE, reflect should return that."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "NEED MORE: need more seeders info"
        mock_client.completion = AsyncMock(return_value=mock_response)

        planner = self._make_planner(llm_client=mock_client)
        result = _run(
            planner.reflect(
                "Download Severance S02E01",
                ["search_torrents: only 2 results found"],
                task="download",
            )
        )
        assert result is not None
        assert "NEED MORE" in result.upper()

    def test_reflect_returns_none_on_failure(self):
        """When LLM call fails, reflect should return None gracefully."""
        mock_client = MagicMock()
        mock_client.completion = AsyncMock(side_effect=Exception("API error"))
        planner = self._make_planner(llm_client=mock_client)

        result = _run(
            planner.reflect("test query", ["result 1"], task="research")
        )
        assert result is None

    def test_reflect_uses_few_tool_results(self):
        """Reflection should only send the last few tool results."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "SUFFICIENT"

        async def capture_completion(**kwargs):
            """Capture the prompt to verify only last results are sent."""
            capture_completion.captured_prompt = kwargs["messages"][0]["content"]
            return mock_response

        capture_completion.captured_prompt = ""
        mock_client.completion = capture_completion

        planner = self._make_planner(llm_client=mock_client)
        many_results = [f"result_{i}" for i in range(10)]

        _run(
            planner.reflect("test query", many_results, task="research")
        )
        # Only last 3 results should be in the prompt
        assert "result_9" in capture_completion.captured_prompt
        assert "result_7" in capture_completion.captured_prompt
        # result_0 should NOT be in the prompt (only last 3)
        assert "result_0" not in capture_completion.captured_prompt
