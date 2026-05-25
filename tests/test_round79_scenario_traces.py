"""Round 79 executable scenario traces for bundle/pack behavior."""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

sys.modules.setdefault("libtorrent", SimpleNamespace())

from scripts.round79_scenario_trace_tests import (  # noqa: E402
    scenario_full_season_inside_multi_season_pack,
    scenario_large_bundle_passes_to_llm,
    scenario_movie_inside_collection,
    scenario_specific_episode_inside_multi_season_pack,
    scenario_tv_candidate_validation_fallbacks,
)


def test_specific_tv_episode_inside_multi_season_pack_trace() -> None:
    result = asyncio.run(scenario_specific_episode_inside_multi_season_pack())
    assert result["selected"] == ["Example.Show.S02E05.1080p.mkv"]


def test_full_tv_season_inside_multi_season_pack_trace() -> None:
    result = asyncio.run(scenario_full_season_inside_multi_season_pack())
    assert result["selected"] == [
        "Example.Show.S02E01.1080p.mkv",
        "Example.Show.S02E02.1080p.mkv",
    ]


def test_tv_validation_fallback_trace() -> None:
    result = scenario_tv_candidate_validation_fallbacks()
    assert result["series_context"]["pack_type"] == "series_complete"


def test_movie_collection_selective_trace() -> None:
    result = asyncio.run(scenario_movie_inside_collection())
    assert result["selected"] == ["The Matrix Collection/The.Matrix.1999.1080p.BluRay.mkv"]


def test_large_bundle_quality_trace() -> None:
    result = scenario_large_bundle_passes_to_llm()
    assert result["estimated_unit_size_mb"] > 0
