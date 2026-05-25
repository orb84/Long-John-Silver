"""Round 85 end-to-end intent-flow traces.

These checks protect the project rule that ordinary LLM download handling uses
few generic tools. Categories provide context, descriptors, ranking hooks, and
UI/internal actions; the LLM does not receive dozens of category-specific
micro-tools for prompts such as "download missing episodes from the latest
season".
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.ai.plan_coordinator import PlanCoordinator
from src.ai.prompt_builder import PromptBuilder
from src.ai.streaming_agent_loop import StreamingAgentLoopExecutor
from src.ai.tool_policy import AgentToolPolicy
from src.core.categories.tv import TvShowCategory
from src.core.models import (
    AgentPlan,
    Intent,
    PlanExecutionResult,
    PlanExecutionStep,
    PlanStep,
    Settings,
    TvShowItem,
)


EXACT_PROMPT = "Hi ! Can you please grab me the episodes i am missing from the latest season of For All Mankind ?"


class _DummyToolExecutor:
    def get_definitions(self, allowed_tool_names: set[str]) -> list[dict]:
        return []


class _DummyPlanner:
    def __init__(self, plan: AgentPlan) -> None:
        self._plan = plan

    async def generate_plan(self, *args: Any, **kwargs: Any) -> AgentPlan:
        return self._plan


class _PlanCoordinatorWithDummyPlanner(PlanCoordinator):
    def __init__(self, plan: AgentPlan, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._dummy_plan = plan

    def create_planner(self) -> _DummyPlanner:
        return _DummyPlanner(self._dummy_plan)


class _ExplodingPlanExecutor:
    async def execute(self, plan: AgentPlan) -> PlanExecutionResult:  # pragma: no cover - should not run
        raise AssertionError("Round 85 must not auto-queue batch recommendations before the LLM decides")


def _settings() -> Settings:
    settings = Settings()
    settings.tracked_items.append(
        TvShowItem(
            key="For All Mankind",
            display_name="For All Mankind",
            language="Italian",
            last_season=5,
            last_episode=10,
        )
    )
    return settings


def _unsafe_micro_tool_plan() -> AgentPlan:
    return AgentPlan(
        intent=Intent.DOWNLOAD,
        user_goal="Download missing episodes from the latest season of For All Mankind in Italian.",
        constraints={"language": "Italian"},
        steps=[
            PlanStep(
                id="find_missing",
                tool_name="tv.find_missing_episodes",
                arguments={"item_id": "For All Mankind", "season": "latest"},
            ),
            PlanStep(
                id="download_batch",
                tool_name="tv.download_missing_batch",
                arguments={"item_id": "For All Mankind", "episodes": "${find_missing.missing_episodes}"},
                depends_on=["find_missing"],
            ),
        ],
    )


def assert_download_tool_surface_is_small_and_generic() -> None:
    policy = AgentToolPolicy()
    category = TvShowCategory()
    names = policy.allowed_tool_names(Intent.DOWNLOAD, category=category)
    required = {"enquire_about_media", "search_media_torrents", "queue_download", "metadata_lookup"}
    assert required <= names, names
    forbidden_prefixes = ("tv.", "movie.", "books.", "games.")
    forbidden = sorted(name for name in names if name.startswith(forbidden_prefixes))
    assert forbidden == [], f"DOWNLOAD intent exposed category micro-tools: {forbidden}"


def assert_download_prompt_guidance_documents_generic_chain() -> None:
    prompt = PromptBuilder().build_system_prompt(Intent.DOWNLOAD, category_guidance=TvShowCategory().build_prompt_guidance("download"))
    assert "search_media_torrents" in prompt
    assert "queue_download" in prompt
    assert "enquire_about_media" in prompt
    assert "TOOL PHILOSOPHY" in prompt
    assert "tv.find_missing_episodes" not in prompt
    assert "tv.download_missing_batch" not in prompt
    assert "audio_languages" in prompt or "existing language" in prompt.lower()


def assert_tv_context_exposes_missing_and_language_policy() -> None:
    category = TvShowCategory()
    canonical = {
        "category_id": "tv",
        "item_id": "For All Mankind",
        "display_name": "For All Mankind",
        "seasons": [
            {"season": 5, "episodes": [
                {"episode": 1, "quality": "1080p", "audio_languages": ["Italian", "English"]},
                {"episode": 2, "quality": "1080p", "audio_languages": ["Italian"]},
            ]}
        ],
        "computed": {
            "local_episode_keys": ["S05E01", "S05E02"],
            "missing_episodes": [
                {"season": 5, "episode": 3, "air_date": "2026-05-01"},
                {"season": 5, "episode": 4, "air_date": "2026-05-08"},
            ],
            "missing_episode_count": 2,
            "audio_languages": ["Italian", "English"],
        },
    }
    summary = category.summarize_library_object_for_llm(canonical)
    assert summary["missing_episode_count"] == 2
    assert summary["missing_episodes"][0]["episode"] == 3
    assert summary["seasons"][0]["downloaded_episodes"][0]["audio_languages"] == ["Italian", "English"]

    profile = category._language_profile_for_llm([
        {"season": 5, "episode": 1, "audio_languages": ["Italian", "English"], "subtitle_languages": ["Italian"]},
        {"season": 5, "episode": 2, "audio_languages": ["Italian"], "subtitle_languages": []},
        {"season": 5, "episode": 3, "audio_languages": ["English"], "subtitle_languages": ["English"]},
    ], configured_language="Italian")
    assert profile["configured_language"] == "Italian"
    assert "Italian" in profile["existing_audio_languages"]
    assert profile["audio_languages_by_season"]["5"] == ["Italian", "English"]
    assert profile["episodes_with_non_preferred_audio"][0]["episode"] == 3
    assert any("Multi-audio" in rule for rule in profile["rules_for_llm"])


def assert_unsafe_micro_tool_plan_normalizes_to_generic_search() -> None:
    coordinator = PlanCoordinator(_DummyToolExecutor(), llm_client=None, settings=_settings())
    allowed = AgentToolPolicy().allowed_tool_names(Intent.DOWNLOAD, category=TvShowCategory())
    normalized = coordinator._normalize_download_plan(_unsafe_micro_tool_plan(), EXACT_PROMPT, allowed)
    assert [step.tool_name for step in normalized.steps] == ["search_media_torrents"], normalized.steps
    args = normalized.steps[0].arguments
    assert args["name"] == "For All Mankind"
    assert "season" not in args
    assert args["language"] == "Italian"


async def assert_prepare_plan_path_uses_generic_tools_only() -> None:
    allowed = AgentToolPolicy().allowed_tool_names(Intent.DOWNLOAD, category=TvShowCategory())
    coordinator = _PlanCoordinatorWithDummyPlanner(
        _unsafe_micro_tool_plan(),
        _DummyToolExecutor(),
        llm_client=None,
        settings=_settings(),
    )
    plan, executor, updated = await coordinator.prepare_plan(
        user_prompt=EXACT_PROMPT,
        intent=Intent.DOWNLOAD,
        system_prompt_content="system",
        allowed_tool_names=allowed,
        context="ACTIVE CATEGORY LIBRARY CONTEXT PACKET: For All Mankind language Italian missing S05E03 S05E04",
    )
    assert plan is not None
    assert executor is not None
    assert [step.tool_name for step in plan.steps] == ["search_media_torrents"]
    assert plan.steps[0].arguments["language"] == "Italian"
    assert "tv.find_missing_episodes" not in updated
    assert "Goal:" in updated


async def assert_batch_recommendation_is_left_for_llm_not_auto_queued() -> None:
    plan = AgentPlan(
        intent=Intent.DOWNLOAD,
        user_goal="Download missing episodes from For All Mankind season 5",
        steps=[PlanStep(id="search", tool_name="search_media_torrents", arguments={"name": "For All Mankind", "season": 5})],
    )
    payload = {
        "name": "For All Mankind",
        "batch_recommendation": {
            "queue_download_arguments": {
                "name": "For All Mankind",
                "result_set_id": "rs_1",
                "candidate_ids": ["cand_1", "cand_2"],
            }
        },
    }
    result = PlanExecutionResult(
        plan=plan,
        all_successful=True,
        steps=[PlanExecutionStep(
            step=plan.steps[0],
            success=True,
            result={"role": "tool", "name": "search_media_torrents", "content": json.dumps(payload)},
        )],
    )
    message = await StreamingAgentLoopExecutor._maybe_auto_queue_batch_recommendation(
        plan,
        _ExplodingPlanExecutor(),
        result,
        messages=[],
        error_presenter=None,  # type: ignore[arg-type]
        chat_presenter=None,
    )
    assert message is None


def assert_no_category_micro_tool_mentions_in_active_llm_guidance_files() -> None:
    active_files = [
        Path("src/ai/reasoning.py"),
        Path("src/ai/prompt_builder.py"),
    ]
    forbidden = ["tv.find_missing_episodes", "tv.download_missing_batch", "tv.download_next_missing_episode"]
    for path in active_files:
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{path} still guides the LLM toward {token}"

    tv_guidance = TvShowCategory().build_prompt_guidance("download")
    for token in forbidden:
        assert token not in tv_guidance, f"TV download prompt guidance still exposes {token}"


def main() -> None:
    assert_download_tool_surface_is_small_and_generic()
    assert_download_prompt_guidance_documents_generic_chain()
    assert_tv_context_exposes_missing_and_language_policy()
    assert_unsafe_micro_tool_plan_normalizes_to_generic_search()
    asyncio.run(assert_prepare_plan_path_uses_generic_tools_only())
    asyncio.run(assert_batch_recommendation_is_left_for_llm_not_auto_queued())
    assert_no_category_micro_tool_mentions_in_active_llm_guidance_files()
    print("Round 85 end-to-end intent flow tests passed")


if __name__ == "__main__":
    main()
