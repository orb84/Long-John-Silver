#!/usr/bin/env python3
"""Round 94 episode-fact grounding regression checks.

The Discord incident showed that a follow-up such as "when was apisode 10
aired?" could lose the referenced show, query web generically, and then let the
model invent an air date. These tests keep the fix focused on the shared chat
architecture: bind follow-up metadata lookups to recent tracked context, require
episode-level metadata for episode facts, and expose deterministic date tense
support to every bridge through the common tool policy/prompt.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ai.plan_coordinator import PlanCoordinator
from src.ai.prompt_builder import PromptBuilder
from src.ai.tool_policy import AgentToolPolicy
from src.ai.tools.metadata_lookup_support import LibraryMetadataSnapshotLookup, MetadataLookupRequest
from src.ai.tools.research import DateComparisonTool, MetadataLookupTool
from src.core.models import Intent


class FakeToolExecutor:
    def get_definitions(self, allowed_tool_names: set[str]):
        return [
            {
                "type": "function",
                "function": {
                    "name": "metadata_lookup",
                    "description": "metadata lookup",
                    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "web search",
                    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
                },
            },
        ]


class FakeLLM:
    async def completion(self, **kwargs):
        plan = {
            "intent": "SEARCH",
            "user_goal": "Find the original air date of episode 10 of the TV show the user is referring to.",
            "constraints": {},
            "steps": [
                {
                    "id": "lookup_episode",
                    "tool_name": "metadata_lookup",
                    "arguments": {
                        "query": "episode 10 air date",
                        "media_type": "tv",
                        "include_episodes": False,
                    },
                    "depends_on": [],
                    "success_condition": "metadata result contains an air_date field for episode 10",
                },
                {
                    "id": "fallback_web_search",
                    "tool_name": "web_search",
                    "arguments": {"query": "episode 10 original air date", "max_results": 5},
                    "depends_on": ["lookup_episode"],
                    "success_condition": "search results include a reliable source listing the air date",
                },
            ],
        }
        return {"choices": [{"message": {"content": json.dumps(plan)}}]}


class FakeWebFirstLLM:
    async def completion(self, **kwargs):
        plan = {
            "intent": "SEARCH",
            "user_goal": "Find episode 10 original air date on the web.",
            "constraints": {},
            "steps": [
                {
                    "id": "fallback_public_lookup",
                    "tool_name": "web_search",
                    "arguments": {"query": "episode 10 original air date", "max_results": 5},
                    "depends_on": [],
                    "success_condition": "search result has the date",
                }
            ],
        }
        return {"choices": [{"message": {"content": json.dumps(plan)}}]}


async def test_followup_metadata_plan_is_bound_to_recent_show_and_episode() -> None:
    settings = SimpleNamespace(tracked_items=[SimpleNamespace(key="For All Mankind", language="Italian")])
    coordinator = PlanCoordinator(FakeToolExecutor(), FakeLLM(), settings=settings)
    context = """
ACTIVE CATEGORY LIBRARY CONTEXT PACKET:
{"category_id":"tv","matched_tracked_items":[{"key":"For All Mankind"}]}
RECENT CONVERSATION HISTORY:
USER: Can you please grab me the episodes I am missing from the latest season of for all mankind ?
ASSISTANT: The only missing piece is S05 E10.
"""
    plan, _executor, _prompt = await coordinator.prepare_plan(
        user_prompt="when was apisode 10 aired ?",
        intent=Intent.SEARCH,
        system_prompt_content="system",
        allowed_tool_names={"metadata_lookup", "web_search"},
        context=context,
    )
    assert plan is not None
    assert len(plan.steps) == 1, "dependent generic web fallback should be removed from deterministic pre-plan"
    step = plan.steps[0]
    assert step.tool_name == "metadata_lookup"
    assert step.arguments["query"] == "For All Mankind"
    assert step.arguments["media_type"] == "tv"
    assert step.arguments["question"] == "when was apisode 10 aired ?"
    assert step.arguments["season"] == 5
    assert step.arguments["episode"] == 10
    assert step.arguments["include_episodes"] is True


async def test_web_first_followup_gets_metadata_first_and_title_bound_query() -> None:
    settings = SimpleNamespace(tracked_items=[SimpleNamespace(key="For All Mankind", language="Italian")])
    coordinator = PlanCoordinator(FakeToolExecutor(), FakeWebFirstLLM(), settings=settings)
    context = """
