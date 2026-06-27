#!/usr/bin/env python3
"""Round 249 regressions for TV torrent scope, language, and quality options.

Covers the For All Mankind failure where a Season 1 request surfaced useless
quality choices, asked about Italian despite English being configured, and later
let S04/S05 single episodes into a Season 1 candidate workspace.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ai.download_candidate_adjudicator import DownloadCandidateAdjudicator
from src.core.categories.tv import TvShowCategory
from src.core.categories.tv_bundle import TVBundleKnowledge
from src.ai.tools.search_workspace import SearchQualityChoicePolicy


@dataclass
class FakeItem:
    key: str


@dataclass
class FakeResult:
    title: str
    magnet: str = "magnet:?xt=urn:btih:deadbeef"
    source: str = "test"
    seeders: int = 1
    size: str = "1"
    size_bytes: int = 1
    quality_score: float = 0.0


def _episode_candidate(candidate_id: str, episode: int, bitrate: int, size: int) -> dict:
    return {
        "candidate_id": candidate_id,
        "title": f"For All Mankind S01E{episode:02d} 1080p WEB-DL 6CH x265 HEVC-PSA",
        "resolution": "1080p",
        "size": str(size),
        "size_bytes": size,
        "estimated_bitrate_kbps": bitrate,
        "seeders": 100 - episode,
        "auto_queue_allowed": True,
        "languages": ["English"],
        "unit_descriptor": {
            "granularity": "episode",
            "label": f"S01E{episode:02d}",
            "stable_key": f"S01E{episode:02d}",
            "sort_key": [1, episode],
            "coordinates": {"season": 1, "episode": episode},
        },
    }


def _same_episode_variant(candidate_id: str, bitrate: int, size: int) -> dict:
    row = _episode_candidate(candidate_id, 1, bitrate, size)
    row["title"] = f"For All Mankind S01E01 Red Moon 1080p variant {candidate_id}"
    return row


def _season_pack(candidate_id: str, bitrate: int, size: int, seeders: int) -> dict:
    return {
        "candidate_id": candidate_id,
        "title": f"For All ManKind S01e01-10 720p Ita Eng variant {candidate_id}",
        "resolution": "720p",
        "size": str(size),
        "size_bytes": size,
        "estimated_bitrate_kbps": bitrate,
        "seeders": seeders,
        "auto_queue_allowed": True,
        "languages": ["Italian", "English"],
        "is_bundle": True,
        "bundle_scope": "episode_range",
        "pack_type": "partial_range",
        "bundle_unit_count": 10,
        "requested_season_coverage": "full_requested_season",
        "unit_descriptor": {
            "granularity": "season",
            "label": "Season 1",
            "stable_key": "S01",
            "sort_key": [1, 0],
            "coordinates": {"season": 1},
        },
    }


def test_tracker_adjacent_episode_range_is_season_pack() -> None:
    pack = TVBundleKnowledge.detect_season_pack("For All ManKind S01e01 10 720p Ita Eng Sub Ita Eng byMetalh")
    assert pack is not None
    assert pack["scope"] == "episode_range"
    assert pack["season"] == 1
    assert pack["start"] == 1
    assert pack["end"] == 10


def test_adjacent_range_parser_does_not_misread_resolution() -> None:
    pack = TVBundleKnowledge.detect_season_pack("For All Mankind S01E01 1080p WEB-DL 6CH x265 HEVC-PSA")
    assert pack is None


def test_tv_pack_workspace_rejects_wrong_season_single_episodes() -> None:
    tv = TvShowCategory()
    item = FakeItem("For All Mankind")
    accepted, llm_review, reason = tv._season_pack_decision_reason(
        FakeResult("For All Mankind S04E01 1080p WEB-DL x265"),
        season=1,
        item=item,
        language="English",
    )
    assert accepted is False
    assert llm_review is False
    assert reason == "reject_not_detected_as_requested_season_pack"

    accepted, llm_review, reason = tv._season_pack_decision_reason(
        FakeResult("For All Mankind S05E01 1080p WEB-DL x265"),
        season=1,
        item=item,
        language="English",
    )
    assert accepted is False
    assert llm_review is False
    assert reason == "reject_not_detected_as_requested_season_pack"


def test_tv_pack_workspace_accepts_adjacent_requested_season_range() -> None:
    tv = TvShowCategory()
    item = FakeItem("For All Mankind")
    accepted, _llm_review, reason = tv._season_pack_decision_reason(
        FakeResult("For All ManKind S01e01 10 720p Ita Eng Sub Ita Eng byMetalh"),
        season=1,
        item=item,
        language="English",
    )
    assert accepted is True
    assert reason == "accept_structural_season_pack"


def test_quality_policy_does_not_compare_different_episodes_as_alternatives() -> None:
    policy = SearchQualityChoicePolicy.evaluate([
        _episode_candidate("s01e01", 1, 13483, 5_180_000_000),
        _episode_candidate("s01e04", 4, 1340, 537_081_088),
        _episode_candidate("s01e08", 8, 1300, 698_544_832),
    ], {})
    assert policy["requires_user_choice"] is False


def test_quality_policy_still_compares_same_episode_variants() -> None:
    policy = SearchQualityChoicePolicy.evaluate([
        _same_episode_variant("compact", 1300, 700_000_000),
        _same_episode_variant("remux", 13000, 6_000_000_000),
    ], {})
    assert policy["requires_user_choice"] is True
    assert policy["tradeoff_type"] == "same_resolution_bitrate_tradeoff"
    assert set(policy["candidate_ids"]) == {"compact", "remux"}


def test_quality_policy_collapses_equivalent_season_pack_mirrors() -> None:
    policy = SearchQualityChoicePolicy.evaluate([
        _season_pack("adjacent-range", 3670, 15_139_760_128, 36),
        _season_pack("dash-range", 3683, 15_193_446_400, 6),
    ], {})
    assert policy["requires_user_choice"] is False


def test_download_prompts_apply_configured_language_silently() -> None:
    guidance = (ROOT / "src/ai/task_prompt_guidance.py").read_text(encoding="utf-8")
    assistant = (ROOT / "src/ai/assistant.py").read_text(encoding="utf-8")
    assert "configured media language" in guidance
    assert "owning category's language-tag skill" in guidance
    assert "Use category guidance for language-tag semantics" in assistant


def test_adjudicator_language_and_scope_prompt_rules_are_category_owned() -> None:
    tv_guidance = TvShowCategory().build_torrent_selection_guidance()
    prompt = DownloadCandidateAdjudicator()._build_prompt(
        request={
            "user_prompt": "Can you please grab me For All Mankind season 1?",
            "effective_search": {"language": "English", "season": 1, "search_scope": "bundle_preferred"},
        },
        rows=[],
        category_guidance=tv_guidance,
        review_stage="budget_probe",
        chunk_index=1,
        chunk_count=1,
        context_limit_tokens=8192,
    )
    assert "Use the owning category guidance" in prompt
    assert "If the configured language is English, ITA+ENG or MULTI is only a fallback" in prompt
    assert "S01E01-06" in prompt
    assert "TV full-season requests" not in prompt


def main() -> None:
    test_tracker_adjacent_episode_range_is_season_pack()
    test_adjacent_range_parser_does_not_misread_resolution()
    test_tv_pack_workspace_rejects_wrong_season_single_episodes()
    test_tv_pack_workspace_accepts_adjacent_requested_season_range()
    test_quality_policy_does_not_compare_different_episodes_as_alternatives()
    test_quality_policy_still_compares_same_episode_variants()
    test_quality_policy_collapses_equivalent_season_pack_mirrors()
    test_download_prompts_apply_configured_language_silently()
    test_adjudicator_language_and_scope_prompt_rules_are_category_owned()
    print("round249_torrent_scope_language_quality_tests: OK")


if __name__ == "__main__":
    main()
