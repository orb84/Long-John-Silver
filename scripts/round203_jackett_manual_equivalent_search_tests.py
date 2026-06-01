#!/usr/bin/env python3
"""Round 204 regression tests for Jackett search cleanup.

These tests lock the intended contract after the Mac/Linux Jackett regression:
* the aggregate v188 endpoint is still the first search path;
* TV season-pack query generation starts with a broad manual-search title;
* degraded/empty primary diagnostics permit emergency fallback.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import types
sys.modules.setdefault("aiosqlite", types.SimpleNamespace(Connection=object, Cursor=object))

from src.search.jackett import JackettSearch
from src.search.aggregator import SearchAggregator
from src.core.categories.tv import TvShowCategory
from src.core.models import ProviderSearchDiagnostics


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_jackett_keeps_v188_aggregate_endpoint_contract() -> None:
    provider = JackettSearch("http://127.0.0.1:9117", "secret", configured_indexers=107)
    require(provider.timeout_seconds >= 80, "aggregator must not kill Jackett before its v188-compatible timeout")
    variants = provider._query_variants("The Boys S01 Complete", category="tv")  # type: ignore[attr-defined]
    require(variants == ["The Boys S01 Complete", "The Boys"], "direct recovery should add broad title without replacing original query")


def test_native_json_parser_keeps_http_torrent_links() -> None:
    provider = JackettSearch("http://127.0.0.1:9117", "secret", configured_indexers=107)
    rows = provider._parse_payload({"Results": [{  # type: ignore[attr-defined]
        "Title": "The Boys S01 1080p Complete",
        "Link": "http://127.0.0.1:9117/dl/test.torrent",
        "Details": "https://example.invalid/details",
        "Size": 123456789,
        "Seeders": 321,
        "Tracker": "1337x",
    }]}, source_prefix="Jackett")
    require(len(rows) == 1, "native JSON Results should parse")
    require(rows[0].url == "https://example.invalid/details", "details link should be preserved for display")
    require(rows[0].size_bytes == 123456789, "size bytes should parse")
    require(rows[0].seeders == 321, "seeders should parse")


async def test_tv_pack_queries_start_with_broad_manual_title() -> None:
    category = TvShowCategory()
    item = SimpleNamespace(key="The Boys")
    queries = await category.agent_pack_search_queries(item, 1, language=None, context=None)
    require(queries[0] == "The Boys", "season pack search should start with broad manual-search title")
    require("The Boys S01" in queries, "season-coded form should still be included")


def test_empty_primary_diagnostics_allow_emergency_fallback() -> None:
    aggregator = SearchAggregator(providers=[], fallback_providers=[], blacklist=SimpleNamespace())
    diagnostics = {
        "Jackett": ProviderSearchDiagnostics(
            provider="Jackett",
            ok=True,
            result_count=0,
            magnet_count=0,
            blocked_reason="empty",
            used_browser=False,
            elapsed_ms=100,
        )
    }
    require(aggregator._should_use_emergency_fallback(diagnostics), "empty/degraded primary search should not block emergency fallback")  # type: ignore[attr-defined]


async def main() -> None:
    test_jackett_keeps_v188_aggregate_endpoint_contract()
    test_native_json_parser_keeps_http_torrent_links()
    await test_tv_pack_queries_start_with_broad_manual_title()
    test_empty_primary_diagnostics_allow_emergency_fallback()
    print("round204_jackett_v188_search_baseline_tests: PASS")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
