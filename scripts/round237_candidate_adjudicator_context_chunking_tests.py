#!/usr/bin/env python3
"""Regression checks for context-budgeted LLM torrent candidate adjudication."""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ai.download_candidate_adjudicator import DownloadCandidateAdjudicator


class _SmallContextLLM:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.metadata_warmed = False

    async def ensure_model_metadata_for_task(self, task: str) -> None:
        assert task == "torrent_ranker"
        self.metadata_warmed = True

    def endpoint_context_limit_for_task(self, task: str) -> int:
        assert task == "torrent_ranker"
        return 4096

    async def completion(self, **kwargs: object) -> dict[str, object]:
        prompt = str((kwargs.get("messages") or [{}])[0].get("content") if kwargs.get("messages") else "")
        self.prompts.append(prompt)
        ids = re.findall(r'"candidate_id"\s*:\s*"([^"]+)"', prompt)
        if "final_tournament" in prompt:
            # Select the last finalist so the test verifies that candidates from
            # later chunks can survive to the final pass.
            pick = ids[-1] if ids else ""
            reason = "Final tournament selected the latest chunk finalist."
        else:
            # Nominate the last row in every chunk.
            pick = ids[-1] if ids else ""
            reason = "Chunk nominee."
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "recommended_candidate_ids": [pick] if pick else [],
                                "reject_candidate_ids": [],
                                "confidence": "medium",
                                "should_queue_now": False,
                                "needs_user_choice": False,
                                "reason": reason,
                                "answer_hint": "Use the selected candidate.",
                            }
                        )
                    }
                }
            ]
        }


async def _main() -> None:
    adjudicator_source = (ROOT / "src/ai/download_candidate_adjudicator.py").read_text()
    assert "candidates[:40]" not in adjudicator_source, "adjudicator must not silently truncate to first 40 candidates"
    assert "endpoint_context_limit_for_task" in adjudicator_source, "adjudicator must consult task context limit"
    assert "chunked_tournament" in adjudicator_source, "adjudicator must have a multi-prompt review mode"

    fake = _SmallContextLLM()
    adjudicator = DownloadCandidateAdjudicator(fake)
    candidates = []
    for idx in range(90):
        # Long titles force chunking under the fake 4k context window.
        suffix = " very-long-release-title-fragment" * 12
        candidates.append(
            {
                "candidate_id": f"cand{idx:02d}",
                "index": idx + 1,
                "title": f"A Knight of the Seven Kingdoms S01E01-06 ITA ENG candidate {idx:02d}{suffix}",
                "seeders": idx,
                "is_bundle": True,
            }
        )

    review = await adjudicator.review(
        user_prompt="Grab A Knight of the Seven Kingdoms in Italian, full first season.",
        tool_arguments={"name": "A Knight of the Seven Kingdoms", "language": "Italian", "search_scope": "bundle_preferred"},
        search_result={"name": "A Knight of the Seven Kingdoms", "category_id": "tv", "language": "Italian"},
        candidates=candidates,
        category_guidance="Prefer an Italian full-season pack over a single episode.",
    )
    assert review, "review should be produced"
    assert review.get("candidate_review_mode") == "chunked_tournament", review
    assert int(review.get("chunk_count") or 0) > 1, review
    assert review.get("candidate_count_reviewed") == len(candidates), review
    assert fake.metadata_warmed, "adjudicator should warm model metadata before budgeting"
    assert any("chunk_review" in prompt for prompt in fake.prompts), "chunk prompts should be sent"
    assert any("final_tournament" in prompt for prompt in fake.prompts), "final tournament prompt should be sent"
    all_seen = "\n".join(fake.prompts)
    assert "cand00" in all_seen and "cand89" in all_seen, "long candidate list must not lose tail candidates before review"
    assert review.get("recommended_candidate_ids"), review


if __name__ == "__main__":
    asyncio.run(_main())
    print("round237_candidate_adjudicator_context_chunking_tests: OK")
