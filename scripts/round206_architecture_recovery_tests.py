#!/usr/bin/env python3
"""Round 206 regression tests for the search/session recovery architecture.

These tests avoid the full app dependency graph so they can run in the
packaging sandbox. They lock the two key regressions from the latest logs:

* Jackett ``/all`` must not block for its full timeout before manual-equivalent
  direct indexer search can return results.
* Conversation recording must ensure a session before inserting turns; this is
  asserted by static contract checks because the sandbox lacks aiosqlite.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import src.search.jackett as jackett_module
from src.search.jackett import JackettSearch


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class SlowAggregateFastDirectClient:
    calls: list[tuple[str, dict]] = []

    def __init__(self, *_, **__):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def get(self, url: str, params: dict | None = None, **_) -> httpx.Response:
        params = dict(params or {})
        SlowAggregateFastDirectClient.calls.append((url, params))
        path = urlparse(url).path
        request = httpx.Request("GET", url)

        if path.endswith("/api/v2.0/indexers/all/results"):
            await asyncio.sleep(10.0)
            return httpx.Response(200, json={"Results": []}, request=request)

        if path.endswith("/api/v2.0/indexers/all/results/torznab/api") and params.get("t") == "indexers":
            xml = """<indexers><indexer id='1337x' name='1337x' type='public' /></indexers>"""
            return httpx.Response(200, text=xml, headers={"content-type": "application/xml"}, request=request)

        if path.endswith("/api/v2.0/indexers/1337x/results"):
            return httpx.Response(200, json={"Results": [{
                "Title": "The Boys S01 1080p BluRay x265",
                "Link": "http://127.0.0.1:9117/dl/the-boys-s01.torrent",
                "Size": 1234567890,
                "Seeders": 456,
                "Tracker": "1337x",
            }]}, request=request)

        return httpx.Response(404, request=request)


async def test_direct_manual_parity_returns_before_slow_all() -> None:
    original_client = jackett_module.httpx.AsyncClient
    SlowAggregateFastDirectClient.calls = []
    jackett_module.httpx.AsyncClient = SlowAggregateFastDirectClient  # type: ignore[assignment]
    try:
        provider = JackettSearch("http://127.0.0.1:9117", "secret", timeout=75, configured_indexers=1)
        started = time.monotonic()
        rows = await provider.search("The Boys S01", category="tv")
        elapsed = time.monotonic() - started
        require(rows and rows[0].title.startswith("The Boys S01"), "direct manual-parity result should be returned")
        require(elapsed < 3.0, f"search waited for slow aggregate instead of direct result: {elapsed:.2f}s")
        paths = [urlparse(url).path for url, _ in SlowAggregateFastDirectClient.calls]
        require(any(path.endswith("/api/v2.0/indexers/all/results") for path in paths), "v188 aggregate endpoint must still be started")
        require(any(path.endswith("/api/v2.0/indexers/1337x/results") for path in paths), "direct configured-indexer endpoint must be tried")
    finally:
        jackett_module.httpx.AsyncClient = original_client  # type: ignore[assignment]


def test_session_invariant_is_centralized() -> None:
    conversation_py = Path("src/core/conversation.py").read_text()
    user_repo_py = Path("src/core/repositories/user.py").read_text()
    require("async def ensure_session" in conversation_py, "ConversationManager must expose ensure_session")
    require("await self.ensure_session(session_id)" in conversation_py, "add_turn must ensure session before FK insert")
    require("async def ensure_session" in user_repo_py, "UserRepository must own session upsert invariant")
    require("local-session-placeholder" in user_repo_py, "local fallback user must be explicit and auditable")


async def main() -> None:
    await test_direct_manual_parity_returns_before_slow_all()
    test_session_invariant_is_centralized()
    print("round206_architecture_recovery_tests: PASS")


if __name__ == "__main__":
    asyncio.run(main())
