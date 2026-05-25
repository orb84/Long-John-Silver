#!/usr/bin/env python3
"""Round 101 root-cause regression checks for agent/tool contracts.

These tests prevent another cycle of one-off placeholder patches by asserting
architectural invariants:

- Fresh DOWNLOAD discovery plans are canonicalized to one category-owned search
  step and do not execute LLM-invented dependency paths.
- Planner placeholder aliases for latest/current season resolve through the
  metadata contract extractor, regardless of guessed container prefix.
- Tool allow-lists and the runtime registry remain in sync.
- TV pack schemas derive the terminal episode from category metadata.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ai.plan_coordinator import PlanCoordinator
from src.ai.plan_executor import PlanExecutor
from src.ai.tool_catalog import AgentToolCatalog
from src.ai.tool_policy import AgentToolPolicy
from src.ai.tools.categories import CategoryToolProvider
from src.ai.tools.downloads import DownloadToolProvider
from src.ai.tools.library import LibraryToolProvider
from src.ai.tools.preferences import PreferencesToolProvider
from src.ai.tools.research import ResearchToolProvider
from src.ai.tools.scheduling import SchedulingToolProvider
from src.ai.tools.storage import StorageToolProvider
from src.ai.tools.web import WebToolProvider
from src.core.categories.registry import CategoryRegistry
from src.core.categories.tv import TvShowCategory
from src.core.domain_models.downloads import SearchResult
from src.core.models import AgentPlan, Intent, PlanExecutionStep, PlanStep


class _NoopToolExecutor:
    pass


def test_download_metadata_placeholder_chain_is_canonicalized() -> None:
    """The exact Round 100 crash shape must not reach PlanExecutor."""
    plan = AgentPlan(
        intent=Intent.DOWNLOAD,
        user_goal="Download the latest season of Yellowstone in Italian",
        constraints={"language": "Italian", "format": "season pack"},
        steps=[
            PlanStep(
                id="lookup_latest_season",
                tool_name="metadata_lookup",
                arguments={
                    "query": "Yellowstone",
                    "media_type": "tv",
                    "service": "tmdb",
                    "include_episodes": False,
                    "question": "What is the most recent season number of the TV series Yellowstone?",
                },
            ),
            PlanStep(
                id="search_latest_season",
                tool_name="search_media_torrents",
                arguments={
                    "name": "Yellowstone",
                    "season": "${lookup_latest_season.results.latest_season}",
                    "language": "Italian",
                    "search_scope": "season_pack_preferred",
                },
                depends_on=["lookup_latest_season"],
            ),
        ],
    )
    coord = PlanCoordinator(_NoopToolExecutor(), llm_client=None, settings=None)
    normalized = coord._normalize_download_plan(plan, "Hi, get me the latest season of Yellowstone in Italian", {"metadata_lookup", "search_media_torrents"})
    assert len(normalized.steps) == 1
    step = normalized.steps[0]
    assert step.tool_name == "search_media_torrents"
    assert step.depends_on == []
    assert step.arguments["name"] == "Yellowstone"
    assert step.arguments["language"] == "Italian"
    assert step.arguments["search_scope"] == "season_pack_preferred"
    assert "season" not in step.arguments, "latest season must be resolved by the TV category, not by a planner placeholder"
    assert "${" not in json.dumps(step.arguments)


def test_download_storage_placeholder_step_is_removed() -> None:
    """Storage preflight must not crash a successful candidate search."""
    plan = AgentPlan(
        intent=Intent.DOWNLOAD,
        user_goal="Download the entire latest season of Yellowstone in Italian",
        steps=[
            PlanStep(
                id="search_season",
                tool_name="search_media_torrents",
                arguments={"name": "Yellowstone", "season": 5, "language": "Italian", "search_scope": "season_pack_preferred"},
            ),
            PlanStep(
                id="check_storage",
                tool_name="check_storage_capacity",
                arguments={"category_id": "tv", "estimated_gb": "${search_season.results_total_size_gb}"},
                depends_on=["search_season"],
            ),
        ],
    )
    coord = PlanCoordinator(_NoopToolExecutor(), llm_client=None, settings=None)
    normalized = coord._normalize_download_plan(plan, "download the whole season please", {"search_media_torrents", "check_storage_capacity"})
    assert [step.tool_name for step in normalized.steps] == ["search_media_torrents"]
    assert normalized.steps[0].arguments["season"] == 5


def test_latest_season_placeholder_aliases_are_contract_based() -> None:
    executor = PlanExecutor(tool_executor=_NoopToolExecutor(), allowed_tool_names={"search_media_torrents"})
    dep = PlanExecutionStep(
        step=PlanStep(id="lookup", tool_name="metadata_lookup"),
        success=True,
        result={
            "role": "tool",
            "content": json.dumps({
                "answer_hints": {"latest_season": 5},
                "results": [{"seasons": [{"season_number": 1}, {"season_number": 5}]}],
            }),
        },
    )
    for placeholder in (
        "${lookup.latest_season}",
        "${lookup.results.latest_season}",
        "${lookup.result.latest_season}",
        "${lookup.best.current_season}",
    ):
        step = PlanStep(
            id="search",
            tool_name="search_media_torrents",
            arguments={"name": "Yellowstone", "season": placeholder},
            depends_on=["lookup"],
        )
        args = dict(step.arguments)
        error = executor._resolve_dynamic_arguments(args, step, {"lookup": dep})
        assert error is None, f"{placeholder}: {error}"
        assert args["season"] == 5


def test_tool_policy_references_registered_tools() -> None:
    registry = CategoryRegistry.with_defaults()
    tool_registry = AgentToolCatalog([
        CategoryToolProvider(category_registry=registry),
        DownloadToolProvider(),
        LibraryToolProvider(category_registry=registry),
        PreferencesToolProvider(),
        ResearchToolProvider(),
        SchedulingToolProvider(),
        StorageToolProvider(),
        WebToolProvider(),
    ]).build_registry()
    registered = tool_registry.get_tool_names()
    policy = AgentToolPolicy()
    for intent in Intent:
        allowed = policy.allowed_tool_names(intent, category=registry.get("tv"), confirmed=True)
        missing = sorted(name for name in allowed if name not in registered)
        assert not missing, f"{intent}: missing registered tools {missing}"


class _FakeAggregator:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def search(self, query: str, **_: object):
        self.queries.append(query)
        if "S05E01-E14" in query:
            return [SearchResult(
                title="Yellowstone.S05E01-E14.ITA.ENG.1080p.WEB-DL.x265",
                magnet="magnet:?xt=urn:btih:pack",
                size="28.0 GB",
                size_bytes=28 * 1024 ** 3,
                seeders=42,
                source="fake",
                quality_score=0.95,
            )]
        return []


class _FakeMediaRepo:
    async def get_category_metadata(self, category_id: str, title: str):
        return [{"metadata": {"seasons": [{"season_number": 5, "episode_count": 14}]}}]

    async def list_category_units(self, *args, **kwargs):
        return []

    async def get_item_progress(self, *args, **kwargs):
        return {"last_season": 5, "last_episode": 0}


class _FakeDB:
    media = _FakeMediaRepo()


async def test_tv_pack_range_uses_metadata_episode_count() -> None:
    tv = TvShowCategory()
    item = tv.create_item("Yellowstone", language="Italian")
    aggregator = _FakeAggregator()
    context = SimpleNamespace(
        db=_FakeDB(),
        aggregator=aggregator,
        pipeline=None,
        settings=SimpleNamespace(default_quality=None),
        metadata_enricher=None,
        metadata_clients={},
    )
    results, _summary = await tv.search_agent_candidates(
        item,
        season=5,
        episode=None,
        language="Italian",
        search_scope="season_pack_preferred",
        context=context,
    )
    assert any("S05E01-E14" in query for query in aggregator.queries), aggregator.queries
    assert results and "S05E01-E14" in results[0].title


async def main() -> None:
    test_download_metadata_placeholder_chain_is_canonicalized()
    test_download_storage_placeholder_step_is_removed()
    test_latest_season_placeholder_aliases_are_contract_based()
    test_tool_policy_references_registered_tools()
    await test_tv_pack_range_uses_metadata_episode_count()
    print("Round 101 agent contract hardening tests passed")


if __name__ == "__main__":
    asyncio.run(main())
