#!/usr/bin/env python3
"""Round 154 compatibility test.

Round 155 superseded the lexical plan guard.  This file now checks the stable
part of the Round 154 fix: bad structured plans must not be executed as an
authoritative pre-tool workflow.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ai.plan_coordinator import PlanCoordinator
from src.core.models import AgentPlan, Intent, PlanStep


def _tool_schema(name: str, props: dict | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"{name} tool",
            "parameters": {"type": "object", "properties": props or {}, "required": []},
        },
    }


class _DummyToolExecutor:
    def get_definitions(self, allowed_tool_names: set[str]) -> list[dict]:
        return [_tool_schema(name) for name in sorted(allowed_tool_names)]


class _StaticPlanner:
    def __init__(self, plan):
        self.plan = plan

    async def generate_plan(self, *args, **kwargs):
        return self.plan


class _CoordinatorWithStaticPlanner(PlanCoordinator):
    def __init__(self, plan):
        super().__init__(_DummyToolExecutor(), llm_client=None)
        self._plan = plan

    def create_planner(self):
        return _StaticPlanner(self._plan)


async def test_plan_is_advisory_not_executed() -> None:
    plan = AgentPlan(
        intent=Intent.SEARCH,
        user_goal="Find recent research papers on transformer models for image classification.",
        steps=[PlanStep(id="web", tool_name="WebSearch", arguments={"query": "transformer models image classification 2023"})],
    )
    coordinator = _CoordinatorWithStaticPlanner(plan)
    prepared, executor, updated = await coordinator.prepare_plan(
        user_prompt="I am looking for a movie from Spielberg made after the year 2000 and about kids and some alien cargo lost by the government ?",
        intent=Intent.SEARCH,
        system_prompt_content="system",
        allowed_tool_names={"metadata_lookup", "web_search"},
        context='ACTIVE CATEGORY LIBRARY CONTEXT PACKET: {"category_id":"movie"}',
    )
    assert prepared is not None
    assert executor is None
    assert "STRUCTURED PLAN ADVISORY" in updated
    assert "not automatically executed" in updated


def main() -> None:
    asyncio.run(test_plan_is_advisory_not_executed())
    print("Round 154 compatibility plan guard tests passed")


if __name__ == "__main__":
    main()
