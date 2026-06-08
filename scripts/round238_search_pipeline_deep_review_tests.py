#!/usr/bin/env python3
"""Round 238 deep review regressions for download search/title/LLM review flow."""

from __future__ import annotations

import asyncio
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ai.download_candidate_adjudicator import DownloadCandidateAdjudicator
from src.core.categories.tv_agent import TvAgentSearchMixin
from src.core.scheduler_services import SchedulerCatalogService


@dataclass
class _Result:
    title: str
    magnet: str = "magnet:?xt=urn:btih:test"


class _Item:
    key = "A Knight the Seven Kingdoms"


class _TvHarness(TvAgentSearchMixin):
    category_id = "tv"

    @staticmethod
    def _safe_positive_int(value):
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None


class _TinyContextLLM:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def ensure_model_metadata_for_task(self, task: str) -> None:
        assert task == "torrent_ranker"

    def endpoint_context_limit_for_task(self, task: str) -> int:
        assert task == "torrent_ranker"
        return 4096

    async def completion(self, **kwargs: object) -> dict[str, object]:
        prompt = str((kwargs.get("messages") or [{}])[0].get("content") if kwargs.get("messages") else "")
        self.prompts.append(prompt)
        ids = re.findall(r'"candidate_id"\s*:\s*"([^"]+)"', prompt)
        # Deliberately return several ids from every chunk/tournament prompt so
        # a large finalist set remains and recursive tournament reduction is exercised.
        picks = ids[-8:] if ids else []
        return {
            "choices": [
                {"message": {"content": json.dumps({
                    "recommended_candidate_ids": picks,
                    "reject_candidate_ids": [],
                    "confidence": "medium",
                    "should_queue_now": False,
                    "needs_user_choice": False,
                    "reason": "selected candidates from this bounded prompt",
                    "answer_hint": "Use the reviewed candidate ordering.",
                })}}
            ]
        }


async def _test_recursive_candidate_tournament() -> None:
    fake = _TinyContextLLM()
    adjudicator = DownloadCandidateAdjudicator(fake)
    candidates = []
    for idx in range(120):
        candidates.append({
            "candidate_id": f"cand{idx:03d}",
            "index": idx + 1,
            "title": "A Knight of the Seven Kingdoms S01E01-06 ITA ENG " + ("long-title-fragment " * 16) + str(idx),
            "seeders": idx,
            "is_bundle": True,
        })
    review = await adjudicator.review(
        user_prompt="Can you grab A Knight of the Seven Kingdoms in Italian, full first season?",
        tool_arguments={"name": "A Knight of the Seven Kingdoms", "language": "Italian", "search_scope": "bundle_preferred"},
        search_result={"name": "A Knight of the Seven Kingdoms", "category_id": "tv", "language": "Italian"},
        candidates=candidates,
        category_guidance="Prefer Italian season/range packs over individual episodes.",
    )
    assert review, "review should be produced"
    assert review.get("candidate_count_reviewed") == len(candidates), review
    assert review.get("candidate_review_mode") == "chunked_tournament", review
    assert int(review.get("chunk_count") or 0) > 1, review
    assert int(review.get("tournament_round_count") or 0) >= 1, review
    assert review.get("finalist_omitted_due_to_context") == 0, review
    prompt_blob = "\n".join(fake.prompts)
    assert "cand000" in prompt_blob and "cand119" in prompt_blob, "tail candidates must reach an LLM prompt"


def _test_title_extraction_preserves_inner_of() -> None:
    cases = {
        "A Knight of the Seven Kingdoms": ("A Knight of the Seven Kingdoms", None, None),
        "A Knight of the Seven Kingdoms Season 1": ("A Knight of the Seven Kingdoms", 1, None),
        "season 1 of A Knight of the Seven Kingdoms": ("A Knight of the Seven Kingdoms", 1, None),
        "the first season of A Knight of the Seven Kingdoms": ("A Knight of the Seven Kingdoms", 1, None),
        "A Knight Of The Seven Kingdoms S01": ("A Knight Of The Seven Kingdoms", 1, None),
        "Star City S01E03": ("Star City", 1, 3),
    }
    for raw, expected in cases.items():
        assert SchedulerCatalogService.extract_structured_unit_from_name(raw, None, None) == expected


def _test_tv_llm_review_candidate_gate_does_not_crash_on_token_helpers() -> None:
    harness = _TvHarness()
    result = _Result("A Knight of the Seven Kingdoms S01e01-06 [1080p Ita Eng Spa h265 10bit SubS] byMe7alh")
    assert harness._is_llm_review_season_pack_candidate(result, 1, item=_Item(), language="Italian")


def _test_source_includes_review_status_and_no_global_of_deletion() -> None:
    scheduler_source = (ROOT / "src/core/scheduler_services.py").read_text()
    assert 're.sub(r"\\bof\\s+' not in scheduler_source, "must not globally delete interior 'of' title words"
    tool_source = (ROOT / "src/ai/tools/scheduling.py").read_text()
    assert "llm_candidate_review_status" in tool_source, "tool result should expose whether LLM candidate review ran"


async def _main() -> None:
    _test_title_extraction_preserves_inner_of()
    _test_tv_llm_review_candidate_gate_does_not_crash_on_token_helpers()
    _test_source_includes_review_status_and_no_global_of_deletion()
    await _test_recursive_candidate_tournament()


if __name__ == "__main__":
    asyncio.run(_main())
    print("round238_search_pipeline_deep_review_tests: OK")
