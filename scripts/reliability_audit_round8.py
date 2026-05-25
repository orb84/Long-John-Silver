#!/usr/bin/env python3
"""Round 8 reliability audit.

Runs lightweight simulations/static checks for the exact failure classes that
regressed in the live app.  It avoids DB/network dependencies so it can run in
minimal CI/sandbox environments.
"""
from __future__ import annotations

from pathlib import Path
import asyncio
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ai.plan_coordinator import PlanCoordinator
from src.core.models import AgentPlan, Intent, PlanStep, QualityProfile, SearchResult, Settings, TvShowItem
from src.core.search_pipeline import SearchPipeline




class FakeAggregator:
    async def search(self, query, category=None, quality_profile=None, preferred_language=None):
        return [SearchResult(
            title="For.All.Mankind.S05E03.1080p.ITA.ENG.WEB-DL.H265.mkv",
            magnet="magnet:?xt=urn:btih:test",
            size="2.0 GB",
            size_bytes=2_000_000_000,
            seeders=42,
            source="fake",
            quality_score=0.9,
        )]


class RecursingRanker:
    async def select_best_for_category(self, **kwargs):
        raise RecursionError("simulated ranker regression")

class DummySettingsManager:
    def __init__(self, settings):
        self.settings = settings


class DummyRegistry:
    def get(self, *_):
        return None


class DummyToolExecutor:
    pass


def assert_search_quality_profile_no_recursion() -> None:
    settings = Settings(default_quality=QualityProfile(preferred_resolution="1080p"))
    pipeline = SearchPipeline(
        aggregator=None, downloader=None, db=None, librarian=None,
        category_registry=DummyRegistry(), settings_manager=DummySettingsManager(settings),
    )
    item = TvShowItem(key="For All Mankind", language="Italian", quality=QualityProfile(preferred_resolution="720p"))
    profile = pipeline._effective_quality_profile(item)
    assert profile is not None
    assert profile.preferred_resolution == "1080p"
    assert item.quality.preferred_resolution == "720p", "helper must not mutate tracked item quality"




async def assert_provider_results_survive_ranker_failure() -> None:
    settings = Settings(default_quality=QualityProfile(preferred_resolution="1080p"))
    pipeline = SearchPipeline(
        aggregator=FakeAggregator(), downloader=None, db=None, librarian=None,
        category_registry=DummyRegistry(), torrent_selection=RecursingRanker(),
        settings_manager=DummySettingsManager(settings),
    )
    item = TvShowItem(key="For All Mankind", language="Italian")
    results = await pipeline.run_search(item, "S05E03", mode="llm", language="Italian")
    assert isinstance(results, list) and results, "valid provider results must not be lost when ranking fails"
    assert results[0].title.startswith("For.All.Mankind.S05E03")

def assert_download_plan_rewrites_invented_tools() -> None:
    settings = Settings(tracked_items=[TvShowItem(key="For All Mankind", language="Italian")])
    coord = PlanCoordinator(DummyToolExecutor(), llm_client=None, settings=settings)
    bad_plan = AgentPlan(
        intent=Intent.DOWNLOAD,
        user_goal="Download remaining episodes from season 5 of For All Mankind",
        constraints={},
        steps=[
            PlanStep(id="find", tool_name="tv.find_missing_episodes", arguments={"item_id": "For All Mankind", "season": 5}),
            PlanStep(id="bad", tool_name="tv.download_missing_batch", arguments={"item_id": "For All Mankind", "episodes": []}, depends_on=["find"]),
        ],
    )
    normalized = coord._normalize_download_plan(
        bad_plan,
        "grab remaining episodes from season 5 of For All Mankind",
        {"search_media_torrents", "tv.find_missing_episodes", "queue_download"},
    )
    assert len(normalized.steps) == 1
    assert normalized.steps[0].tool_name == "search_media_torrents"
    assert normalized.steps[0].arguments["name"] == "For All Mankind"
    assert normalized.steps[0].arguments["season"] == 5
    assert normalized.steps[0].arguments["language"] == "Italian"


def assert_download_start_gate_not_substring_only() -> None:
    source = Path("src/core/downloader.py").read_text()
    assert "self._explicit_start_allowed" in source
    assert 'getattr(item, "id", None) in self._explicit_start_allowed' in source
    assert "any(marker in reason for marker in user_markers)" not in source


def assert_suggestions_api_reports_compiling() -> None:
    router = Path("src/web/routers/suggestions.py").read_text()
    ui = Path("src/web/static/js/components/suggestionsPanel.js").read_text()
    assert '"compiling": compiling' in router
    assert "data.compiling" in ui
    assert "setTimeout(() => this.load(), 2500)" in ui


def main() -> None:
    assert_search_quality_profile_no_recursion()
    asyncio.run(assert_provider_results_survive_ranker_failure())
    assert_download_plan_rewrites_invented_tools()
    assert_download_start_gate_not_substring_only()
    assert_suggestions_api_reports_compiling()
    print("round8 reliability audit passed")


if __name__ == "__main__":
    main()
