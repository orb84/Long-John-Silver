#!/usr/bin/env python3
"""Round 259 regression for TV auto-download toggle watch resync.

The Round 258 UI checkbox could persist settings but then fail during watch-policy
RSS resync because ``MediaScheduler._rss_url_for_query`` referenced
``quote_plus`` without importing it.  The test calls that helper directly so a
missing import cannot survive compile-only validation.
"""
from __future__ import annotations

from pathlib import Path
import sys
import types
from urllib.parse import quote_plus as stdlib_quote_plus

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("aiosqlite", types.SimpleNamespace(Connection=object, Row=dict, Cursor=object))

from src.core.scheduler import MediaScheduler


class Check:
    """Small assertion collector for script-style regression checks."""

    def __init__(self) -> None:
        self.failures: list[str] = []

    def ok(self, condition: bool, message: str) -> None:
        """Record a failure when ``condition`` is false."""
        if not condition:
            self.failures.append(message)

    def finish(self) -> None:
        """Exit non-zero when any check failed."""
        if self.failures:
            print("Round 259 TV auto-download toggle failures:")
            for failure in self.failures:
                print(f" - {failure}")
            raise SystemExit(1)
        print("round259_tv_auto_download_toggle_import_tests: OK")


class FakeSettings:
    """Settings double for RSS URL construction."""

    def __init__(self) -> None:
        self.jackett_url = "http://localhost:9117/"
        self.jackett_api_key = "abc+123 key"


class FakeSettingsManager:
    """Settings manager double for an uninitialized scheduler helper."""

    def __init__(self) -> None:
        self.settings = FakeSettings()


def scheduler_for_helper() -> MediaScheduler:
    """Create a mostly-uninitialized scheduler for pure helper calls."""
    scheduler = object.__new__(MediaScheduler)
    scheduler._settings_manager = FakeSettingsManager()
    return scheduler


def test_rss_url_for_query_encodes_without_name_error(check: Check) -> None:
    """Verify the TV watch-policy RSS helper has its URL encoder in scope."""
    scheduler = scheduler_for_helper()
    query = "Star City S01E03 ITA ENG"
    url = scheduler._rss_url_for_query(query)
    check.ok("/api/v2.0/indexers/all/results/torznab/api" in url, "RSS URL should target Jackett Torznab API")
    check.ok(f"apikey={stdlib_quote_plus('abc+123 key')}" in url, "RSS URL should encode the Jackett API key")
    check.ok(f"q={stdlib_quote_plus(query)}" in url, "RSS URL should encode the release-watch query")


def test_source_imports_quote_plus(check: Check) -> None:
    """Keep the direct import that prevents runtime NameError on checkbox toggles."""
    source = (ROOT / "src/core/scheduler.py").read_text(encoding="utf-8")
    check.ok("from urllib.parse import quote_plus" in source, "scheduler must import quote_plus for RSS resync")


if __name__ == "__main__":
    check = Check()
    test_rss_url_for_query_encodes_without_name_error(check)
    test_source_imports_quote_plus(check)
    check.finish()
