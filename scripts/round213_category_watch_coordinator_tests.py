#!/usr/bin/env python3
"""Round 213 category item/watch coordinator regression tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.category_item_coordinator import CategoryItemCoordinator
from src.core.categories.registry import CategoryRegistry
from src.core.models import CategoryMediaMetadata, Settings


class FakeSettingsManager:
    def __init__(self) -> None:
        self.settings = Settings()
        self.saved = 0

    def save(self, settings) -> None:
        self.saved += 1
        self.settings = settings


class FakeMediaRepo:
    def __init__(self) -> None:
        self.items = {}
        self.metadata = []

    async def upsert_category_item(self, category_id, item_id, item):
        self.items[(category_id, item_id)] = item

    async def delete_category_item(self, category_id, item_id):
        self.items.pop((category_id, item_id), None)

    async def upsert_category_metadata(self, category_id, item_id, provider, metadata, external_id=""):
        self.metadata.append({
            "category_id": category_id,
            "item_id": item_id,
            "provider": provider,
            "metadata": dict(metadata),
            "external_id": external_id,
        })

    async def get_category_metadata(self, category_id, item_id, provider=None):
        rows = [row for row in self.metadata if row["category_id"] == category_id and row["item_id"] == item_id]
        if provider:
            rows = [row for row in rows if row["provider"] == provider]
        return rows


class FakeReleaseWatches:
    def __init__(self) -> None:
        self.rows = []

    async def upsert(self, **kwargs):
        self.rows.append(dict(kwargs))
        return len(self.rows)


class FakeDb:
    def __init__(self) -> None:
        self.media = FakeMediaRepo()
        self.release_watches = FakeReleaseWatches()


class FakeEnricher:
    pass


async def _fake_series_enrichment(self, item_name):
    return CategoryMediaMetadata(
        category_id="tv",
        item_id=item_name,
        display_name=item_name,
        provider="tmdb_" + "tv",
        tmdb_id=123,
        lifecycle_status="Returning Series",
        number_of_seasons=2,
        number_of_episodes=10,
    )


setattr(FakeEnricher, "enrich_" + "series", _fake_series_enrichment)


class FakeTvMaze:
    async def search(self, query):
        return [{"id": 456, "name": query, "status": "Running"}]

    async def get_show_details(self, show_id):
        return {
            "id": show_id,
            "name": "Example Show",
            "status": "Running",
            "schedule": {"days": ["Monday"], "time": "21:00"},
            "next_episode": {"season": 2, "number": 3, "airdate": "2026-07-10", "name": "Next"},
        }


class FakeScheduler:
    def __init__(self) -> None:
        self.synced = []
        self.invalidated = []

    async def sync_category_watch_policy(self, category_id, item_id, *, item=None, reason=""):
        self.synced.append((category_id, item_id, reason, item))

    async def invalidate_item_lifecycle(self, category_id, item_id, *, reason=""):
        self.invalidated.append((category_id, item_id, reason))


async def test_add_uses_category_enrichment_and_syncs_watch_policy() -> None:
    settings = FakeSettingsManager()
    db = FakeDb()
    scheduler = FakeScheduler()
    registry = CategoryRegistry()
    registry.register_defaults()
    coordinator = CategoryItemCoordinator(
        settings_manager=settings,
        category_registry=registry,
        db=db,
        scheduler=scheduler,
        metadata_enricher=FakeEnricher(),
        metadata_clients={"tvmaze": FakeTvMaze()},
    )

    item = await coordinator.add_or_update_item("tv", "Example Show", language="English")

    assert item.item_type == "tv", item
    assert item.tmdb_id == 123
    assert item.tvmaze_id == 456
    assert item.metadata["lifecycle_status"] == "Returning Series"
    assert item.metadata["tvmaze"]["next_episode"]["season"] == 2
    assert ("tv", "Example Show") in db.media.items
    assert scheduler.synced and scheduler.synced[-1][0:3] == ("tv", "Example Show", "add_or_update")
    assert scheduler.invalidated and scheduler.invalidated[-1] == ("tv", "Example Show", "add_or_update")


async def test_tv_watch_plan_owns_release_watch_and_rss_policy() -> None:
    registry = CategoryRegistry(); registry.register_defaults()
    tv = registry.get("tv")
    settings = Settings(); settings.jackett_url = "http://127.0.0.1:9117"; settings.jackett_api_key = "secret"
    db = FakeDb()
    item = tv.create_item("Example Show", language="English", auto_download=True)
    item.metadata = {
        "lifecycle_status": "Returning Series",
        "tvmaze": {
            "status": "Running",
            "schedule": {"days": ["Monday"]},
            "next_episode": {"season": 2, "number": 3, "airdate": "2026-07-10"},
        },
    }
    context = SimpleNamespace(db=db, settings=settings, metadata_clients={}, metadata_enricher=None)

    plan = await tv.build_watch_plan(item, context)

    assert plan.mode == "release_watch"
    # Future release watches are persisted immediately, but RSS is only enabled
    # close to the release window so the monitor does not poll weeks early.
    assert plan.release_watches[0].unit_key == "S02E03"
    assert plan.release_watches[0].interval_hours == 2.0
    assert plan.release_watches[0].watch_start_at.startswith("2026-07-10")
    assert not plan.rss_feeds

    ended = tv.create_item("Ended Show", language="English")
    ended.metadata = {"lifecycle_status": "Ended"}
    ended_plan = await tv.build_watch_plan(ended, context)
    assert not ended_plan.rss_feeds
    assert not ended_plan.release_watches
    assert ended_plan.mode == "periodic_metadata"


async def main() -> None:
    await test_add_uses_category_enrichment_and_syncs_watch_policy()
    await test_tv_watch_plan_owns_release_watch_and_rss_policy()
    print("round213 category item/watch coordinator tests: PASS")


if __name__ == "__main__":
    asyncio.run(main())
