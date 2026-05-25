#!/usr/bin/env python3
"""Round 79 scenario-trace tests.

These checks deliberately simulate user-facing workflows without requiring a
real torrent engine, libtorrent session, or database.  They walk the same code
seams used in production: category validation, descriptor creation, bundle
context, generic selective-download configuration, and quality gating.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

# BundleDownloadHandler imports libtorrent lazily.  The scenario fake does not
# need the real bindings, only a module that allows the import to succeed.
sys.modules.setdefault("libtorrent", SimpleNamespace())

from src.core.bundle_download import BundleDownloadHandler
from src.core.categories.movie import MovieCategory
from src.core.categories.tv import TvShowCategory
from src.core.categories.tv_bundle import TVBundleKnowledge
from src.core.models import QualityProfile, SearchResult, SizeLimitMode
from src.core.smart_quality import SmartQualityInferrer


@dataclass
class FakeFile:
    path: str
    size: int = 1024


class FakeFiles:
    def __init__(self, paths: list[str]) -> None:
        self._files = [FakeFile(path) for path in paths]

    def file_path(self, index: int) -> str:
        return self._files[index].path

    def at(self, index: int) -> FakeFile:
        return self._files[index]


class FakeTorrentInfo:
    def __init__(self, paths: list[str]) -> None:
        self._files = FakeFiles(paths)

    def num_files(self) -> int:
        return len(self._files._files)

    def files(self) -> FakeFiles:
        return self._files


class FakeHandle:
    def __init__(self, paths: list[str], *, has_metadata: bool = True) -> None:
        self._info = FakeTorrentInfo(paths)
        self._has_metadata = has_metadata
        self.priorities: list[int] | None = None

    def has_metadata(self) -> bool:
        return self._has_metadata

    def torrent_file(self) -> FakeTorrentInfo:
        return self._info

    def prioritize_files(self, priorities: list[int]) -> None:
        self.priorities = list(priorities)


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def selected_paths(paths: list[str], priorities: list[int] | None) -> list[str]:
    assert priorities is not None, "expected fake handle priorities to be assigned"
    return [path for path, priority in zip(paths, priorities) if priority > 0]


async def scenario_specific_episode_inside_multi_season_pack() -> dict[str, Any]:
    tv = TvShowCategory()
    handler = BundleDownloadHandler()
    item = SimpleNamespace(key="Example Show", display_name="Example Show", last_season=None, last_episode=None)
    result = SearchResult(title="Example.Show.S01-S03.Complete.1080p.WEB-DL", magnet="magnet:?xt=urn:btih:tv-range")

    assert_true(tv.validate_search_result_for_request(result, item, "S02E05"), "S02E05 should accept a containing S01-S03 bundle")
    descriptor = tv.unit_descriptor_from_search_result(result, item, "S02E05")
    assert_true(descriptor["stable_key"] == "S02E05", f"expected request descriptor S02E05, got {descriptor}")

    paths = [
        "Example.Show.S01E01.1080p.mkv",
        "Example.Show.S02E04.1080p.mkv",
        "Example.Show.S02E05.1080p.mkv",
        "Example.Show.S03E01.1080p.mkv",
        "Sample/Example.Show.S02E05.Sample.mkv",
    ]
    handle = FakeHandle(paths)
    configured = await handler.configure_selective_download(
        "tv-specific-from-multiseason",
        handle,
        category_id="tv",
        target_descriptors=[descriptor],
    )
    picked = selected_paths(paths, handle.priorities)
    assert_true(configured, "selective bundle configuration should succeed")
    assert_true(picked == ["Example.Show.S02E05.1080p.mkv"], f"expected only S02E05, got {picked}")
    return {"path": "TV S02E05 request -> S01-S03 bundle -> selective file priority", "selected": picked}


async def scenario_full_season_inside_multi_season_pack() -> dict[str, Any]:
    tv = TvShowCategory()
    handler = BundleDownloadHandler()
    item = SimpleNamespace(key="Example Show", display_name="Example Show", last_season=None, last_episode=None)
    result = SearchResult(title="Example Show Seasons 1-3 Complete 1080p", magnet="magnet:?xt=urn:btih:tv-seasons")

    assert_true(tv.validate_search_result_for_request(result, item, "Season 2"), "Season 2 should accept a containing Seasons 1-3 bundle")
    descriptor = tv.unit_descriptor_from_search_result(result, item, "Season 2")
    assert_true(descriptor["granularity"] == "season" and descriptor["stable_key"] == "S02", f"expected Season 2 descriptor, got {descriptor}")

    paths = [
        "Example.Show.S01E01.1080p.mkv",
        "Example.Show.S02E01.1080p.mkv",
        "Example.Show.S02E02.1080p.mkv",
        "Example.Show.S03E01.1080p.mkv",
        "Extras/interview.mkv",
    ]
    handle = FakeHandle(paths)
    configured = await handler.configure_selective_download(
        "tv-season-from-multiseason",
        handle,
        category_id="tv",
        target_descriptors=[descriptor],
    )
    picked = selected_paths(paths, handle.priorities)
    assert_true(configured, "season selective configuration should succeed")
    assert_true(picked == ["Example.Show.S02E01.1080p.mkv", "Example.Show.S02E02.1080p.mkv"], f"expected only season 2 files, got {picked}")
    return {"path": "TV Season 2 request -> Seasons 1-3 bundle -> season-only priorities", "selected": picked}


def scenario_tv_candidate_validation_fallbacks() -> dict[str, Any]:
    tv = TvShowCategory()
    item = SimpleNamespace(key="Example Show", display_name="Example Show", last_season=None, last_episode=None)
    accepted_range = SearchResult(title="Example.Show.S01E03-E07.1080p", magnet="m:range")
    rejected_range = SearchResult(title="Example.Show.S01E03-E07.1080p", magnet="m:range")
    accepted_series = SearchResult(title="Example Show Complete Series 1080p", magnet="m:series")
    wrong_season = SearchResult(title="Example.Show.S03.Complete.1080p", magnet="m:wrong")

    assert_true(tv.validate_search_result_for_request(accepted_range, item, "S01E05"), "S01E05 should accept S01E03-E07")
    assert_true(not tv.validate_search_result_for_request(rejected_range, item, "S01E08"), "S01E08 should not accept S01E03-E07")
    assert_true(tv.validate_search_result_for_request(accepted_series, item, "S05E01"), "Complete Series should remain eligible for later metadata/LLM judgment")
    assert_true(not tv.validate_search_result_for_request(wrong_season, item, "S02E01"), "wrong single-season pack should be rejected")

    context = TVBundleKnowledge.detect_season_pack("Example Show Complete Series 1080p")
    assert_true(context and context["pack_type"] == "series_complete", f"expected series_complete, got {context}")
    return {"path": "TV validation -> exact/range/multi-season/series fallbacks", "series_context": context}


async def scenario_movie_inside_collection() -> dict[str, Any]:
    movie = MovieCategory()
    handler = BundleDownloadHandler()
    item = SimpleNamespace(key="The Matrix", display_name="The Matrix", year=1999)
    result = SearchResult(title="The.Matrix.Collection.1999.2003.1080p.BluRay", magnet="magnet:?xt=urn:btih:movie-collection")
    descriptor = movie.unit_descriptor_from_search_result(result, item, None)
    assert_true(movie.torrent_bundle_candidate_context(result, item=item) is not None, "movie collection should expose bundle context")

    paths = [
        "The Matrix Collection/The.Matrix.1999.1080p.BluRay.mkv",
        "The Matrix Collection/The.Matrix.Reloaded.2003.1080p.BluRay.mkv",
        "The Matrix Collection/Sample/The.Matrix.1999.sample.mkv",
        "The Matrix Collection/Extras/Behind.The.Scenes.mkv",
    ]
    handle = FakeHandle(paths)
    configured = await handler.configure_selective_download(
        "movie-from-collection",
        handle,
        category_id="movie",
        target_descriptors=[descriptor],
    )
    picked = selected_paths(paths, handle.priorities)
    assert_true(configured, "movie collection selective configuration should succeed")
    assert_true(picked == ["The Matrix Collection/The.Matrix.1999.1080p.BluRay.mkv"], f"expected only requested movie payload, got {picked}")
    return {"path": "Movie request -> collection torrent -> requested film only", "selected": picked}


def scenario_large_bundle_passes_to_llm() -> dict[str, Any]:
    handler = BundleDownloadHandler()
    title = "Example.Show.S01-S03.Complete.1080p.WEB-DL"
    context = handler.describe_candidate(title, category_id="tv")
    estimate = handler.compute_per_unit_limit_mb(
        90 * 1024 * 1024 * 1024,
        title,
        category_id="tv",
        bundle_context=context,
    )
    accepted, reason = SmartQualityInferrer().should_accept_result(
        SearchResult(title=title, magnet="m:large", size_bytes=90 * 1024 * 1024 * 1024),
        QualityProfile(max_file_size_mb=3000, size_limit_mode=SizeLimitMode.FILE_SIZE),
    )
    assert_true(context and context["scope"] == "season_range", f"expected season_range context, got {context}")
    assert_true(estimate is not None and estimate > 0 and estimate < 3000, f"expected sane per-unit estimate, got {estimate}")
    assert_true(accepted, "large total bundle should reach LLM/category evaluation instead of hard rejection")
    return {"path": "Large bundle -> per-unit estimate -> LLM/category evaluation", "estimated_unit_size_mb": estimate, "reason": reason}


async def main() -> None:
    scenarios = [
        await scenario_specific_episode_inside_multi_season_pack(),
        await scenario_full_season_inside_multi_season_pack(),
        scenario_tv_candidate_validation_fallbacks(),
        await scenario_movie_inside_collection(),
        scenario_large_bundle_passes_to_llm(),
    ]
    for index, scenario in enumerate(scenarios, start=1):
        print(f"PASS {index}: {scenario['path']}")
        details = {k: v for k, v in scenario.items() if k != "path"}
        if details:
            print(f"       {details}")


if __name__ == "__main__":
    asyncio.run(main())
