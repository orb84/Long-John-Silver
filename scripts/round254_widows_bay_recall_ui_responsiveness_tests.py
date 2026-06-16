#!/usr/bin/env python3
"""Round 254 regressions for Widows Bay Italian recall and bounded fallback behavior."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import asyncio
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ai.tools.scheduling import _annotate_selection_policy
from src.core.categories.tv import TvShowCategory
from src.core.categories.tv_agent import TvAgentSearchMixin


@dataclass
class FakeResult:
    title: str
    seeders: int = 1
    size_bytes: int = 1_000_000_000
    magnet: str = "magnet:?xt=urn:btih:test"
    source: str = "test"
    size: str = "1 GB"
    quality_score: float = 0.0


class FakeItem:
    key = "Widow Bay"
    quality = None


def _season_payload(candidate_id: str, title: str, *, seeders: int, languages: list[str] | None, is_bundle: bool = True) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "title": title,
        "seeders": seeders,
        "languages": languages or [],
        "resolution": "1080p",
        "estimated_bitrate_kbps": 4200,
        "quality_score": 0.6,
        "is_bundle": is_bundle,
        "bundle_scope": "episode_range" if is_bundle else None,
        "pack_type": "partial_range" if is_bundle else None,
        "bundle_unit_count": 6 if is_bundle else 1,
        "unit_descriptor": {
            "granularity": "season" if is_bundle else "episode",
            "stable_key": "S01" if is_bundle else "S01E07",
            "label": "Season 1" if is_bundle else "S01E07",
            "sort_key": [1, 0 if is_bundle else 7],
            "coordinates": {"season": 1} if is_bundle else {"season": 1, "episode": 7},
        },
    }


def test_singular_user_title_matches_possessive_plural_tracker_title() -> None:
    assert TvAgentSearchMixin._title_matches_requested_series(
        "Widows.Bay.S01E01-06.1080p.ATVP.WEB-DL.ITA.ENG.DD5.1.H.264-G66",
        "Widow Bay",
    )
    assert TvAgentSearchMixin._title_matches_requested_series(
        "Widow's Bay (2026) s01e06-07 [Mkv - 1080p H264 - MultiLang Aac 2.0 - MultiSubs]",
        "Widow Bay",
    )
    assert not TvAgentSearchMixin._title_matches_requested_series("The Hardy Boys S01", "The Boys")


def test_relevant_pack_is_not_rejected_as_title_mismatch_for_widow_vs_widows() -> None:
    tv = TvShowCategory()
    result = FakeResult("Widows.Bay.S01E01-06.1080p.ATVP.WEB-DL.ITA.ENG.DD5.1.H.264-G66")
    structural, reviewable, reason = tv._season_pack_decision_reason(result, season=1, item=FakeItem(), language="Italian")
    assert structural is True
    assert reason == "accept_structural_season_pack"


def test_explicit_italian_candidates_outrank_unknown_language_even_with_fewer_seeders() -> None:
    tv = TvShowCategory()
    italian_pack = _season_payload(
        "italian-pack",
        "Widows.Bay.S01E01-06.1080p.ATVP.WEB-DL.ITA.ENG.DD5.1.H.264-G66",
        seeders=40,
        languages=["Italian", "English"],
    )
    unknown_multilang = _season_payload(
        "unknown-high-seed",
        "Widow's Bay (2026) s01e06-07 [Mkv - 1080p H264 - MultiLang Aac 2.0 - MultiSubs]",
        seeders=324,
        languages=[],
    )
    filtered = tv.filter_agent_candidate_payloads_for_request(
        [unknown_multilang, italian_pack],
        season=1,
        episode=None,
        search_scope="bundle_preferred",
        language="Italian",
    )
    assert [row["candidate_id"] for row in filtered][:2] == ["italian-pack", "unknown-high-seed"]


def test_explicit_non_english_unknown_rows_are_not_auto_queueable() -> None:
    italian = _season_payload(
        "italian-pack",
        "Widows.Bay.S01E01-06.1080p.ATVP.WEB-DL.ITA.ENG.DD5.1.H.264-G66",
        seeders=40,
        languages=["Italian", "English"],
    )
    unknown = _season_payload(
        "unknown-pack",
        "Widow's Bay (2026) s01e06-07 [Mkv - 1080p H264 - MultiLang Aac 2.0 - MultiSubs]",
        seeders=324,
        languages=[],
    )
    rows = [italian, unknown]
    _annotate_selection_policy(rows, preferred_language="Italian", language_is_explicit=True)
    assert italian["auto_queue_allowed"] is True
    assert unknown["auto_queue_allowed"] is False
    assert "language not advertised" in unknown["auto_queue_blocked_reason"]


def test_same_season_explicit_language_single_episode_can_reach_pack_workspace_as_fallback() -> None:
    tv = TvShowCategory()
    result = FakeResult("Widows.Bay.S01E07.Seasickness.1080p.ATVP.WEB-DL.ITA-ENG.DDP5.1.Atmos.H.264-G66.mkv")
    structural, reviewable, reason = tv._season_pack_decision_reason(result, season=1, item=FakeItem(), language="Italian")
    assert structural is False
    assert reviewable is True
    assert reason == "accept_llm_review_plausible_pack"


async def test_italian_rank_agent_results_prefers_language_evidence_over_unknown_high_seed() -> None:
    tv = TvShowCategory()
    item = FakeItem()
    results = [
        FakeResult("Widow's Bay (2026) s01e06-07 [Mkv - 1080p H264 - MultiLang Aac 2.0 - MultiSubs]", seeders=324, size_bytes=7_644_960_256),
        FakeResult("Widows.Bay.S01E01-06.1080p.ATVP.WEB-DL.ITA.ENG.DD5.1.H.264-G66", seeders=40, size_bytes=19_005_231_104),
    ]
    ranked = await tv.rank_agent_search_results(results, item=item, language="Italian", season=1, episode=None, context=None)
    assert ranked[0].title.startswith("Widows.Bay.S01E01-06"), [row.title for row in ranked]


def main() -> None:
    test_singular_user_title_matches_possessive_plural_tracker_title()
    test_relevant_pack_is_not_rejected_as_title_mismatch_for_widow_vs_widows()
    test_explicit_italian_candidates_outrank_unknown_language_even_with_fewer_seeders()
    test_explicit_non_english_unknown_rows_are_not_auto_queueable()
    test_same_season_explicit_language_single_episode_can_reach_pack_workspace_as_fallback()
    asyncio.run(test_italian_rank_agent_results_prefers_language_evidence_over_unknown_high_seed())
    print("round254_widows_bay_recall_ui_responsiveness_tests: OK")


if __name__ == "__main__":
    main()
