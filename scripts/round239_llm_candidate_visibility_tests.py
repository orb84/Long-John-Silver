"""Round 239 regression tests for torrent LLM candidate-review visibility.

These checks guard the second review pass after adding LLM candidate
adjudication. The adjudicator may correctly review many candidates, but the
final chat model can only exploit that work if compaction preserves the review
status, recommended IDs, and per-candidate flags.
"""

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.ai.tool_result_compactor import ToolResultCompactor


def test_media_search_compaction_preserves_llm_review() -> None:
    compactor = ToolResultCompactor()
    result = {
        "query": "A Knight of the Seven Kingdoms S01E01-06 Ita",
        "language": "Italian",
        "category_id": "tv",
        "name": "A Knight of the Seven Kingdoms",
        "season": 1,
        "result_set_id": "rs-test",
        "search_scope": "bundle_preferred",
        "recommended_candidate_id": "cand-pack",
        "llm_candidate_review_status": "reviewed",
        "llm_candidate_review": {
            "reviewed_by": "llm_torrent_candidate_adjudicator",
            "candidate_review_mode": "chunked_tournament",
            "candidate_count_reviewed": 120,
            "chunk_count": 6,
            "finalist_count": 18,
            "tournament_round_count": 2,
            "context_limit_tokens": 4096,
            "recommended_candidate_ids": ["cand-pack", "cand-pack-720"],
            "reject_candidate_ids": [f"bad-{i}" for i in range(20)],
            "confidence": "high",
            "should_queue_now": False,
            "needs_user_choice": True,
            "reason": "The first candidate is an Italian S01E01-06 season pack.",
            "answer_hint": "Present the Italian season pack as the best match and inspect if needed.",
        },
        "candidate_picker": [
            {
                "id": "cand-pack",
                "index": 1,
                "title": "A Knight of the Seven Kingdoms S01e01-06 [1080p Ita Eng Spa]",
                "seeders": 42,
                "is_bundle": True,
                "llm_recommended": True,
            },
            {
                "id": "cand-ep1",
                "index": 2,
                "title": "A Knight of the Seven Kingdoms S01E01 ITA",
                "seeders": 12,
            },
        ],
        "candidates": [
            {
                "candidate_id": "cand-pack",
                "index": 1,
                "title": "A Knight of the Seven Kingdoms S01e01-06 [1080p Ita Eng Spa]",
                "seeders": 42,
                "is_bundle": True,
                "pack_type": "season_pack",
                "llm_recommended": True,
                "result_set_id": "rs-test",
            },
            {
                "candidate_id": "cand-ep1",
                "index": 2,
                "title": "A Knight of the Seven Kingdoms S01E01 ITA",
                "seeders": 12,
                "result_set_id": "rs-test",
            },
        ],
    }

    compact = compactor.compact("search_media_torrents", result)
    assert compact["llm_candidate_review_status"] == "reviewed"
    assert compact["recommended_candidate_id"] == "cand-pack"
    assert compact["llm_candidate_review"]["recommended_candidate_ids"] == ["cand-pack", "cand-pack-720"]
    assert compact["llm_candidate_review"]["candidate_count_reviewed"] == 120
    assert compact["llm_candidate_review"]["chunk_count"] == 6
    assert compact["llm_candidate_review"]["reject_candidate_ids_preview"] == [f"bad-{i}" for i in range(8)]
    assert compact["llm_candidate_review"]["rejected_candidate_count"] == 20
    assert compact["candidate_picker"][0]["llm_recommended"] is True
    assert compact["candidates"][0]["llm_recommended"] is True
    assert "llm_review_note" in compact


def test_media_search_compaction_preserves_review_status_without_review_payload() -> None:
    compactor = ToolResultCompactor()
    result = {
        "query": "Star City S01E03 Ita",
        "category_id": "tv",
        "name": "Star City",
        "result_set_id": "rs-star",
        "llm_candidate_review_status": "skipped_no_task_llm",
        "candidate_picker": [],
        "candidates": [],
    }
    compact = compactor.compact("search_media_torrents", result)
    assert compact["llm_candidate_review_status"] == "skipped_no_task_llm"
    assert "llm_candidate_review" not in compact


def main() -> None:
    test_media_search_compaction_preserves_llm_review()
    test_media_search_compaction_preserves_review_status_without_review_payload()
    print("round239_llm_candidate_visibility_tests: OK")


if __name__ == "__main__":
    main()
