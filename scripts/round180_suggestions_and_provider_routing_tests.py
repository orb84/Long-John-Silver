#!/usr/bin/env python3
"""Round 180 regressions for visible suggestion actions and source routing."""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.models import QualityProfile, SearchResult, Settings
# SearchAggregator imports the concrete BlacklistManager type for runtime wiring;
# the tests inject a minimal collaborator and do not need the DB-backed class.
sys.modules.setdefault("src.utils.blacklist", types.SimpleNamespace(BlacklistManager=object))
sys.modules.setdefault("src.core.release_groups", types.SimpleNamespace(ReleaseGroupTracker=object))
from src.search.aggregator import SearchAggregator


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class FakeBlacklist:
    def filter_results(self, results):
        return results


class FakeProvider:
    def __init__(self, name: str, results: list[SearchResult] | None = None):
        self.name = name
        self.results = results or []
        self.calls: list[str] = []

    async def search(self, query: str, category: str | None = None):
        self.calls.append(query)
        return self.results

    async def health_check(self) -> bool:
        return True


def test_suggestion_actions_have_blocking_overlay() -> None:
    source = read("src/web/static/js/components/suggestionsPanel.js")
    css = read("src/web/static/css/style.css")
    require("_actionBusy" in source, "SuggestionManager must track whether an action is in progress")
    require("_runLockedSuggestionAction" in source, "suggestion approve/deny should run through a shared UI lock")
    require("_setActionBusy(true" in source, "suggestion actions must enable the visible busy lock")
    require("suggestion-action-overlay" in source, "suggestion actions must render a blocking overlay")
    require("await this.load({ force: true })" in source, "suggestion actions should keep the lock until the refreshed list is loaded")
    require("is-action-busy" in css and "background: rgba(0, 0, 0" in css, "suggestions CSS must include the semi-transparent blocking cover")
    require("pointer-events: all" in css, "the busy overlay must intercept user input")


def test_direct_scrapers_are_opt_in_and_not_used_with_primary_provider() -> None:
    settings = Settings()
    require(settings.direct_scraper_fallback is False, "direct scraper fallback must be disabled by default")
    setup_html = read("src/web/templates/setup.html")
    require('id="setup-direct-scraper-fallback" checked' not in setup_html, "setup wizard must not opt users into direct scrapers by default")
    setup_js = read("src/web/static/js/pages/setup.js")
    require("directFallback ? directFallback.checked : false" in setup_js, "missing setup checkbox should not default direct scrapers on")

    primary = FakeProvider("Jackett", [])
    fallback = FakeProvider("BTDigg", [SearchResult(title="Example S01E01 1080p", magnet="magnet:?xt=urn:btih:abc", seeders=5)])
    aggregator = SearchAggregator(
        providers=[primary],
        fallback_providers=[fallback],
        blacklist=FakeBlacklist(),
        quality_profile=QualityProfile(min_seeders=0),
    )
    results = asyncio.run(aggregator.search("Example S01E01", category="tv"))
    require(primary.calls == ["Example S01E01"], "primary provider should be queried")
    require(fallback.calls == [], "direct fallback must not be queried while Jackett/primary provider is configured")
    require(results == [], "no direct-scraper result should leak into normal Jackett-backed searches")


def test_direct_scraper_emergency_path_only_when_no_primary_provider() -> None:
    fallback = FakeProvider("BTDigg", [SearchResult(title="Example S01E01 1080p", magnet="magnet:?xt=urn:btih:def", seeders=5)])
    aggregator = SearchAggregator(
        providers=[],
        fallback_providers=[fallback],
        blacklist=FakeBlacklist(),
        quality_profile=QualityProfile(min_seeders=0),
    )
    asyncio.run(aggregator.search("Example S01E01", category="tv"))
    require(fallback.calls == ["Example S01E01"], "explicit direct fallback should only run as an emergency when no primary provider exists")


if __name__ == "__main__":
    test_suggestion_actions_have_blocking_overlay()
    test_direct_scrapers_are_opt_in_and_not_used_with_primary_provider()
    test_direct_scraper_emergency_path_only_when_no_primary_provider()
    print("round180 suggestions/provider routing tests passed")
