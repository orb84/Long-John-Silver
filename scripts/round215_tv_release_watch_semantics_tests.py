#!/usr/bin/env python3
"""Round 215 TV release-watch semantics regression tests."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import sys

# The lightweight CI sandbox for these project zips may not have aiosqlite
# installed, but importing MediaScheduler only needs the database module to load.
sys.modules.setdefault("aiosqlite", SimpleNamespace(OperationalError=Exception, Connection=object, Cursor=object))

from src.core.categories.registry import CategoryRegistry
from src.core.models import Settings
from src.core.scheduler import MediaScheduler


class FakeReleaseRepo:
    def __init__(self, due_rows=None) -> None:
        self.upserts = []
        self.recorded = []
        self.queued = []
        self._due_rows = due_rows or []
        self.expired_called = False

    async def upsert(self, **kwargs):
        self.upserts.append(dict(kwargs)); return len(self.upserts)

    async def due(self, *, limit=25):
        return list(self._due_rows)

    async def expire_overdue(self, *, limit=100):
        self.expired_called = True; return 0

    async def record_attempt(self, watch_id, **kwargs):
        self.recorded.append((watch_id, dict(kwargs)))

    async def mark_queued(self, category_id, item_id, unit_key, *, outcome=None):
        self.queued.append((category_id, item_id, unit_key, dict(outcome or {})))


class FakeDb:
    def __init__(self, repo):
        self.release_watches = repo


class FakePipeline:
    def __init__(self, *, candidate=None, discovery_ok=False):
        self.candidate = candidate
        self.discovery_ok = discovery_ok
        self.search_calls = []
        self.discovery_calls = []

    async def run_search(self, item, episode_label=None, mode="auto", language=""):
        self.search_calls.append((getattr(item, "key", ""), episode_label, mode, language))
        return self.candidate

    async def run_discovery(self, item, episode_label=None, force=False, language=""):
        self.discovery_calls.append((getattr(item, "key", ""), episode_label, force, language))
        return self.discovery_ok


class FakeNotifications:
    def __init__(self): self.sent = []
    async def notify(self, message, **kwargs): self.sent.append((message, kwargs))
    async def send_message(self, *args, **kwargs): self.sent.append((args, kwargs))


async def test_future_airdate_creates_delayed_watch_not_rss() -> None:
    registry = CategoryRegistry(); registry.register_defaults()
    tv = registry.get("tv")
    settings = Settings(); settings.language = "English"
    item = tv.create_item("Example Show", language="English")
    future = (datetime.now(timezone.utc) + timedelta(days=5)).date().isoformat()
    item.metadata = {
        "lifecycle_status": "Returning Series",
        "tvmaze": {"status": "Running", "schedule": {"days": ["Monday"]}, "next_episode": {"season": 3, "number": 7, "airdate": future}},
    }
    plan = await tv.build_watch_plan(item, SimpleNamespace(db=None, settings=settings, metadata_clients={}))
    assert plan.mode == "release_watch"
    assert plan.release_watches and plan.release_watches[0].unit_key == "S03E07"
    watch = plan.release_watches[0]
    assert watch.watch_start_at.startswith(future), watch
    assert watch.expected_air_at.startswith(future), watch
    assert watch.cadence_profile == "weekly"
    assert watch.requirements["preferred_language"] == "English"
    assert watch.requirements["language_fallback_requires_approval"] is True
    assert not plan.rss_feeds, "future episodes should not create near-real-time RSS weeks/days early"


async def test_scheduler_persists_category_watch_timing_and_requirements() -> None:
    registry = CategoryRegistry(); registry.register_defaults()
    tv = registry.get("tv")
    settings = Settings(); settings.language = "English"
    item = tv.create_item("Example Show", language="English")
    item.metadata = {"lifecycle_status": "Returning Series", "tvmaze": {"status": "Running", "schedule": {"days": ["Monday"]}, "next_episode": {"season": 1, "number": 2, "airdate": "2030-01-01"}}}
    plan = await tv.build_watch_plan(item, SimpleNamespace(db=None, settings=settings, metadata_clients={}))
    repo = FakeReleaseRepo()
    sched = SimpleNamespace(_db=FakeDb(repo))
    await MediaScheduler._apply_release_watches_from_plan(sched, plan, item)
    row = repo.upserts[0]
    assert row["unit_key"] == "S01E02"
    assert row["watch_start_at"].startswith("2030-01-01")
    assert row["expected_air_at"].startswith("2030-01-01")
    assert row["cadence_profile"] == "weekly"
    assert row["requirements"]["preferred_language"] == "English"


async def test_release_watch_outcomes_are_typed_not_completed_on_candidate_or_queue() -> None:
    registry = CategoryRegistry(); registry.register_defaults()
    tv = registry.get("tv")
    item = tv.create_item("Example Show", language="English")
    item.auto_download = False
    settings = Settings(); settings.language = "English"; settings.auto_download = False; settings.tracked_items.append(item)
    candidate = SimpleNamespace(title="Example Show S01E02 1080p", size_bytes=123, seeders=20, source="jackett", magnet="magnet:?xt=urn:btih:abc")
    repo = FakeReleaseRepo(due_rows=[{"id": 1, "category_id": "tv", "item_id": "Example Show", "unit_key": "S01E02", "preferred_language": "English", "interval_hours": 2.0, "requirements": {"preferred_language": "English", "auto_download": False}}])
    sched = SimpleNamespace(_db=FakeDb(repo), _categories=registry, _settings_manager=SimpleNamespace(settings=settings), _pipeline=FakePipeline(candidate=candidate), _notifications=FakeNotifications())
    sched._release_candidate_summary = MediaScheduler._release_candidate_summary
    await MediaScheduler.process_release_watches(sched)
    assert repo.recorded and repo.recorded[0][1]["status"] == "candidate_found"
    assert repo.recorded[0][1]["candidate_summary"]["title"].startswith("Example Show")

    item.auto_download = True; settings.auto_download = True
    repo2 = FakeReleaseRepo(due_rows=[{"id": 2, "category_id": "tv", "item_id": "Example Show", "unit_key": "S01E03", "preferred_language": "English", "interval_hours": 2.0, "requirements": {"preferred_language": "English", "auto_download": True}}])
    sched2 = SimpleNamespace(_db=FakeDb(repo2), _categories=registry, _settings_manager=SimpleNamespace(settings=settings), _pipeline=FakePipeline(discovery_ok=True), _notifications=FakeNotifications())
    sched2._release_candidate_summary = MediaScheduler._release_candidate_summary
    await MediaScheduler.process_release_watches(sched2)
    assert repo2.queued and repo2.queued[0][2] == "S01E03"
    assert not repo2.recorded, "queued watches should not be marked completed before import confirms the unit"


async def main() -> None:
    await test_future_airdate_creates_delayed_watch_not_rss()
    await test_scheduler_persists_category_watch_timing_and_requirements()
    await test_release_watch_outcomes_are_typed_not_completed_on_candidate_or_queue()
    print("round215 tv release-watch semantics tests: PASS")


if __name__ == "__main__":
    asyncio.run(main())
