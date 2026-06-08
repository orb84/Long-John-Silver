#!/usr/bin/env python3
"""Regression checks for Round 236 LLM-led torrent candidate adjudication."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ai.download_candidate_adjudicator import DownloadCandidateAdjudicator
from src.ai.tools.scheduling import SchedulingToolProvider
from src.core.categories.tv import TvShowCategory


class _FakeLLM:
    async def completion(self, **_: object) -> dict[str, object]:
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "recommended_candidate_ids": ["pack1080"],
                                "reject_candidate_ids": ["episode1"],
                                "confidence": "high",
                                "should_queue_now": False,
                                "needs_user_choice": False,
                                "reason": "The S01E01-06 pack explicitly includes ITA and covers the requested full season.",
                                "answer_hint": "Recommend the Italian S01E01-06 season pack before single episodes.",
                            }
                        )
                    }
                }
            ]
        }


async def _main() -> None:
    root = Path(__file__).resolve().parents[1]
    scheduling_text = (root / "src/ai/tools/scheduling.py").read_text()
    assert "DownloadCandidateAdjudicator" in scheduling_text, "search_media_torrents must call the LLM candidate adjudicator"
    assert "llm_candidate_review" in scheduling_text, "tool result must expose LLM candidate review"
    assert "review_llm_recommended_candidate" in scheduling_text, "tool result must expose review next action"

    provider = SchedulingToolProvider(llm_client=_FakeLLM())
    tools = provider.get_tools()
    search_tool = next(tool for tool in tools if getattr(tool, "name", "") == "search_media_torrents")
    assert getattr(search_tool, "_candidate_adjudicator", None) is not None, "Scheduling provider must pass LLM client into search tool"

    adjudicator = DownloadCandidateAdjudicator(_FakeLLM())
    candidates = [
        {"candidate_id": "episode1", "title": "A Knight of the Seven Kingdoms S01E01 [1080p Ita Eng Spa]", "seeders": 74},
        {"candidate_id": "pack1080", "title": "A Knight of the Seven Kingdoms S01e01-06 [1080p Ita Eng Spa h265 10bit SubS]", "seeders": 100, "is_bundle": True},
    ]
    review = await adjudicator.review(
        user_prompt="Can you please grab me A Knight of the Seven Kingdoms in italian? Full first season",
        tool_arguments={"name": "A Knight of the Seven Kingdoms", "language": "Italian", "search_scope": "bundle_preferred"},
        search_result={"name": "A Knight of the Seven Kingdoms", "category_id": "tv", "language": "Italian"},
        candidates=candidates,
        category_guidance="TV full-season requests should prefer season packs that cover the requested season.",
    )
    assert review and review["recommended_candidate_ids"] == ["pack1080"], review
    reordered = DownloadCandidateAdjudicator.reorder_candidates(candidates, review)
    assert reordered[0]["candidate_id"] == "pack1080", reordered

    tv = TvShowCategory()
    item = tv.create_item("A Knight the Seven Kingdoms")
    result = type("Candidate", (), {"title": "A Knight of the Seven Kingdoms S01e01-06 [1080p Ita Eng Spa h265 10bit SubS]", "magnet": "magnet:?xt=urn:btih:abc"})()
    assert tv._is_llm_review_season_pack_candidate(result, 1, item=item, language="Italian"), "plausible ITA season range must reach LLM review workspace"


if __name__ == "__main__":
    asyncio.run(_main())
    print("round236_llm_candidate_adjudication_tests: OK")
