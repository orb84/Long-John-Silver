#!/usr/bin/env python3
"""Round 99 regression checks for pack-first agent flow and bridge status cadence."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ai.intent_router import IntentRouter
from src.ai.plan_coordinator import PlanCoordinator
from src.ai.plan_executor import PlanExecutor
from src.core.models import AgentPlan, Intent, PlanExecutionStep, PlanStep



class _NoopToolExecutor:
    pass


def _assert_scope_injected() -> None:
    plan = AgentPlan(
        intent=Intent.DOWNLOAD,
        user_goal="Grab the latest season pack for a show, preferring a full season pack",
        steps=[PlanStep(id="search", tool_name="search_media_torrents", arguments={"name": "Example", "season": 5})],
    )
    coord = PlanCoordinator(tool_executor=_NoopToolExecutor(), llm_client=None, settings=None)
    normalized = coord._normalize_download_plan(
        plan,
        "Grab the latest season in Italian please, prefer a season pack rather than single episodes",
        {"search_media_torrents"},
    )
    assert normalized.steps[0].arguments["search_scope"] == "season_pack_preferred"
    assert normalized.constraints["download_search_scope"] == "season_pack_preferred"

    whole_plan = AgentPlan(
        intent=Intent.DOWNLOAD,
        user_goal="Download the latest season of a show",
        steps=[PlanStep(id="search", tool_name="search_media_torrents", arguments={"name": "Example", "season": 5})],
    )
    whole_normalized = coord._normalize_download_plan(
        whole_plan,
        "Can you please download for me the latest season of Example in Italian?",
        {"search_media_torrents"},
    )
    assert whole_normalized.steps[0].arguments["search_scope"] == "season_pack_preferred"


def _assert_recent_untracked_title_repair() -> None:
    context = """
RECENT CONVERSATION HISTORY:
TOOL (search_media_torrents) query: Yellowstone Season 5
ASSISTANT: I found candidates for **Yellowstone Season 5**.
"""
    coord = PlanCoordinator(tool_executor=_NoopToolExecutor(), llm_client=None, settings=None)
    assert coord._recent_media_name_from_context(context) == "Yellowstone"


def _assert_seasons_placeholder_alias() -> None:
    executor = PlanExecutor(tool_executor=_NoopToolExecutor(), allowed_tool_names={"search_media_torrents"})
    dep = PlanExecutionStep(
        step=PlanStep(id="lookup", tool_name="metadata_lookup"),
        success=True,
        result={
            "role": "tool",
            "content": json.dumps({
                "result": {"seasons": [{"season_number": 1}, {"season_number": 5}]},
            }),
        },
    )
    step = PlanStep(
        id="search",
        tool_name="search_media_torrents",
        arguments={"season": "${lookup.result.seasons}"},
        depends_on=["lookup"],
    )
    args = dict(step.arguments)
    error = executor._resolve_dynamic_arguments(args, step, {"lookup": dep})
    assert error is None, error
    assert args["season"] == 5



def _assert_status_cadence_source() -> None:
    runner_src = Path("src/ai/chat_session_runner.py").read_text()
    assert "first_progress_seconds: float = 5.0" in runner_src
    assert "later_progress_seconds: float = 75.0" in runner_src
    assert "max_status_updates: int = 3" in runner_src
    assert "_next_progress_delay" in runner_src
    assert "tick < request.max_status_updates" in runner_src


def main() -> None:
    _assert_scope_injected()
    _assert_recent_untracked_title_repair()
    _assert_seasons_placeholder_alias()
    _assert_status_cadence_source()
    assert "season pack" in IntentRouter._ROUTING_PROMPT.lower()
    print("Round 99 pack-first agent-flow checks passed")


if __name__ == "__main__":
    main()
