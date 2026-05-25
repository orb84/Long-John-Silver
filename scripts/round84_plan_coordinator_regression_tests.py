"""Round 84 regression traces for DOWNLOAD plan normalization.

The user hit a websocket chat crash because PlanCoordinator referenced a
missing private method on every DOWNLOAD plan path.  These checks exercise the
same shape of plan from the logs and add a small static audit so future edits
cannot leave dangling private PlanCoordinator method calls unnoticed.
"""

from __future__ import annotations

import ast
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.ai.plan_coordinator import PlanCoordinator
from src.core.categories.tv import TvShowCategory
from src.core.models import AgentPlan, Intent, PlanStep, Settings, TvShowItem


class _DummyToolExecutor:
    def get_definitions(self, allowed_tool_names: set[str]) -> list[dict]:
        return []


class _DummyPlanner:
    def __init__(self, plan: AgentPlan) -> None:
        self._plan = plan

    async def generate_plan(self, *args, **kwargs) -> AgentPlan:
        return self._plan


class _PlanCoordinatorWithDummyPlanner(PlanCoordinator):
    def __init__(self, plan: AgentPlan, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._dummy_plan = plan

    def create_planner(self):
        return _DummyPlanner(self._dummy_plan)


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


def _coordinator() -> PlanCoordinator:
    return PlanCoordinator(_DummyToolExecutor(), llm_client=None, settings=_settings())


def _log_plan() -> AgentPlan:
    return AgentPlan(
        intent=Intent.DOWNLOAD,
        user_goal='Download all missing episodes from the latest season of the tracked show "For All Mankind" (Italian language).',
        constraints={"language": "Italian", "resolution": "1080p"},
        steps=[
            PlanStep(
                id="lookup_show",
                tool_name="metadata_lookup",
                arguments={"query": "For All Mankind", "media_type": "tv", "service": "auto", "include_episodes": False},
                depends_on=[],
                success_condition="Metadata for the show is retrieved.",
            ),
            PlanStep(
                id="find_missing",
                tool_name="tv.find_missing_episodes",
                arguments={"item_id": "For All Mankind", "season": "${lookup_show.results.seasons[-1].season_number}"},
                depends_on=["lookup_show"],
                success_condition="Missing episodes are returned.",
            ),
            PlanStep(
                id="download_batch",
                tool_name="tv.download_missing_batch",
                arguments={"item_id": "For All Mankind", "episodes": "${find_missing.results.missing_episodes}"},
                depends_on=["find_missing"],
                success_condition="Missing episodes are queued.",
            ),
        ],
    )


def assert_direct_category_download_guard_exists_and_is_category_neutral() -> None:
    coord = _coordinator()
    guard = getattr(coord, "_looks_like_direct_category_download_tool")
    assert callable(guard)
    assert guard("tv.download_missing_batch") is True
    assert guard("movie.download_movie") is True
    assert guard("books.import_volume") is True
    assert guard("games.queue_patch") is True
    assert guard("tv.find_missing_episodes") is False
    assert guard("metadata_lookup") is False
    assert guard("search_media_torrents") is False


def assert_log_plan_normalizes_without_attribute_error() -> None:
    coord = _coordinator()
    normalized = coord._normalize_download_plan(
        _log_plan(),
        "Hi ! Can you please grab me the episodes i am missing from the latest season of For All Mankind ?",
        allowed_tool_names={
            "metadata_lookup",
            "tv.find_missing_episodes",
            "tv.download_missing_batch",
            "search_media_torrents",
            "queue_download",
        },
    )
    assert len(normalized.steps) == 1, normalized.steps
    step = normalized.steps[0]
    assert step.tool_name == "search_media_torrents", step
    assert step.arguments["name"] == "For All Mankind", step.arguments
    assert step.arguments["language"] == "Italian", step.arguments
    assert "season" not in step.arguments, step.arguments


async def assert_prepare_plan_websocket_path_does_not_raise_attribute_error() -> None:
    coord = _PlanCoordinatorWithDummyPlanner(
        _log_plan(),
        _DummyToolExecutor(),
        llm_client=None,
        settings=_settings(),
    )
    plan, executor, updated_prompt = await coord.prepare_plan(
        user_prompt="Hi ! Can you please grab me the episodes i am missing from the latest season of For All Mankind ?",
        intent=Intent.DOWNLOAD,
        system_prompt_content="system",
        allowed_tool_names={
            "metadata_lookup",
            "tv.find_missing_episodes",
            "tv.download_missing_batch",
            "search_media_torrents",
            "queue_download",
        },
        context=None,
    )
    assert plan is not None
    assert executor is not None
    assert plan.steps[0].tool_name == "search_media_torrents", plan.steps
    assert "season" not in plan.steps[0].arguments, plan.steps[0].arguments
    assert "Goal:" in updated_prompt


def assert_search_plan_drops_unresolved_queue_step() -> None:
    coord = _coordinator()
    plan = AgentPlan(
        intent=Intent.DOWNLOAD,
        user_goal="Download For All Mankind season 5",
        steps=[
            PlanStep(
                id="search_candidates",
                tool_name="search_media_torrents",
                arguments={"name": "For All Mankind", "season": 5},
            ),
            PlanStep(
                id="queue_candidates",
                tool_name="queue_download",
                arguments={"candidate_ids": "${search_candidates.candidate_ids}"},
                depends_on=["search_candidates"],
            ),
        ],
    )
    normalized = coord._normalize_download_plan(
        plan,
        "download For All Mankind season 5",
        allowed_tool_names={"search_media_torrents", "queue_download"},
    )
    assert [step.tool_name for step in normalized.steps] == ["search_media_torrents"], normalized.steps




def assert_tv_scanner_recovers_legacy_s_dot_episode_layout_from_logs() -> None:
    show_dir = Path("/library/Series/Babylon 5")
    file_path = show_dir / "Season 1" / "Babylon 5 s1.08.avi"
    season, episode = TvShowCategory()._infer_episode_coordinates_from_path(file_path, show_dir)
    assert (season, episode) == (1, 8), (season, episode)

    false_quality = show_dir / "Season 1" / "Babylon 5 S1 720p sample.avi"
    season, episode = TvShowCategory()._infer_episode_coordinates_from_path(false_quality, show_dir)
    assert episode is None, (season, episode)


def assert_plan_coordinator_has_no_missing_private_self_calls() -> None:
    source_path = Path("src/ai/plan_coordinator.py")
    tree = ast.parse(source_path.read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PlanCoordinator")
    methods = {node.name for node in cls.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    self_calls: set[str] = set()
    for node in ast.walk(cls):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
            and node.func.attr.startswith("_")
        ):
            self_calls.add(node.func.attr)
    missing = sorted(self_calls - methods)
    assert not missing, f"PlanCoordinator has dangling private self-call(s): {missing}"


def main() -> None:
    assert_direct_category_download_guard_exists_and_is_category_neutral()
    assert_log_plan_normalizes_without_attribute_error()
    asyncio.run(assert_prepare_plan_websocket_path_does_not_raise_attribute_error())
    assert_search_plan_drops_unresolved_queue_step()
    assert_tv_scanner_recovers_legacy_s_dot_episode_layout_from_logs()
    assert_plan_coordinator_has_no_missing_private_self_calls()
    print("Round 84 plan coordinator regression tests passed")


if __name__ == "__main__":
    main()
