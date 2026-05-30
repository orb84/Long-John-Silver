#!/usr/bin/env python3
"""Round 182 regression checks for suggestion UI feedback and TV search fallback."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_suggestion_overlay_is_visible_and_locks_input() -> None:
    js = read("src/web/static/js/components/suggestionsPanel.js")
    css = read("src/web/static/css/style.css")
    require("document.body.appendChild(this._actionOverlay)" in js, "suggestion overlay must be appended to body, not hidden inside a scrolled panel")
    require("_lockControls(root)" in js and "node.disabled = true" in js, "suggestion actions must lock buttons while running")
    require("position: fixed" in css and "z-index: 9999" in css, "suggestion overlay must cover the visible viewport")
    require("pointer-events: none" in css, "busy suggestion list must not accept clicks through overlay")


def test_item_inspector_exposes_suggestions() -> None:
    js = read("src/web/static/js/components/categoryItemDetailModal.js")
    require("/api/suggestions" in js and "item_id" in js, "item inspector must fetch item-scoped suggestions")
    require("Suggested next actions" in js, "item inspector must render a suggestions section")
    require("_approveSuggestion" in js and "_denySuggestion" in js, "item inspector suggestions must be actionable")
    require("category-detail-action-overlay" in js, "item inspector actions need a visible locked/loading overlay")


def test_jackett_timeout_is_not_generic_scraper_timeout() -> None:
    jackett = read("src/search/jackett.py")
    aggregator = read("src/search/aggregator.py")
    require("timeout: float = 75.0" in jackett, "Jackett default timeout must exceed slow all-indexer searches")
    require("self.timeout_seconds" in jackett, "Jackett must expose provider-specific timeout to aggregator")
    require("provider.search(q),\n                            timeout=self._provider_timeout_for(provider)" in aggregator, "all provider search paths must honor provider-specific timeouts")
    require("last_search_timed_out" in aggregator, "aggregator must expose timeout diagnostics to pipeline")


def test_tv_search_does_not_fan_out_after_provider_timeout_and_has_soulseek_fallback() -> None:
    pipeline = read("src/core/search_pipeline.py")
    tv = read("src/core/categories/tv_workflows.py")
    require("primary_timed_out" in pipeline and "skipping query-ladder fan-out" in pipeline, "pipeline must not run a long query ladder after Jackett timeout")
    require("_soulseek_fallback_for_episode" in tv, "TV workflow must attempt Soulseek after torrent miss")
    require("TV Soulseek fallback search" in tv, "TV Soulseek fallback must be logged")
    require("No torrent candidate was queued" in tv, "user receipt must say whether torrent failed and Soulseek was attempted")


def main() -> None:
    test_suggestion_overlay_is_visible_and_locks_input()
    test_item_inspector_exposes_suggestions()
    test_jackett_timeout_is_not_generic_scraper_timeout()
    test_tv_search_does_not_fan_out_after_provider_timeout_and_has_soulseek_fallback()
    print("round182 suggestions/jackett/soulseek regression tests passed")


if __name__ == "__main__":
    main()
