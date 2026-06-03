#!/usr/bin/env python3
"""Round 212 regression checks for RSS release-event safety."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.categories.registry import CategoryRegistry
from src.core.models import SearchResult
from src.search.rss_monitor import RSSMonitor
class DummySupervisor:
    def spawn_restartable(self, *args, **kwargs):
        return None


class FakeRSSMonitor(RSSMonitor):
    def __init__(self, *args, feed_items_by_url: dict[str, list[SearchResult]], **kwargs):
        super().__init__(*args, **kwargs)
        self._feed_items_by_url = feed_items_by_url

    async def _fetch_feed(self, url: str) -> list[SearchResult]:
        return list(self._feed_items_by_url.get(url, []))


def test_rss_callback_receives_search_result_not_unit_string() -> None:
    calls: list[tuple[str, SearchResult, str | None]] = []

    async def on_match(name: str, result: SearchResult, unit_label: str | None = None) -> None:
        calls.append((name, result, unit_label))

    url = "http://jackett/rss?q=Battlestar+Galactica"
    monitor = FakeRSSMonitor(
        [url],
        ["Battlestar Galactica"],
        DummySupervisor(),
        on_match=on_match,
        category_registry=CategoryRegistry.with_defaults(),
        item_categories={"Battlestar Galactica": "tv"},
        feed_targets={url: ["Battlestar Galactica"]},
        feed_items_by_url={
            url: [SearchResult(title="Battlestar Galactica S03E06 Torn 1080p HEVC x265", size="Unknown", source="rss")]
        },
    )
    asyncio.run(monitor._poll_all_feeds())
    assert len(calls) == 1
    name, result, unit_label = calls[0]
    assert name == "Battlestar Galactica"
    assert isinstance(result, SearchResult)
    assert result.title.startswith("Battlestar Galactica")
    assert unit_label == "S03E06"


def test_category_scoped_matching_uses_parsed_title_not_raw_substring() -> None:
    monitor = RSSMonitor(
        ["u"],
        ["The Wire"],
        DummySupervisor(),
        category_registry=CategoryRegistry.with_defaults(),
        item_categories={"The Wire": "tv"},
        feed_targets={"u": ["The Wire"]},
    )
    false_positive = SearchResult(
        title="Wicked Attraction S03E05 Beyond the Wire 1080p AMZN WEB-DL",
        size="Unknown",
        source="rss",
    )
    real_match = SearchResult(title="The Wire S03E05 1080p WEB-DL", size="Unknown", source="rss")
    assert monitor._match_items([false_positive], candidate_names=["The Wire"]) == []
    matches = monitor._match_items([real_match], candidate_names=["The Wire"])
    assert len(matches) == 1
    assert matches[0][0] == "The Wire"
    assert matches[0][2] == "S03E05"


def test_feed_rotation_is_bounded_and_item_scoped() -> None:
    urls = [f"u{i}" for i in range(10)]
    monitor = RSSMonitor(
        urls,
        [f"Show {i}" for i in range(10)],
        DummySupervisor(),
        feed_targets={url: [f"Show {i}"] for i, url in enumerate(urls)},
        max_feeds_per_cycle=3,
    )
    assert monitor._feeds_for_cycle() == ["u0", "u1", "u2"]
    assert monitor._feeds_for_cycle() == ["u3", "u4", "u5"]
    assert monitor._feeds_for_cycle() == ["u6", "u7", "u8"]
    assert monitor._feeds_for_cycle() == ["u9", "u0", "u1"]


def test_scheduler_source_result_serialization_is_defensive() -> None:
    scheduler_py = Path("src/core/scheduler.py").read_text(encoding="utf-8")
    assert "def _serialize_source_result" in scheduler_py
    assert "dict(source_result or {})" not in scheduler_py
    assert 'return {"value": str(source_result)}' in scheduler_py


def test_main_no_empty_jackett_rss_poll_is_used() -> None:
    main_py = Path("main.py").read_text(encoding="utf-8")
    scheduler_py = Path("src/core/scheduler.py").read_text(encoding="utf-8")
    # RSS starts empty and category watch-policy sync supplies item-scoped
    # queries; startup must not create one broad empty /all feed.
    assert "feed_urls=[]" in main_py
    assert "sync_all_category_watch_policies" in main_py
    assert "quote_plus(query)" in scheduler_py
    assert "&t=search&q={quote_plus(query)}" in scheduler_py
    assert "feed_urls=[rss_feed_url]" not in main_py


def main() -> None:
    test_rss_callback_receives_search_result_not_unit_string()
    test_category_scoped_matching_uses_parsed_title_not_raw_substring()
    test_feed_rotation_is_bounded_and_item_scoped()
    test_scheduler_source_result_serialization_is_defensive()
    test_main_no_empty_jackett_rss_poll_is_used()
    print("round212 rss monitor/release-event tests: PASS")


if __name__ == "__main__":
    main()