ACTIVE CATEGORY LIBRARY CONTEXT PACKET:
{"category_id":"tv","matched_tracked_items":[{"key":"For All Mankind"}]}
RECENT CONVERSATION HISTORY:
ASSISTANT: The only missing piece is S05E10 for For All Mankind.
"""
    plan, _executor, _prompt = await coordinator.prepare_plan(
        user_prompt="when was apisode 10 aired ?",
        intent=Intent.SEARCH,
        system_prompt_content="system",
        allowed_tool_names={"metadata_lookup", "web_search"},
        context=context,
    )
    assert plan is not None
    assert plan.steps[0].tool_name == "metadata_lookup"
    assert plan.steps[0].arguments["query"] == "For All Mankind"
    assert plan.steps[1].tool_name == "web_search"
    assert plan.steps[1].arguments["query"].startswith("For All Mankind ")


def test_metadata_request_parses_episode_typo_and_requires_episode_data() -> None:
    request = MetadataLookupRequest.from_arguments({
        "query": "For All Mankind",
        "question": "when was apisode 10 aired ?",
        "media_type": "tv",
        "season": 5,
    })
    assert isinstance(request, MetadataLookupRequest)
    assert request.episode == 10
    assert request.include_episodes is True

    season_only = {"number_of_seasons": 5, "season_details": {"season_number": 5}}
    assert not LibraryMetadataSnapshotLookup.can_answer(season_only, request.question)

    with_episode = {
        "season_details": {
            "season_number": 5,
            "episodes": [{"episode_number": 10, "name": "This Land Is Our Land", "air_date": "2026-05-29"}],
        }
    }
    tool = MetadataLookupTool()
    assert tool._result_can_answer(with_episode, request)
    hints = MetadataLookupTool.requested_episode_hint(with_episode, 5, 10)
    assert hints and hints["air_date"] == "2026-05-29"


async def test_date_comparison_tool_flags_future_dates() -> None:
    tool = DateComparisonTool()
    result = await tool.execute({"date": "2999-01-01", "label": "test future"}, context=None)
    assert result["ok"] is True
    assert result["relation"] == "future"
    assert "scheduled" in result["tense_guidance"]


def test_prompt_and_tool_policy_expose_tense_safety_everywhere() -> None:
    prompt = PromptBuilder().build_system_prompt(Intent.SEARCH)
    assert "CURRENT RUNTIME DATETIME" in prompt
    assert "DATE FACT RULE" in prompt
    assert "compare_date_to_now" in prompt

    policy = AgentToolPolicy()
    assert "compare_date_to_now" in policy.allowed_tool_names(Intent.SEARCH)
    assert "compare_date_to_now" in policy.allowed_tool_names(Intent.CHAT)
    assert "compare_date_to_now" in policy.allowed_tool_names(Intent.DOWNLOAD)


def main() -> None:
    asyncio.run(test_followup_metadata_plan_is_bound_to_recent_show_and_episode())
    asyncio.run(test_web_first_followup_gets_metadata_first_and_title_bound_query())
    test_metadata_request_parses_episode_typo_and_requires_episode_data()
    asyncio.run(test_date_comparison_tool_flags_future_dates())
    test_prompt_and_tool_policy_expose_tense_safety_everywhere()
    print("PASS round94 episode fact grounding tests")


if __name__ == "__main__":
    main()
