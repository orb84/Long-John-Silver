#!/usr/bin/env python3
"""Round 245 regressions for download follow-ups and Helm telemetry stability."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ai.download_context_policy import DownloadContextPolicy
from src.ai.tools.search_workspace import SearchQualityChoicePolicy


def _pack(cid: str, title: str, resolution: str, size: int, bitrate: int, seeders: int | None) -> dict:
    return {
        "candidate_id": cid,
        "index": 1,
        "title": title,
        "size": str(size),
        "size_bytes": size,
        "seeders": seeders,
        "languages": ["Italian", "English", "Spanish"],
        "resolution": resolution,
        "codec": "h265",
        "is_bundle": True,
        "bundle_scope": "episode_range",
        "pack_type": "partial_range",
        "bundle_unit_count": 6,
        "requested_season_coverage": "full_requested_season",
        "auto_queue_allowed": True,
        "estimated_bitrate_kbps": bitrate,
        "unit_descriptor": {"granularity": "season", "label": "Season 1", "stable_key": "S01", "coordinates": {"season": 1}},
    }


def test_short_resolution_reply_is_candidate_followup() -> None:
    assert DownloadContextPolicy.should_suppress_pending_candidates("get the 720 version please", "DOWNLOAD") is False
    assert DownloadContextPolicy.should_start_fresh_goal("get the 720 version please", "DOWNLOAD") is False


def test_progress_generation_blocks_llm_for_downloads() -> None:
    src = (ROOT / "src/ai/assistant.py").read_text(encoding="utf-8")
    assert 'intent_value in {"DOWNLOAD", "CONFIG"}' in src
    assert "_looks_like_bad_progress_ack" in src
    assert "I’m sorry" in src or "i’m sorry" in src


def test_equivalent_720_mirrors_are_not_user_choice_options() -> None:
    compact_1080 = _pack(
        "compact-1080",
        "A Knight of the Seven Kingdoms S01e01-06 [1080p Ita Eng Spa h265 10bit SubS]",
        "1080p",
        3521873182,
        1422,
        39,
    )
    larger_720 = _pack(
        "larger-720-good",
        "A Knight of the Seven Kingdoms S01e01-06 [720p Ita Eng Spa SubS] byMe7alh [MIRCrew]",
        "720p",
        6358800384,
        2569,
        11,
    )
    mirror_720 = _pack(
        "larger-720-mirror",
        "A Knight Of The Seven Kingdoms S01e01-06 (720p Ita Eng Spa SubS) byMe7alh",
        "720p",
        6356551680,
        2568,
        None,
    )
    policy = SearchQualityChoicePolicy.evaluate([compact_1080, larger_720, mirror_720], {})
    assert policy["requires_user_choice"] is True
    assert "larger-720-good" in policy["candidate_ids"]
    assert "compact-1080" in policy["candidate_ids"]
    assert "larger-720-mirror" not in policy["candidate_ids"]


def test_explicit_resolution_prevents_cross_resolution_quality_prompt() -> None:
    larger_720 = _pack("larger-720-good", "720p pack", "720p", 6358800384, 2569, 11)
    mirror_720 = _pack("larger-720-mirror", "720p mirror", "720p", 6356551680, 2568, None)
    compact_1080 = _pack("compact-1080", "1080p pack", "1080p", 3521873182, 1422, 39)
    policy = SearchQualityChoicePolicy.evaluate([larger_720, mirror_720, compact_1080], {"preferred_resolution": "720p"})
    assert policy["requires_user_choice"] is False


def test_pending_context_exposes_quality_policy() -> None:
    src = (ROOT / "src/ai/pending_actions.py").read_text(encoding="utf-8")
    assert "quality_choice_policy" in src
    assert "_compact_quality_choice" in src
    assert "_compact_llm_review" in src


def test_silent_poll_preserves_live_telemetry() -> None:
    src = (ROOT / "src/web/static/js/components/downloadManagerUI.js").read_text(encoding="utf-8")
    assert "_mergeSilentPoll" in src
    assert "_preserveLiveTelemetry" in src
    assert "lastNonZeroRate" in src
    assert "lastPeers" in src
    assert "lastSeeds" in src


def main() -> None:
    test_short_resolution_reply_is_candidate_followup()
    test_progress_generation_blocks_llm_for_downloads()
    test_equivalent_720_mirrors_are_not_user_choice_options()
    test_explicit_resolution_prevents_cross_resolution_quality_prompt()
    test_pending_context_exposes_quality_policy()
    test_silent_poll_preserves_live_telemetry()
    print("round245_download_followup_and_telemetry_tests: OK")


if __name__ == "__main__":
    main()
