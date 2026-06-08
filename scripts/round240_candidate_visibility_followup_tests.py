"""Round 240 regression tests for LLM candidate visibility after deep review."""
from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.ai.tool_result_compactor import ToolResultCompactor
from src.ai.tools.scheduling import _candidate_picker_rows


def test_candidate_picker_rows_preserve_llm_recommended_and_candidate_id_alias() -> None:
    rows = _candidate_picker_rows([
        {
            "candidate_id": "cand-pack",
            "index": 1,
            "title": "A Knight of the Seven Kingdoms S01E01-06 [1080p Ita Eng]",
            "size": "8.1 GB",
            "seeders": 42,
            "llm_recommended": True,
            "is_bundle": True,
            "bundle_scope": "season",
            "selection_warnings": ["inspect pack files before queueing"],
        }
    ])
    assert rows[0]["id"] == "cand-pack"
    assert rows[0]["candidate_id"] == "cand-pack"
    assert rows[0]["llm_recommended"] is True
    assert rows[0]["selection_warnings"] == ["inspect pack files before queueing"]


def test_compactor_keeps_late_llm_recommended_candidate_outside_top_eight() -> None:
    candidates = []
    for idx in range(1, 13):
        cid = f"cand-{idx:02d}"
        candidates.append({
            "candidate_id": cid,
            "index": idx,
            "title": f"Noise Candidate {idx}",
            "size": "1 GB",
            "seeders": idx,
            "result_set_id": "rs1",
        })
    candidates[-1].update({
        "title": "A Knight of the Seven Kingdoms S01E01-06 [1080p Ita Eng]",
        "is_bundle": True,
        "bundle_scope": "season",
        "llm_recommended": True,
    })
    result = {
        "query": "A Knight of the Seven Kingdoms season pack",
        "result_set_id": "rs1",
        "candidates": candidates,
        "candidate_picker": _candidate_picker_rows(candidates, limit=60),
        "llm_candidate_review_status": "reviewed",
        "recommended_candidate_id": "cand-12",
        "llm_candidate_review": {
            "recommended_candidate_ids": ["cand-12"],
            "confidence": "high",
            "reason": "The late candidate is the Italian season pack.",
        },
    }
    compact = ToolResultCompactor().compact("search_media_torrents", result)
    compact_ids = [row.get("candidate_id") for row in compact["candidates"]]
    assert "cand-12" in compact_ids, compact_ids
    picker_row = next(row for row in compact["candidate_picker"] if row.get("candidate_id") == "cand-12")
    assert picker_row["llm_recommended"] is True
    assert compact["llm_candidate_review"]["recommended_candidate_ids"] == ["cand-12"]


def main() -> None:
    test_candidate_picker_rows_preserve_llm_recommended_and_candidate_id_alias()
    test_compactor_keeps_late_llm_recommended_candidate_outside_top_eight()
    print("round240_candidate_visibility_followup_tests: OK")


if __name__ == "__main__":
    main()
