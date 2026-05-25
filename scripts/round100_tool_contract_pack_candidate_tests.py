"""Round 100 regression checks: tool contracts and TV pack candidate workflow.

This script is intentionally dependency-light.  It verifies that intent policy
allow-lists reference registered tool names, that dynamic category tools exist,
and that TV pack search schemas/candidate placeholders stay category-owned and
queueable without brittle planner paths.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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
from src.ai.plan_executor import PlanExecutor
from src.ai.tool_executor import ToolCallExecutor
from src.core.categories.registry import CategoryRegistry
from src.core.categories.tv import TvShowCategory
from src.core.domain_models.downloads import SearchResult
from src.core.models import Intent


class FakeAggregator:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def search(self, query: str, **_: object):
        self.queries.append(query)
        if "S05E01-E14" in query:
            return [
                SearchResult(
                    title="Yellowstone.S05E01-E14.ITA.ENG.1080p.WEB-DL.x265",
                    magnet="magnet:?xt=urn:btih:pack",
                    size="28.0 GB",
                    size_bytes=28 * 1024 ** 3,
                    seeders=42,
                    source="fake",
                    quality_score=0.95,
                )
            ]
        return []


class FakeMediaRepo:
    async def get_category_metadata(self, category_id: str, title: str):
        return [{
            "metadata": {
                "seasons": [
                    {"season_number": 1, "episode_count": 9},
                    {"season_number": 5, "episode_count": 14},
                ],
            }
        }]

    async def list_category_units(self, *args, **kwargs):
        return []

    async def get_item_progress(self, *args, **kwargs):
        return {"last_season": 5, "last_episode": 0}


class FakeDB:
    media = FakeMediaRepo()


async def test_tv_pack_queries_are_dynamic() -> None:
    tv = TvShowCategory()
    item = tv.create_item("Yellowstone", language="Italian")
    aggregator = FakeAggregator()
    context = SimpleNamespace(
        db=FakeDB(),
        aggregator=aggregator,
        pipeline=None,
        settings=SimpleNamespace(default_quality=None),
        metadata_enricher=None,
        metadata_clients={},
    )
    results, summary = await tv.search_agent_candidates(
        item,
        season=5,
        episode=None,
        language="Italian",
        search_scope="season_pack_preferred",
        context=context,
    )
    assert any("S05E01-E14" in q for q in aggregator.queries), aggregator.queries
    assert results and "S05E01-E14" in results[0].title
    assert "Season 5" in summary


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
    assert "search_media_torrents" in registered
    assert "queue_download" in registered
    assert "tv.download_season_pack" in registered


async def test_tool_aliases_and_missing_dependencies_are_safe() -> None:
    registry = AgentToolCatalog([WebToolProvider()]).build_registry()
    executor = ToolCallExecutor(registry)
    message, summary = await executor.execute_tool_call(
        "find_browser_links",
        {"purpose": "episodes"},
        "alias-call",
        {"browser_find_links"},
    )
    assert message["name"] == "browser_find_links"
    assert "browser_find_links" in summary

    message, summary = await executor.execute_tool_call(
        "browse_page",
        {"url": "https://example.com"},
        "browse-call",
        {"browse_page"},
    )
    assert "browser runtime" in message["content"].lower() or "unavailable" in message["content"].lower()


def test_plan_executor_search_result_aliases() -> None:
    executor = PlanExecutor(tool_executor=None, allowed_tool_names=set())  # type: ignore[arg-type]
    payload = {
        "search_scope": "season_pack_preferred",
        "results_total_size_gb": 28.0,
        "candidates": [
            {"candidate_id": "pack-1", "is_bundle": True, "size_bytes": 28 * 1024 ** 3},
            {"candidate_id": "ep-1", "is_bundle": False, "size_bytes": 2 * 1024 ** 3},
        ],
    }
    assert executor._extract_placeholder_path(payload, "results_total_size_gb") == 28.0
    assert executor._extract_placeholder_path(payload, "results[*].candidate_id") == ["pack-1"]


async def main() -> None:
    test_tool_policy_references_registered_tools()
    await test_tool_aliases_and_missing_dependencies_are_safe()
    test_plan_executor_search_result_aliases()
    await test_tv_pack_queries_are_dynamic()
    print("Round 100 tool contract and pack candidate tests passed")


if __name__ == "__main__":
    asyncio.run(main())
