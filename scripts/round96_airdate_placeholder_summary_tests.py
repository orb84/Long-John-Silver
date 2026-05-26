#!/usr/bin/env python3
"""Round 96 air-date placeholder and summary regression checks.

Round 95 made episode-date grounding much better, but a Discord follow-up still
failed because a local planner referenced ``results.episode.air_date`` instead
of the canonical ``answer_hints.requested_episode.air_date``.  The same log set
showed the conversation summarizer inventing a June 19 air date, which can later
poison follow-up context.  These tests keep both boundaries deterministic.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if "aiosqlite" not in sys.modules:
    sys.modules["aiosqlite"] = SimpleNamespace(Connection=object, Cursor=object)

from src.ai.plan_coordinator import PlanCoordinator
from src.ai.plan_executor import PlanExecutor
from src.ai.tools.research import MetadataLookupTool
from src.core.conversation import ConversationManager
from src.core.models import AgentPlan, Intent, PlanExecutionStep, PlanStep


class FakeToolExecutor:
    def get_definitions(self, allowed_tool_names: set[str]):
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": name,
                    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
                },
            }
            for name in sorted(allowed_tool_names)
        ]


class WhenWillEpisodePlanLLM:
    async def completion(self, **kwargs):
        plan = {
            "intent": "SEARCH",
            "user_goal": "Find when episode 10 of For All Mankind season 5 will air.",
            "constraints": {},
            "steps": [
                {
                    "id": "lookup_ep10",
                    "tool_name": "metadata_lookup",
                    "arguments": {
                        "query": "For All Mankind",
                        "media_type": "tv",
                        "season": 5,
                        "episode": 10,
                        "include_episodes": True,
                    },
                    "depends_on": [],
                    "success_condition": "episode air date returned",
                },
                {
                    "id": "compare_to_now",
                    "tool_name": "compare_date_to_now",
                    "arguments": {"date": "${lookup_ep10.results.episode.air_date}"},
                    "depends_on": ["lookup_ep10"],
                    "success_condition": "date compared to now",
                },
            ],
        }
        return {"choices": [{"message": {"content": json.dumps(plan)}}]}


def test_metadata_payload_exposes_requested_episode_aliases() -> None:
    payload = MetadataLookupTool._success_payload(
        request=SimpleNamespace(query="For All Mankind", media_type="tv", question="When will episode 10 air?", season=5, episode=10),
        services_tried=["tmdb"],
        results=[{}],
        best={
            "provider": "tmdb",
            "type": "tv",
            "title": "For All Mankind",
            "season_details": {
                "season_number": 5,
                "episodes": [
                    {"episode_number": 10, "name": "This Land Is Our Land", "air_date": "2026-05-29"},
                ],
            },
        },
    )
    assert payload["answer_hints"]["requested_episode"]["air_date"] == "2026-05-29"
    assert payload["requested_episode"]["title"] == "This Land Is Our Land"
    assert payload["episode"]["episode_number"] == 10


def test_bad_planner_episode_placeholder_resolves_to_requested_episode_hint() -> None:
    executor = PlanExecutor(tool_executor=SimpleNamespace(), allowed_tool_names={"metadata_lookup", "compare_date_to_now"})
    lookup_step = PlanStep(id="lookup_ep10", tool_name="metadata_lookup")
    executed = {
        "lookup_ep10": PlanExecutionStep(
            step=lookup_step,
            success=True,
            result={
                "role": "tool",
                "content": json.dumps({
                    "ok": True,
                    "answer_hints": {
                        "requested_episode": {
                            "season": 5,
                            "episode_number": 10,
                            "title": "This Land Is Our Land",
                            "air_date": "2026-05-29",
                        }
                    },
                }),
            },
        )
    }
    args = {"date": "${lookup_ep10.results.episode.air_date}"}
    compare_step = PlanStep(
        id="compare_to_now",
        tool_name="compare_date_to_now",
        arguments=args,
        depends_on=["lookup_ep10"],
    )
    error = executor._resolve_dynamic_arguments(args, compare_step, executed)
    assert error is None
    assert args["date"] == "2026-05-29"


async def test_when_will_episode_date_gets_official_crosscheck() -> None:
    settings = SimpleNamespace(tracked_items=[SimpleNamespace(key="For All Mankind", language="Italian")])
    coordinator = PlanCoordinator(FakeToolExecutor(), WhenWillEpisodePlanLLM(), settings=settings)
    context = """
ACTIVE CATEGORY LIBRARY CONTEXT PACKET:
{"category_id":"tv","matched_tracked_items":[{"key":"For All Mankind","language":"Italian"}]}
RECENT CONVERSATION HISTORY:
USER: Grab the missing episodes from the latest season of For All Mankind.
ASSISTANT: Episode 10 has not aired yet.
"""
    plan, _executor, _prompt = await coordinator.prepare_plan(
        user_prompt="When will episode 10 air ?",
        intent=Intent.SEARCH,
        system_prompt_content="system",
        allowed_tool_names={"metadata_lookup", "compare_date_to_now", "web_search"},
        context=context,
    )
    assert plan is not None
    assert any(step.tool_name == "web_search" for step in plan.steps)
    web = next(step for step in plan.steps if step.tool_name == "web_search")
    assert "For All Mankind" in web.arguments["query"]
    assert "episode 10" in web.arguments["query"]
    assert "Apple TV" in web.arguments["query"]


def test_conversation_summary_drops_dates_not_present_in_source() -> None:
    manager = ConversationManager(db=SimpleNamespace(system=SimpleNamespace()))
    source = "Assistant said S05E10 is expected on 28 mag 2026, but source confidence was limited."
    summary = "The finale is scheduled to air on June 19, 2026."
    cleaned = manager._sanitize_summary_against_source(summary, source)
    assert "June 19" not in cleaned
    assert "unsupported date" in cleaned.lower()


def main() -> None:
    test_metadata_payload_exposes_requested_episode_aliases()
    test_bad_planner_episode_placeholder_resolves_to_requested_episode_hint()
    asyncio.run(test_when_will_episode_date_gets_official_crosscheck())
    test_conversation_summary_drops_dates_not_present_in_source()
    print("PASS round96 airdate placeholder/summary tests")


if __name__ == "__main__":
    main()
