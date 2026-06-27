#!/usr/bin/env python3
"""Round 202 regression tests for Jackett/Soulseek search orchestration."""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Test environment shim: importing BlacklistManager pulls the DB module, but
# these orchestration tests do not touch SQLite.
import types
sys.modules.setdefault("aiosqlite", types.SimpleNamespace(Connection=object, Cursor=object))

from src.core.domain_models.downloads import SearchResult
from src.core.models import QualityProfile
from src.search.aggregator import SearchAggregator
from src.search.base import SearchProvider
from src.search.jackett import JackettSearch
from src.utils.blacklist import BlacklistManager
TV_CATEGORY_ID = "t" + "v"


class EmptyPrimary(SearchProvider):
    @property
    def name(self) -> str:
        return "Primary"

    @property
    def supported_categories(self) -> list[str]:
        return ["*"]

    async def search(self, query: str, category: str | None = None):
        self.record_error_category("timeout")
        return []

    async def health_check(self) -> bool:
        return True


class FallbackProvider(SearchProvider):
    @property
    def name(self) -> str:
        return "Fallback"

    @property
    def supported_categories(self) -> list[str]:
        return ["*"]

    async def search(self, query: str, category: str | None = None):
        return [SearchResult(title=f"{query} fallback result", magnet="magnet:?xt=urn:btih:abc", seeders=5, source="fallback")]

    async def health_check(self) -> bool:
        return True


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def test_jackett_uses_v188_aggregate_before_recovery() -> None:
    provider = JackettSearch("http://127.0.0.1:9117", "key", configured_indexers=100)
    calls: list[str] = []

    async def fake_aggregate(query: str):
        calls.append("aggregate:" + query)
        return [SearchResult(title="The Boys S01 Complete 1080p", magnet="magnet:?xt=urn:btih:111", source="Jackett")], None

    async def fail_recovery(*args, **kwargs):
        raise AssertionError("direct recovery should not run after aggregate success")

    provider._search_aggregate = fake_aggregate  # type: ignore[method-assign]
    provider._search_direct_recovery = fail_recovery  # type: ignore[method-assign]
    results = await provider.search("The Boys S01", category="tv")
    require(results and results[0].title.startswith("The Boys"), "aggregate should return v188-compatible Jackett results")
    require(calls == ["aggregate:The Boys S01"], "aggregate all should be the first and only path on success")


async def test_aggregator_uses_emergency_fallback_when_primary_empty() -> None:
    agg = SearchAggregator(
        [EmptyPrimary()],
        BlacklistManager(None),
        QualityProfile(),
        fallback_providers=[FallbackProvider()],
        provider_timeout=3,
    )
    results = await agg.search("The Boys S01", category="tv")
    require(results, "fallback should run when primary provider is empty/degraded")
    require(results[0].source == "fallback", "fallback result should be returned")


async def test_scheduler_soulseek_companion_runs_in_parallel() -> None:
    from src.core.scheduler_services import SchedulerTorrentSearchService, SchedulerServiceContext

    class FakeCategory:
        category_id = "tv"
        def create_item(self, name, language=None):
            return SimpleNamespace(key=name, category_id="tv", item_type="tv", language=language or "English")
        def normalize_search_language(self, value, explicit=False):
            return value

    class FakeRegistry:
        def get(self, category_id):
            return FakeCategory() if category_id == TV_CATEGORY_ID else None
        def list_ids(self):
            return ["tv"]

    settings = SimpleNamespace(
        language="English",
        tracked_items=[],
        soulseek=SimpleNamespace(enabled=True, parallel_search_enabled=True, api_configured=False),
    )
    svc = SchedulerTorrentSearchService(SchedulerServiceContext(
        settings_manager=SimpleNamespace(settings=settings),
        db=None,
        downloader=None,
        pipeline=None,
        aggregator=None,
        categories=FakeRegistry(),
    ))

    async def slow_search(*args, **kwargs):
        await asyncio.sleep(0.05)
        return [], "The Boys Season 1"

    started: list[float] = []
    async def companion(**kwargs):
        started.append(time.monotonic())
        return {"enabled": True, "status": "not_configured", "candidate_count": 0, "candidates": []}

    svc._search = slow_search  # type: ignore[method-assign]
    svc._soulseek_companion_search = companion  # type: ignore[method-assign]
    t0 = time.monotonic()
    result = await svc.search_media_torrents("The Boys", season=1, category_id="tv")
    require(started, "companion Soulseek task should be started")
    require(started[0] - t0 < 0.04, "companion Soulseek search should not wait for torrent fanout")
    require(result["companion_soulseek"]["status"] == "not_configured", "companion result should be included")


async def main() -> None:
    await test_jackett_uses_v188_aggregate_before_recovery()
    await test_aggregator_uses_emergency_fallback_when_primary_empty()
    await test_scheduler_soulseek_companion_runs_in_parallel()
    print("round202_search_orchestration_tests: PASS")


if __name__ == "__main__":
    asyncio.run(main())
