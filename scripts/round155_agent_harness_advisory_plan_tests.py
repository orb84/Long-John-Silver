#!/usr/bin/env python3
"""Round 155 agent harness tests: advisory plans, no lexical guard, bounded retries."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ai.plan_coordinator import PlanCoordinator
from src.ai.reasoning import ReasoningPlanner
from src.ai.tool_executor import ToolCallExecutor
from src.ai.tool_registry import ToolRegistry
from src.core.models import AgentPlan, Intent, PlanStep, ToolExecutionContext


def _tool_schema(name: str, props: dict | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"{name} tool",
            "parameters": {"type": "object", "properties": props or {}, "required": []},
        },
    }


class _FakeLLM:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)

    async def completion(self, **kwargs):
        class _Resp:
            def __init__(self, text: str) -> None:
                self.choices = [type("Choice", (), {"message": type("Msg", (), {"content": text})()})()]
        return _Resp(self.responses.pop(0) if self.responses else "")


async def test_planner_has_no_lexical_overlap_guard() -> None:
    # This deliberately has no meaningful lexical overlap with the user prompt.
    # It is still accepted because planner validation is objective only. The
    # coordinator may later discard/ignore it as advisory, but ReasoningPlanner
    # must not pretend to semantically validate natural-language equivalence.
    canned = '''{
      "intent": "SEARCH",
      "user_goal": "Find recent research papers on transformer models for image classification.",
      "constraints": {"max_results": 10},
      "steps": [
        {"id": "step1", "tool_name": "WebSearch", "arguments": {"query": "transformer models image classification 2023"}, "depends_on": [], "success_condition": "results"}
      ]
    }'''
    planner = ReasoningPlanner(llm_client=_FakeLLM([canned]))
    plan = await planner.generate_plan(
        "I am looking for a movie from Spielberg made after the year 2000 and about kids and some alien cargo lost by the government ?",
        Intent.SEARCH,
        context='ACTIVE CATEGORY LIBRARY CONTEXT PACKET: {"category_id":"movie"}',
        tool_schemas=[_tool_schema("metadata_lookup"), _tool_schema("web_search")],
    )
    assert plan is not None
    assert plan.steps[0].tool_name == "web_search"


class _DummyToolExecutor:
    def get_definitions(self, allowed_tool_names: set[str]) -> list[dict]:
        return [_tool_schema(name) for name in sorted(allowed_tool_names)]


class _StaticPlanner:
    def __init__(self, plan):
        self.plan = plan

    async def generate_plan(self, *args, **kwargs):
        return self.plan


class _CoordinatorWithPlanner(PlanCoordinator):
    def __init__(self, plan):
        super().__init__(_DummyToolExecutor(), llm_client=None)
        self._plan = plan

    def create_planner(self):
        return _StaticPlanner(self._plan)


async def test_prepare_plan_is_advisory_and_never_creates_executor() -> None:
    plan = AgentPlan(
        intent=Intent.SEARCH,
        user_goal="Look up a movie clue",
        steps=[PlanStep(id="web", tool_name="WebSearch", arguments={"query": "Spielberg alien cargo"})],
    )
    coordinator = _CoordinatorWithPlanner(plan)
    prepared, executor, updated = await coordinator.prepare_plan(
        user_prompt="movie clue",
        intent=Intent.SEARCH,
        system_prompt_content="system",
        allowed_tool_names={"metadata_lookup", "web_search"},
        context='ACTIVE CATEGORY LIBRARY CONTEXT PACKET: {"category_id":"movie"}',
    )
    assert prepared is not None
    assert executor is None
    assert "STRUCTURED PLAN ADVISORY" in updated
    assert "not automatically executed" in updated


class _FlakyRegistry(ToolRegistry):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0
        async def flaky(**kwargs):
            self.calls += 1
            if self.calls == 1:
                return {"ok": False, "recoverable": True, "error_code": "TEMPORARY_NETWORK", "error": "temporary network timeout"}
            return {"ok": True, "value": "recovered"}
        self.register(
            "web_search",
            "search",
            {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
            flaky,
            intents={Intent.SEARCH},
        )


async def test_read_only_tool_retry_is_bounded() -> None:
    registry = _FlakyRegistry()
    executor = ToolCallExecutor(registry)
    message, summary = await executor.execute_tool_call(
        name="WebSearch",
        arguments_raw={"query": "x"},
        tool_call_id="tc1",
        allowed_tool_names={"web_search"},
        tool_context=ToolExecutionContext(session_id="test"),
    )
    assert registry.calls == 2
    assert message["name"] == "web_search"
    assert "recovered" in message["content"]
    assert "web_search" in summary


def test_no_lexical_overlap_guard_source_left() -> None:
    text = (ROOT / "src/ai/reasoning.py").read_text(encoding="utf-8")
    assert "_significant_tokens" not in text
    assert "PLAN_STOPWORDS" not in text
    assert "request_tokens" not in text
    assert "semantic matching" in text


def main() -> None:
    asyncio.run(test_planner_has_no_lexical_overlap_guard())
    asyncio.run(test_prepare_plan_is_advisory_and_never_creates_executor())
    asyncio.run(test_read_only_tool_retry_is_bounded())
    test_no_lexical_overlap_guard_source_left()
    print("Round 155 agent harness advisory plan tests passed")


if __name__ == "__main__":
    main()
