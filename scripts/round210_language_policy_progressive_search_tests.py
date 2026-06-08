#!/usr/bin/env python3
"""Round 210 tests for language-query hygiene and faster Jackett direct recovery."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
import src.search.jackett as jackett_module
from src.core.categories.tv import TvShowCategory
from src.core.domain_models.media import CategoryItem
from src.search.jackett import JackettSearch


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def check_language_query_policy() -> None:
    tv = TvShowCategory()
    item = CategoryItem(key="The Boys")

    english_pack = await tv.agent_pack_search_queries(item, 1, language="English", context=None)
    english_episode = tv.build_alternative_search_queries(item, "S01E01", "English")
    english_joined = "\n".join(english_pack + english_episode)
    for forbidden in (" ITA", "Italian", " ENG", "English", " MULTI"):
        require(forbidden not in english_joined, f"English query plan leaked {forbidden!r}: {english_joined}")

    french_pack = await tv.agent_pack_search_queries(item, 1, language="French", context=None)
    french_joined = "\n".join(french_pack)
    require(any(q.endswith("FRE") for q in french_pack), f"French search tag missing: {french_pack}")
    require(any(q.endswith("French") for q in french_pack), f"French word variant missing: {french_pack}")
    for forbidden in (" ITA", "Italian", " Spanish", " SPA", " MULTI"):
        require(forbidden not in french_joined, f"French plan leaked unrelated token {forbidden!r}: {french_joined}")

    italian_pack = await tv.agent_pack_search_queries(item, 1, language="Italian", context=None)
    italian_joined = "\n".join(italian_pack)
    require(any(q.endswith("ITA") for q in italian_pack), f"Italian tag missing: {italian_pack}")
    require(any(q.endswith("Italian") for q in italian_pack), f"Italian word variant missing: {italian_pack}")
    require(" MULTI" not in italian_joined, f"MULTI should be candidate evidence, not query text: {italian_pack}")


class ProgressiveFakeClient:
    calls: list[tuple[str, dict]] = []

    def __init__(self, *_, **__):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def get(self, url: str, params: dict | None = None, **_) -> httpx.Response:
        params = dict(params or {})
        ProgressiveFakeClient.calls.append((url, params))
        path = urlparse(url).path
        request = httpx.Request("GET", url)

        if path.endswith("/api/v2.0/indexers/all/results"):
            raise httpx.TimeoutException("aggregate hung", request=request)

        if path.endswith("/api/v2.0/indexers/all/results/torznab/api") and params.get("t") == "indexers":
            xml = "<indexers><indexer id='alpha'/><indexer id='beta'/><indexer id='slowpoke'/></indexers>"
            return httpx.Response(200, text=xml, headers={"content-type": "application/xml"}, request=request)

        if "/api/v2.0/indexers/alpha/results" in path or "/api/v2.0/indexers/beta/results" in path:
            selector = "alpha" if "alpha" in path else "beta"
            rows = []
            for idx in range(15):
                rows.append({
                    "Title": f"The Boys S01 {selector} result {idx:02d} 1080p",
                    "Link": f"magnet:?xt=urn:btih:{selector}{idx:02d}",
                    "Size": 1000000000 + idx,
                    "Seeders": 100 - idx,
                    "Tracker": selector,
                })
            return httpx.Response(200, json={"Results": rows}, request=request)

        if "/api/v2.0/indexers/slowpoke/results" in path:
            raise httpx.TimeoutException("slow indexer", request=request)

        return httpx.Response(404, request=request)


async def check_jackett_progressive_direct_recovery() -> None:
    original_client = jackett_module.httpx.AsyncClient
    ProgressiveFakeClient.calls = []
    jackett_module.httpx.AsyncClient = ProgressiveFakeClient  # type: ignore[assignment]
    try:
        provider = JackettSearch("http://127.0.0.1:9117", "secret", configured_indexers=3)
        rows = await provider.search("The Boys S01", category="tv")
        require(len(rows) >= 20, f"progressive direct recovery should return an early useful batch, got {len(rows)}")
        paths = [urlparse(url).path for url, _ in ProgressiveFakeClient.calls]
        require(paths[0].endswith("/api/v2.0/indexers/all/results"), "v188 aggregate request must still start first")
        require(any("/indexers/alpha/results" in path for path in paths), "direct configured indexer alpha not queried")
        require(any("/indexers/beta/results" in path for path in paths), "direct configured indexer beta not queried")
    finally:
        jackett_module.httpx.AsyncClient = original_client  # type: ignore[assignment]


async def main() -> None:
    await check_language_query_policy()
    await check_jackett_progressive_direct_recovery()
    print("round210 language policy/progressive search tests: PASS")


if __name__ == "__main__":
    asyncio.run(main())
