#!/usr/bin/env python3
"""Round 241 regressions for season-pack selection contradictions.

These tests cover the failure where a correct LLM-reviewed Italian S01E01-06
pack was found, but a deterministic batch recommendation also exposed stray
S01E07/S08E02 groups and the final chat model invented extra season episodes.
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ai.tool_result_compactor import ToolResultCompactor
from src.ai.tools.scheduling import _build_batch_recommendation, _candidate_picker_rows
from src.core.scheduler_services import SchedulerTorrentSearchService


def test_season_pack_candidate_suppresses_episode_batch_recommendation() -> None:
    candidates = [
        {
            "candidate_id": "pack-ita",
            "title": "A Knight of the Seven Kingdoms S01e01-06 [1080p Ita Eng Spa h265 10bit SubS]",
            "is_bundle": True,
            "bundle_scope": "episode_range",
            "pack_type": "partial_range",
            "unit_descriptor": {"stable_key": "S01", "label": "Season 1", "granularity": "season", "sort_key": [1, 0], "coordinates": {"season": 1}},
        },
        {
            "candidate_id": "ep7-noise",
            "title": "A Knight of the Seven Kingdoms S01E07 1080p WEB h264-ETHEL",
            "unit_descriptor": {"stable_key": "S01E07", "label": "S01E07", "granularity": "episode", "sort_key": [1, 7], "coordinates": {"season": 1, "episode": 7}},
        },
    ]
    assert _build_batch_recommendation(
        name="A Knight of the Seven Kingdoms",
        category_id="tv",
        season=1,
        episode=None,
        search_scope="default",
        result_set_id="rs1",
        candidates=candidates,
        category=object(),
        preferred_language="Italian",
    ) is None


def test_expected_episode_count_and_full_coverage_annotation() -> None:
    query = "Season 1 pack queries: A Knight S01E01-E06 | A Knight S01E01-06 | A Knight S01"
    assert SchedulerTorrentSearchService._expected_episode_count_from_query_summary(query, 1) == 6
    payload = {
        "bundle_context": {
            "scope": "episode_range",
            "season": 1,
            "start": 1,
            "end": 6,
            "unit_count": 6,
        }
    }
    SchedulerTorrentSearchService._annotate_requested_season_coverage(payload, 1, 6)
    assert payload["requested_season_coverage"] == "full_requested_season"
    assert "expected season length is 6" in payload["coverage_note"]


def test_candidate_picker_and_compactor_preserve_full_season_coverage_and_queue_instruction() -> None:
    candidate = {
        "candidate_id": "pack-ita",
        "index": 1,
        "title": "A Knight of the Seven Kingdoms S01e01-06 [1080p Ita Eng Spa h265 10bit SubS]",
        "size": "3.3 GB",
        "seeders": 100,
        "languages": ["Italian", "English", "Spanish"],
        "is_bundle": True,
        "bundle_scope": "episode_range",
        "pack_type": "partial_range",
        "bundle_unit_count": 6,
        "expected_episode_count": 6,
        "requested_season_coverage": "full_requested_season",
        "coverage_note": "covers S01E01-E06; category expected season length is 6",
        "llm_recommended": True,
    }
    picker = _candidate_picker_rows([candidate])
    assert picker[0]["requested_season_coverage"] == "full_requested_season"
    assert picker[0]["coverage_note"].startswith("covers S01E01")
    result = {
        "query": "Season 1 pack queries: A Knight of the Seven Kingdoms S01E01-E06",
        "language": "Italian",
        "category_id": "tv",
        "name": "A Knight of the Seven Kingdoms",
        "season": 1,
        "expected_episode_count": 6,
        "result_set_id": "rs1",
        "candidate_picker": picker,
        "candidates": [candidate],
        "llm_candidate_review_status": "reviewed",
        "recommended_candidate_id": "pack-ita",
        "llm_next_action": "Call queue_download with result_set_id='rs1' and candidate_id='pack-ita'.",
        "llm_candidate_review": {
            "recommended_candidate_ids": ["pack-ita"],
            "confidence": "high",
            "should_queue_now": True,
            "needs_user_choice": False,
            "reason": "Best Italian full-season pack.",
            "answer_hint": "Selected Italian full-season pack episodes 1-6.",
        },
    }
    compact = ToolResultCompactor().compact("search_media_torrents", result)
    assert compact["expected_episode_count"] == 6
    assert compact["candidate_picker"][0]["requested_season_coverage"] == "full_requested_season"
    assert compact["candidates"][0]["coverage_note"].endswith("season length is 6")
    assert "queue_download" in compact["llm_next_action"]
    assert "batch_recommendation" not in compact


def test_download_prompt_tells_model_not_to_invent_extra_episodes() -> None:
    guidance = (ROOT / "src/ai/task_prompt_guidance.py").read_text()
    assert "Never infer missing or extra TV episodes from candidate groups" in guidance
    assert "recommended S01E01-06 pack" in guidance


def main() -> None:
    test_season_pack_candidate_suppresses_episode_batch_recommendation()
    test_expected_episode_count_and_full_coverage_annotation()
    test_candidate_picker_and_compactor_preserve_full_season_coverage_and_queue_instruction()
    test_download_prompt_tells_model_not_to_invent_extra_episodes()
    print("round241_download_selection_contradiction_tests: OK")


if __name__ == "__main__":
    main()
