#!/usr/bin/env python3
"""Round 235 regression tests for TV pack recall after lossy LLM titles."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ai.media_title_repair import MediaTitleRepair
from src.core.categories.tv_agent import TvAgentSearchMixin
from src.core.categories.tv_bundle import TVBundleKnowledge


@dataclass
class _Result:
    title: str
    magnet: str = "magnet:?xt=urn:btih:test"


class _TvHarness(TvAgentSearchMixin):
    category_id = "tv"

    @staticmethod
    def _safe_positive_int(value):
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None


class _Item:
    key = "A Knight the Seven Kingdoms"


def test_media_title_repair_recovers_dropped_inner_stopword() -> None:
    prompt = "Can you please grab me A Knight of the Seven Kingdoms in italian ? Full first season"
    assert MediaTitleRepair.recover_literal_title("A Knight the Seven Kingdoms", prompt) == "A Knight of the Seven Kingdoms"
    tv = _TvHarness()
    normalized = tv.normalize_agent_search_name_argument("A Knight the Seven Kingdoms Season 1", user_prompt=prompt)
    assert normalized == "A Knight the Seven Kingdoms"
    assert MediaTitleRepair.recover_literal_title(normalized, prompt) == "A Knight of the Seven Kingdoms"


def test_tv_title_match_allows_missing_stopword_but_not_single_token_article_titles() -> None:
    assert TvAgentSearchMixin._title_matches_requested_series(
        "A Knight of the Seven Kingdoms S01e01-06 [1080p Ita Eng Spa h265 10bit SubS] byMe7alh [MIRCrew]",
        "A Knight the Seven Kingdoms",
    )
    assert not TvAgentSearchMixin._title_matches_requested_series("The Hardy Boys S01", "The Boys")


def test_tv_episode_range_pack_is_relevant_with_lossy_title() -> None:
    harness = _TvHarness()
    result = _Result("A Knight of the Seven Kingdoms S01e01-06 [1080p Ita Eng Spa h265 10bit SubS] byMe7alh [MIRCrew]")
    assert TVBundleKnowledge.detect_season_pack(result.title)["pack_type"] == "partial_range"
    assert harness._is_relevant_season_pack_result(result, 1, item=_Item())


def main() -> None:
    test_media_title_repair_recovers_dropped_inner_stopword()
    test_tv_title_match_allows_missing_stopword_but_not_single_token_article_titles()
    test_tv_episode_range_pack_is_relevant_with_lossy_title()
    print("round235 TV pack title recall tests passed")


if __name__ == "__main__":
    main()
