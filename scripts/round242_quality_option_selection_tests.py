#!/usr/bin/env python3
"""Round 242 regressions for download quality-option presentation.

Covers the failure where LJS found multiple requested-language season packs but
collapsed them into one compact low-bitrate proposal instead of showing the
quality/size tradeoff to the user.
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ai.download_candidate_adjudicator import DownloadCandidateAdjudicator
from src.ai.tool_result_compactor import ToolResultCompactor
from src.ai.tools.search_workspace import SearchQualityChoicePolicy
from src.ai.tools.search_workspace import SearchWorkspaceFormatter


def _pack(cid: str, title: str, resolution: str, codec: str | None, size: int, bitrate: int, seeders: int) -> dict:
    return {
        "candidate_id": cid,
        "index": 1 if cid == "compact-1080" else 2,
        "title": title,
        "size": f"{size}",
        "size_bytes": size,
        "seeders": seeders,
        "languages": ["Italian", "English", "Spanish"],
        "resolution": resolution,
        "codec": codec,
        "is_bundle": True,
        "bundle_scope": "episode_range",
        "pack_type": "partial_range",
        "bundle_unit_count": 6,
        "requested_season_coverage": "full_requested_season",
        "coverage_note": "covers S01E01-E06; category expected season length is 6",
        "auto_queue_allowed": True,
        "per_episode_size": f"{round(size / 6 / (1024**2), 1)} MB",
        "per_episode_size_mb": round(size / 6 / (1024**2), 1),
        "estimated_bitrate_kbps": bitrate,
        "unit_descriptor": {"granularity": "season", "label": "Season 1", "stable_key": "S01"},
    }


def test_cross_resolution_italian_season_packs_require_quality_choice() -> None:
    compact_1080 = _pack(
        "compact-1080",
        "A Knight of the Seven Kingdoms S01e01-06 [1080p Ita Eng Spa h265 10bit SubS]",
        "1080p",
        "h265",
        3521873182,
        1422,
        39,
    )
    larger_720 = _pack(
        "larger-720",
        "A Knight of the Seven Kingdoms S01e01-06 [720p Ita Eng Spa SubS]",
        "720p",
        None,
        6358800384,
        2569,
        11,
    )
    policy = SearchQualityChoicePolicy.evaluate([compact_1080, larger_720], {})
    assert policy["requires_user_choice"] is True
    assert policy["tradeoff_type"] == "bundle_quality_tradeoff"
    assert set(policy["candidate_ids"]) == {"compact-1080", "larger-720"}
    assert policy["comparison"]["max_bitrate_kbps"] > policy["comparison"]["min_bitrate_kbps"] * 1.25


def test_adjudicator_prompt_tells_llm_quality_policy_blocks_auto_queue() -> None:
    prompt = DownloadCandidateAdjudicator()._build_prompt(
        request={
            "user_prompt": "grab A Knight of the Seven Kingdoms in Italian, full first season",
            "effective_search": {
                "quality_choice_policy": {"requires_user_choice": True, "candidate_ids": ["compact-1080", "larger-720"]},
            },
        },
        rows=[],
        category_guidance="TV guidance",
        review_stage="budget_probe",
        chunk_index=1,
        chunk_count=1,
        context_limit_tokens=8192,
    )
    assert "quality_choice_policy.requires_user_choice=true" in prompt
    assert "set should_queue_now=false" in prompt
    assert "quality/size options" in prompt


def test_compactor_preserves_quality_choice_policy_and_picker_details() -> None:
    compact_1080 = _pack("compact-1080", "Compact 1080p Ita pack", "1080p", "h265", 3521873182, 1422, 39)
    larger_720 = _pack("larger-720", "Larger 720p Ita pack", "720p", None, 6358800384, 2569, 11)
    policy = SearchQualityChoicePolicy.evaluate([compact_1080, larger_720], {})
    picker = SearchWorkspaceFormatter.candidate_picker_rows([compact_1080, larger_720])
    result = {
        "query": "A Knight of the Seven Kingdoms S01E01-06 ITA",
        "language": "Italian",
        "category_id": "tv",
        "name": "A Knight of the Seven Kingdoms",
        "season": 1,
        "result_set_id": "rs-quality",
        "candidate_picker": picker,
        "candidates": [compact_1080, larger_720],
        "quality_choice_policy": policy,
        "llm_candidate_review_status": "reviewed",
        "llm_candidate_review": {
            "recommended_candidate_ids": ["compact-1080", "larger-720"],
            "confidence": "high",
            "should_queue_now": False,
            "needs_user_choice": True,
            "reason": "Both Italian packs are viable but trade size/bitrate/resolution.",
        },
    }
    compact = ToolResultCompactor().compact("search_media_torrents", result)
    assert compact["quality_choice_policy"]["requires_user_choice"] is True
    assert len(compact["quality_choice_policy"]["choices"]) == 2
    assert {row["candidate_id"] for row in compact["candidate_picker"][:2]} == {"compact-1080", "larger-720"}
    assert compact["candidate_picker"][0]["per_episode_size"]
    assert compact["candidate_picker"][0]["estimated_bitrate_kbps"]


def test_download_guidance_says_quality_policy_overrides_auto_queue() -> None:
    text = (ROOT / "src/ai/task_prompt_guidance.py").read_text()
    assert "quality_choice_policy.requires_user_choice" in text
    assert "do not queue one candidate" in text
    assert "quality_choice_policy is present, it overrides auto-queue" in text


def main() -> None:
    test_cross_resolution_italian_season_packs_require_quality_choice()
    test_adjudicator_prompt_tells_llm_quality_policy_blocks_auto_queue()
    test_compactor_preserves_quality_choice_policy_and_picker_details()
    test_download_guidance_says_quality_policy_overrides_auto_queue()
    print("round242_quality_option_selection_tests: OK")


if __name__ == "__main__":
    main()
