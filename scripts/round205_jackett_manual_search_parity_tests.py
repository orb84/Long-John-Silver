#!/usr/bin/env python3
"""Round 205 tests for Jackett manual-search parity.

These lock the actual failure reported from the Mac logs: Jackett manual search
for "The Boys S01" can return many rows, while LJS was converting a 75-second
aggregate timeout into a misleading empty result.  The provider must keep the
v188 aggregate request first, then verify empty/degraded aggregate responses by
querying Jackett's configured indexers directly.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import src.search.jackett as jackett_module
from src.search.jackett import JackettSearch


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class FakeAsyncClient:
    calls: list[tuple[str, dict]] = []
    mode = "timeout_then_direct_json"

    def __init__(self, *_, **__):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def get(self, url: str, params: dict | None = None, **_) -> httpx.Response:
        params = dict(params or {})
        FakeAsyncClient.calls.append((url, params))
        path = urlparse(url).path
        request = httpx.Request("GET", url)

        if path.endswith("/api/v2.0/indexers/all/results"):
            if FakeAsyncClient.mode == "timeout_then_direct_json":
                raise httpx.TimeoutException("aggregate hung", request=request)
            return httpx.Response(200, json={"Results": []}, request=request)

        if path.endswith("/api/v2.0/indexers/all/results/torznab/api") and params.get("t") == "indexers":
            xml = """<?xml version='1.0' encoding='UTF-8'?>
            <indexers>
              <indexer id='1337x' name='1337x' type='public' />
            </indexers>"""
            return httpx.Response(200, text=xml, headers={"content-type": "application/xml"}, request=request)

        if path.endswith("/api/v2.0/indexers/1337x/results"):
            return httpx.Response(200, json={"Results": [{
                "Title": "The Boys S01 1080p BluRay x265",
                "Link": "http://127.0.0.1:9117/dl/the-boys-s01.torrent",
                "Details": "https://example.invalid/the-boys-s01",
                "Size": 1234567890,
                "Seeders": 456,
                "Tracker": "1337x",
            }]}, request=request)

        return httpx.Response(404, json={"error": "not configured"}, request=request)


class FakeTorznabOnlyClient(FakeAsyncClient):
    calls: list[tuple[str, dict]] = []

    async def get(self, url: str, params: dict | None = None, **_) -> httpx.Response:
        params = dict(params or {})
        FakeTorznabOnlyClient.calls.append((url, params))
        path = urlparse(url).path
        request = httpx.Request("GET", url)

        if path.endswith("/api/v2.0/indexers/all/results"):
            return httpx.Response(200, json={"Results": []}, request=request)
        if path.endswith("/api/v2.0/indexers/all/results/torznab/api") and params.get("t") == "indexers":
            xml = """<indexers><indexer id='eztv' name='EZTV' type='public' /></indexers>"""
            return httpx.Response(200, text=xml, headers={"content-type": "application/xml"}, request=request)
        if path.endswith("/api/v2.0/indexers/eztv/results"):
            return httpx.Response(200, json={"Results": []}, request=request)
        if path.endswith("/api/v2.0/indexers/eztv/results/torznab/api"):
            xml = """<?xml version='1.0' encoding='UTF-8'?>
            <rss><channel><item>
              <title>The Boys S01 Complete 1080p</title>
              <link>http://127.0.0.1:9117/dl/eztv-the-boys.torrent</link>
              <guid>eztv-the-boys-s01</guid>
              <torznab:attr xmlns:torznab='http://torznab.com/schemas/2015/feed' name='seeders' value='99'/>
              <torznab:attr xmlns:torznab='http://torznab.com/schemas/2015/feed' name='size' value='555000000'/>
            </item></channel></rss>"""
            return httpx.Response(200, text=xml, headers={"content-type": "application/xml"}, request=request)
        return httpx.Response(404, request=request)


async def test_aggregate_timeout_does_not_become_zero_results() -> None:
    original_client = jackett_module.httpx.AsyncClient
    FakeAsyncClient.calls = []
    FakeAsyncClient.mode = "timeout_then_direct_json"
    jackett_module.httpx.AsyncClient = FakeAsyncClient  # type: ignore[assignment]
    try:
        provider = JackettSearch("http://127.0.0.1:9117", "secret", configured_indexers=1)
        rows = await provider.search("The Boys S01", category="tv")
        require(len(rows) == 1, "aggregate timeout must fall back to configured-indexer direct search")
        require(rows[0].title.startswith("The Boys S01"), "direct JSON result should be returned")
        require(provider.latest_error_category in ("", None), "successful recovery should clear error category")
        paths = [urlparse(url).path for url, _ in FakeAsyncClient.calls]
        require("/api/v2.0/indexers/all/results" in paths[0], "v188 aggregate endpoint must remain first")
        require(any("/api/v2.0/indexers/1337x/results" in path for path in paths), "direct configured indexer endpoint must be tried")
    finally:
        jackett_module.httpx.AsyncClient = original_client  # type: ignore[assignment]


async def test_empty_aggregate_checks_direct_torznab_compatibility() -> None:
    original_client = jackett_module.httpx.AsyncClient
    FakeTorznabOnlyClient.calls = []
    jackett_module.httpx.AsyncClient = FakeTorznabOnlyClient  # type: ignore[assignment]
    try:
        provider = JackettSearch("http://127.0.0.1:9117", "secret", configured_indexers=1)
        rows = await provider.search("The Boys S01", category="tv")
        require(len(rows) == 1, "empty aggregate must be verified through direct Torznab fallback")
        require(rows[0].seeders == 99, "Torznab seeders should parse")
        paths = [urlparse(url).path for url, _ in FakeTorznabOnlyClient.calls]
        require(any(path.endswith("/api/v2.0/indexers/eztv/results/torznab/api") for path in paths), "Torznab direct fallback endpoint must be tried")
    finally:
        jackett_module.httpx.AsyncClient = original_client  # type: ignore[assignment]


async def main() -> None:
    await test_aggregate_timeout_does_not_become_zero_results()
    await test_empty_aggregate_checks_direct_torznab_compatibility()
    print("round205_jackett_manual_search_parity_tests: PASS")


if __name__ == "__main__":
    asyncio.run(main())
