#!/usr/bin/env python3
"""Round 207 regression tests for TV language-aware search and pack safety.

These tests protect the failure seen in the macOS logs: the tool received
language=Italian, but provider queries did not expose ITA/Italian variants;
then a huge multi-season pack was treated as a clean Season 1 answer and a
multi-unit batch recommendation was built for a one-season request.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ai.tools.scheduling import (
    _annotate_selection_policy,
    _build_batch_recommendation,
    _estimated_total_size_bytes,
)
from src.core.categories.tv import TvShowCategory
from src.core.models import SearchResult
from src.search.jackett import JackettSearch


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def test_language_variants_are_in_tv_query_plan() -> None:
    category = TvShowCategory()
    item = SimpleNamespace(key="The Boys")
    queries = await category.agent_pack_search_queries(item, 1, language="Italian", context=None)
    assert_true(queries[0] == "The Boys S01", f"first query should preserve manual-equivalent exact query, got {queries[:4]!r}")
    assert_true("The Boys S01 ITA" in queries[:3], f"Italian ITA variant should appear early, got {queries!r}")
    assert_true("The Boys S01 Italian" in queries[:4], f"Italian word variant should appear early, got {queries!r}")
    if "The Boys" in queries:
        assert_true(queries.index("The Boys") > queries.index("The Boys Season 1"), f"broad title query should be late, got {queries!r}")
    assert_true(len(queries) <= 8, f"interactive TV pack ladder must stay bounded, got {len(queries)} queries: {queries!r}")


def test_season_unit_coordinates_are_not_reparsed_from_pack_title() -> None:
    category = TvShowCategory()
    assert_true(category._unit_coordinates("Season 1") == (1, 0), "Season 1 should resolve to season-only coordinates")
    assert_true(category._unit_coordinates("S01") == (1, 0), "S01 should resolve to season-only coordinates")

    result = SearchResult(title="The Boys S01-S05 ITA WEBRip x264", size="24 GB", seeders=8, source="test")
    descriptor = category.unit_descriptor_from_search_result(result, SimpleNamespace(key="The Boys"), unit_label="Season 1")
    assert_true((descriptor.get("unit_key") or descriptor.get("stable_key")) == "S01", f"season-only request should keep S01, got {descriptor!r}")
    assert_true(descriptor.get("coordinates", {}).get("season") == 1, f"descriptor should target requested season 1, got {descriptor!r}")
    assert_true("episode" not in descriptor.get("coordinates", {}), f"season descriptor must not invent an episode, got {descriptor!r}")


def test_multi_season_pack_requires_selective_download_and_size_projection() -> None:
    category = TvShowCategory()
    result = SearchResult(title="The Boys S01-S05 ITA WEBRip x264", size="24 GB", seeders=8, source="test")
    context = category.torrent_bundle_candidate_context(result, item=SimpleNamespace(key="The Boys"), unit_label="Season 1")
    assert_true(context is not None, "multi-season TV pack should be detected")
    assert_true(context.get("contains_requested_unit") is True, f"pack should contain requested season, got {context!r}")
    assert_true(context.get("selective_download_required") is True, f"multi-season pack must require selective file selection, got {context!r}")
    assert_true(context.get("selection_scope") == "requested_season_only", f"selection scope should be requested season only, got {context!r}")

    candidates = [{
        "candidate_id": "c1",
        "title": result.title,
        "seeders": 12,
        "languages": ["italian"],
        "size_bytes": 24 * 1024**3,
        "per_episode_size_bytes": 600 * 1024**2,
        "bundle_context": context,
    }]
    _annotate_selection_policy(candidates, preferred_language="Italian")
    assert_true(candidates[0]["auto_queue_allowed"] is False, f"selective pack should not be one-click queueable: {candidates[0]!r}")
    assert_true("requires selective file inspection" in candidates[0]["auto_queue_blocked_reason"], f"missing selective blocker: {candidates[0]!r}")
    projected = _estimated_total_size_bytes(candidates, ["c1"])
    assert_true(projected == 600 * 1024**2 * 10, f"selective size should project requested season only, got {projected}")


def test_bundle_scope_does_not_create_multi_unit_batch_recommendation() -> None:
    recommendation = _build_batch_recommendation(
        name="The Boys",
        category_id="tv",
        season=1,
        episode=None,
        search_scope="bundle_preferred",
        result_set_id="rs1",
        candidates=[{"candidate_id": "c1"}, {"candidate_id": "c2"}],
        category=object(),
        preferred_language="Italian",
    )
    assert_true(recommendation is None, f"bundle/season-pack search should not become multi-unit recommendation: {recommendation!r}")


def test_jackett_api_key_redaction() -> None:
    redacted = JackettSearch._redact_exception("failed GET /api?apikey=supersecret&Query=The+Boys")
    assert_true("supersecret" not in redacted, f"api key leaked in redacted exception: {redacted}")
    assert_true("apikey=<redacted>" in redacted, f"redaction marker missing: {redacted}")


async def main() -> None:
    await test_language_variants_are_in_tv_query_plan()
    test_season_unit_coordinates_are_not_reparsed_from_pack_title()
    test_multi_season_pack_requires_selective_download_and_size_projection()
    test_bundle_scope_does_not_create_multi_unit_batch_recommendation()
    test_jackett_api_key_redaction()
    print("round207_tv_language_pack_safety_tests: PASS")


if __name__ == "__main__":
    asyncio.run(main())
