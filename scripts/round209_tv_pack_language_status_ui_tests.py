#!/usr/bin/env python3
"""Round 209 regression checks for TV season pack search and quiet UI maintenance."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.categories.tv import TvShowCategory
from src.core.categories.tv_bundle import TVBundleKnowledge
from src.core.domain_models.media import CategoryItem
from src.core.scheduler_services import SchedulerCatalogService
from src.ai.torrent_selection_prompt import TorrentSelectionPromptBuilder


class Row(SimpleNamespace):
    pass


def assert_true(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def assert_false(value: bool, message: str) -> None:
    if value:
        raise AssertionError(message)


async def check_query_planning() -> None:
    tv = TvShowCategory()
    item = CategoryItem(key="The Boys")
    english = await tv.agent_pack_search_queries(item, 1, language="English", context=None)
    assert_true(english[0] == "The Boys S01", f"manual-equivalent query not first: {english[:3]}")
    assert_true("The Boys Season 1" in english, f"missing season-word query: {english}")
    assert_false(any(" ITA" in q or "Italian" in q for q in english), f"English plan leaked Italian: {english}")
    assert_false(any(" ENG" in q or "English" in q for q in english), f"English plan should not hard-code ENG: {english}")
    assert_true(english.index("The Boys") == len(english) - 1, f"broad title must be final fallback: {english}")

    italian = await tv.agent_pack_search_queries(item, 1, language="Italian", context=None)
    assert_true(any(q == "The Boys S01 ITA" for q in italian), f"Italian plan missing ITA variant: {italian}")
    assert_true(any(q == "The Boys Season 1 Italian" for q in italian), f"Italian plan missing Italian variant: {italian}")


def check_season_title_extraction() -> None:
    cleaned, season, episode = SchedulerCatalogService.extract_structured_unit_from_name(
        "the first season of The Boys", None, None,
    )
    assert_true(season == 1 and episode is None, f"season extraction failed: {cleaned!r}, {season}, {episode}")
    assert_true(cleaned.casefold() == "the boys", f"title cleanup failed: {cleaned!r}")


def check_pack_detection_and_validation() -> None:
    tv = TvShowCategory()
    item = CategoryItem(key="The Boys")
    good = Row(title="The Boys S01 Complete 1080p WEB-DL", magnet="magnet:?xt=1")
    complete = Row(title="The Boys Complete Series S01-S05 1080p", magnet="magnet:?xt=2")
    adjacent = Row(title="The Boys Season 01 02 (2019 2020) [COMPLETE] 1080p", magnet="magnet:?xt=3")
    wrong_season = Row(title="The Boys S05 1080p WEB-DL", magnet="magnet:?xt=4")
    wrong_show = Row(title="The Hardy Boys S01 Complete", magnet="magnet:?xt=5")
    single_ep = Row(title="The Boys S01E01 1080p WEB-DL", magnet="magnet:?xt=6")

    assert_true(tv.validate_search_result_for_request(good, item, "Season 1"), "exact season pack rejected")
    assert_true(tv.validate_search_result_for_request(complete, item, "Season 1"), "complete-series pack rejected")
    assert_true(tv.validate_search_result_for_request(adjacent, item, "Season 1"), "adjacent season-list pack rejected")
    assert_false(tv.validate_search_result_for_request(wrong_season, item, "Season 1"), "wrong season accepted")
    assert_false(tv.validate_search_result_for_request(wrong_show, item, "Season 1"), "wrong show accepted")
    assert_false(tv.validate_search_result_for_request(single_ep, item, "Season 1"), "single episode accepted as season pack")

    detected = TVBundleKnowledge.detect_season_pack(adjacent.title)
    assert_true(bool(detected), "adjacent season list not detected")
    assert_true(detected["season_start"] == 1 and detected["season_end"] == 2, f"bad adjacent range: {detected}")


def check_prompt_language_rules() -> None:
    parts = "\n".join(TorrentSelectionPromptBuilder.language_and_quality_parts("English", "1080p"))
    assert_false("is ideal" in parts, "dual-audio should not be described as ideal")
    assert_true("acceptable, not ideal" in parts, "dual-audio demotion language missing")


def check_scheduler_status_and_css() -> None:
    scheduler = (ROOT / "src/core/scheduler.py").read_text()
    assert_true("Suggestion compilation skipped: no tracked/library items." in scheduler, "empty suggestion skip missing")
    assert_true("if force:" in scheduler and "Compiling suggestions" in scheduler, "forced suggestion status missing")

    css = (ROOT / "src/web/static/css/style.css").read_text()
    for needle in [
        "#helm.view",
        "overflow: hidden;",
        "grid-template-columns: minmax(0, 1fr) minmax(280px, 360px);",
        ".chat-container",
        "min-height: 0;",
        ".chat-input-area",
        "flex-shrink: 0;",
        ".widgets-container",
        "overflow-y: auto;",
    ]:
        assert_true(needle in css, f"CSS layout guard missing: {needle}")


async def main() -> None:
    check_season_title_extraction()
    await check_query_planning()
    check_pack_detection_and_validation()
    check_prompt_language_rules()
    check_scheduler_status_and_css()
    print("round209 tv pack/language/status/ui tests: PASS")


if __name__ == "__main__":
    asyncio.run(main())
