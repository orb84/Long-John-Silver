#!/usr/bin/env python3
"""Round 181 regressions for TV exact-episode search query fallback."""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.categories.tv import TvShowCategory
from src.core.models import SearchResult, QualityProfile
from src.core.search_pipeline import SearchPipeline


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class FakeAggregator:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    async def search(self, query: str, category: str | None = None, quality_profile=None, preferred_language: str | None = None):
        self.calls.append((query, preferred_language))
        if query == "For All Mankind S05E10":
            return [SearchResult(
                title="For All Mankind S05E10 1080p WEB h264 GRACE",
                magnet="magnet:?xt=urn:btih:round181",
                seeders=25,
                quality_score=0.95,
            )]
        return []




class PackOnlyAggregator:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    async def search(self, query: str, category: str | None = None, quality_profile=None, preferred_language: str | None = None):
        self.calls.append((query, preferred_language))
        if query == "For All Mankind S05":
            return [SearchResult(
                title="For All Mankind S05 1080p WEB-DL Complete",
                magnet="magnet:?xt=urn:btih:round181pack",
                seeders=40,
                size="20 GB",
                quality_score=0.92,
            )]
        return []


class FakeCategoryRegistry:
    def __init__(self, category) -> None:
        self.category = category

    def get(self, category_id: str):
        return self.category if category_id == self.category.category_id else None


class FakeDownloader: ...
class FakeDb: ...
class FakeLibrarian: ...


def test_tv_builds_bare_episode_before_language_tagged_variants() -> None:
    category = TvShowCategory()
    item = category.create_item("For All Mankind", language="Italian")
    primary = category.build_search_query(item, "S05E10", "Italian")
    alternatives = category.build_alternative_search_queries(item, "S05E10", "Italian")
    require(primary == "For All Mankind S05E10", "TV primary episode query should be bare exact SxxEyy, not ITA-suffixed")
    require("For All Mankind S05E10" in alternatives, "TV alternatives should retain the bare exact episode query")
    require("For.All.Mankind.S05E10" in alternatives, "TV alternatives should include dotted release-name form")
    require(any(q.endswith("ITA") for q in alternatives), "TV alternatives may still include language-tagged variants later in the ladder")
    require(alternatives.index("For All Mankind S05E10") < next(i for i, q in enumerate(alternatives) if q.endswith("ITA")), "bare exact query must be tried before ITA-tagged variants")


def test_search_pipeline_tries_category_alternatives_after_empty_primary() -> None:
    class StrictFirstTv(TvShowCategory):
        def build_search_query(self, item, unit_label, language):
            return self._append_search_language(f"{item.key} {unit_label}", language)

    category = StrictFirstTv()
    item = category.create_item("For All Mankind", language="Italian")
    aggregator = FakeAggregator()
    pipeline = SearchPipeline(
        aggregator=aggregator,
        downloader=FakeDownloader(),
        db=FakeDb(),
        librarian=FakeLibrarian(),
        category_registry=FakeCategoryRegistry(category),
        settings_manager=SimpleNamespace(settings=SimpleNamespace(language="Italian")),
    )
    result = asyncio.run(pipeline.run_search(item, "S05E10", mode="auto", language="Italian"))
    queries = [query for query, _ in aggregator.calls]
    require(queries[0] == "For All Mankind S05E10 ITA", "test setup should start with the old strict language query")
    require("For All Mankind S05E10" in queries[1:], "pipeline must try TV bare alternatives after an empty primary result")
    require(result is not None and result.title.startswith("For All Mankind S05E10"), "bare alternative result should be accepted")


def test_tv_exact_episode_validator_rejects_wrong_episode() -> None:
    category = TvShowCategory()
    item = category.create_item("For All Mankind", language="Italian")
    wrong = SearchResult(title="For All Mankind S05E09 1080p WEB", magnet="magnet:?xt=urn:btih:wrong", quality_score=0.95)
    right = SearchResult(title="For All Mankind 5x10 1080p WEB", magnet="magnet:?xt=urn:btih:right", quality_score=0.95)
    require(not category.validate_search_result_for_request(wrong, item, "S05E10"), "TV validator must reject the wrong SxxEyy")
    require(category.validate_search_result_for_request(right, item, "S05E10"), "TV validator should accept 5x10 as S05E10")


def test_tv_episode_can_be_satisfied_by_season_pack_candidate() -> None:
    category = TvShowCategory()
    item = category.create_item("For All Mankind", language="Italian")
    pack = SearchResult(
        title="For All Mankind S05 1080p WEB-DL Complete",
        magnet="magnet:?xt=urn:btih:pack",
        quality_score=0.92,
        size="20 GB",
    )
    require(category.validate_search_result_for_request(pack, item, "S05E10"), "TV exact episode search should accept a season pack that can contain S05E10")
    context = category.torrent_bundle_candidate_context(pack, item=item, unit_label="S05E10")
    require(bool(context and context.get("is_bundle")), "TV season-pack context should mark the candidate as a bundle")
    require(context.get("contains_requested_unit") is True, "bundle context should say that S05E10 can be contained")
    require(context.get("selective_download_required") is True, "episode-from-pack downloads must be marked for selective file download")


def test_search_pipeline_falls_back_to_tv_season_pack_when_episode_missing() -> None:
    category = TvShowCategory()
    item = category.create_item("For All Mankind", language="Italian")
    aggregator = PackOnlyAggregator()
    pipeline = SearchPipeline(
        aggregator=aggregator,
        downloader=FakeDownloader(),
        db=FakeDb(),
        librarian=FakeLibrarian(),
        category_registry=FakeCategoryRegistry(category),
        settings_manager=SimpleNamespace(settings=SimpleNamespace(language="Italian")),
    )
    result = asyncio.run(pipeline.run_search(item, "S05E10", mode="auto", language="Italian"))
    queries = [query for query, _ in aggregator.calls]
    require("For All Mankind S05" in queries, "TV search ladder must try the season-pack query after exact episode queries")
    require(result is not None and "S05" in result.title and "Complete" in result.title, "season-pack fallback should be accepted as a candidate for S05E10")


if __name__ == "__main__":
    test_tv_builds_bare_episode_before_language_tagged_variants()
    test_search_pipeline_tries_category_alternatives_after_empty_primary()
    test_tv_exact_episode_validator_rejects_wrong_episode()
    test_tv_episode_can_be_satisfied_by_season_pack_candidate()
    test_search_pipeline_falls_back_to_tv_season_pack_when_episode_missing()
    print("round181 TV episode query ladder tests passed")
