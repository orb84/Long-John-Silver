#!/usr/bin/env python3
"""Round 92 regression checks.

Covers the Discord timeout/progress repair, the prompt-size regression caused by
injecting the full torrent guide, the multi-unit planning normalization that
prevents a guessed single episode from replacing category-owned fan-out, and the
library organize duplicate season/episode argument fix.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ai.plan_coordinator import PlanCoordinator
from src.core.models import AgentPlan, Intent, PlanStep



def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_discord_uses_progress_without_hard_request_timeout() -> None:
    src = read("src/web/discord_bridge.py")
    runner = read("src/ai/chat_session_runner.py")
    assert "_run_prompt_with_progress" in src
    assert "chat_runner.run_events" in src
    assert "format_progress_message" in runner
    assert "run_stream(" in runner
    assert "timeout=120" not in src
    assert "Discord request timed out" not in src
    assert "await bot.handle_interaction_prompt(interaction, prompt)" in src
    assert 'await bot.handle_interaction_prompt(interaction, f"download {query}")' in src


def test_download_prompt_uses_compact_quality_guide() -> None:
    src = read("src/ai/prompt_builder.py")
    assert "get_compact_quality_guide" in src
    assert "get_quality_guide()" not in src
    guide_src = read("src/utils/torrent_knowledge.py")
    assert "def get_compact_quality_guide" in guide_src
    assert "COMPACT_TORRENT_QUALITY_GUIDE" in guide_src


def test_multi_unit_download_plan_drops_single_episode_guess() -> None:
    plan = AgentPlan(
        intent=Intent.DOWNLOAD,
        user_goal="Grab the missing episodes from the latest season",
        constraints={"requested_unit_scope": "missing"},
        steps=[
            PlanStep(
                id="search_latest_guess",
                tool_name="search_media_torrents",
                arguments={
                    "name": "For All Mankind",
                    "season": 5,
                    "episode": 10,
                    "language": "Italian",
                },
                depends_on=[],
                success_condition="Candidates returned.",
            )
        ],
    )
    normalized = PlanCoordinator(tool_executor=None, llm_client=None)._normalize_download_plan(
        plan,
        "Please grab the episodes I am missing from the latest season",
        allowed_tool_names={"search_media_torrents"},
    )
    args = normalized.steps[0].arguments
    assert args["name"] == "For All Mankind"
    assert args["season"] == 5
    assert args["language"] == "Italian"
    assert "episode" not in args
    assert normalized.constraints["multi_unit_scope"] == "category_owned_fanout_without_single_unit_guess"


def test_category_organize_does_not_pass_season_episode_twice() -> None:
    src = read("src/core/categories/base.py")
    assert "if k not in {'item_name', 'season', 'episode'}" in src
    organize_block = src[src.index("    def organize("):src.index("    # ── Media verification", src.index("    def organize("))]
    assert "season=season" in organize_block
    assert "episode=episode or 0" in organize_block
    assert "**path_metadata" in organize_block


def main() -> None:
    tests = [
        test_discord_uses_progress_without_hard_request_timeout,
        test_download_prompt_uses_compact_quality_guide,
        test_multi_unit_download_plan_drops_single_episode_guess,
        test_category_organize_does_not_pass_season_episode_twice,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")


if __name__ == "__main__":
    main()
