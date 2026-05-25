"""Round 12 reliability audit for generic download-health supervision.

This is intentionally dependency-light: it exercises the supervisor's state
machine with fakes rather than requiring libtorrent, DB, or network services.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.core.download_health import DownloadHealthSupervisor, DownloadHealthState
from src.core.models import DownloadItem, DownloadPriority, DownloadStatus


class FakeDownloads:
    def __init__(self, items):
        self.items = {i.id: i for i in items}

    async def get_active_downloads(self):
        return list(self.items.values())

    async def get_download(self, download_id):
        return self.items.get(download_id)

    async def upsert_download(self, item):
        self.items[item.id] = item


class FakeDownloader:
    def __init__(self, items):
        self.db = FakeDownloads(items)
        self.parked = []
        self.tests = []
        self.priorities = []

    async def get_active_downloads(self):
        return await self.db.get_active_downloads()

    async def park_stalled_download(self, download_id, reason=""):
        item = await self.db.get_download(download_id)
        item.status = DownloadStatus.STALLED
        self.parked.append((download_id, reason))
        await self.db.upsert_download(item)
        return item

    async def start_health_test(self, download_id, temporary_priority=DownloadPriority.HIGH):
        item = await self.db.get_download(download_id)
        item.status = DownloadStatus.DOWNLOADING
        item.priority = temporary_priority
        self.tests.append(download_id)
        await self.db.upsert_download(item)
        return item

    async def set_priority(self, download_id, priority):
        item = await self.db.get_download(download_id)
        item.priority = priority
        self.priorities.append((download_id, priority))
        await self.db.upsert_download(item)
        return item


class FakePipeline:
    async def run_search(self, *args, **kwargs):
        return []


async def main():
    settings = SimpleNamespace(
        stall_health_window_minutes=0.01,
        stall_test_interval_minutes=60,
        stall_test_duration_minutes=15,
        stall_alternative_cooldown_minutes=180,
        stall_min_progress_bytes=1024,
        stall_idle_rate_bps=1024,
        tracked_items=[],
        language="Italian",
    )
    settings_manager = SimpleNamespace(settings=settings)

    stuck = DownloadItem(
        id="stuck1",
        item_name="Rare Show",
        magnet="magnet:?xt=urn:btih:stuck1",
        status=DownloadStatus.DOWNLOADING,
        priority=DownloadPriority.NORMAL,
        downloaded_bytes=1000,
        download_rate=0,
        category_id="tv",
        season=1,
        episode=2,
    )
    downloader = FakeDownloader([stuck])
    supervisor = DownloadHealthSupervisor(
        settings_manager=settings_manager,
        db=downloader.db,
        downloader=downloader,
        pipeline=FakePipeline(),
    )
    state = supervisor._states.setdefault("stuck1", DownloadHealthState(download_id="stuck1"))
    state.last_bytes = 1000
    state.last_progress_at = datetime.now(timezone.utc) - timedelta(minutes=6)

    counters = await supervisor.run_once()
    assert counters["parked"] == 1, counters
    assert downloader.parked and downloader.db.items["stuck1"].status == DownloadStatus.STALLED

    # Force a test window and ensure a parked torrent gets resumed as HIGH.
    state.next_test_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    counters = await supervisor.run_once()
    assert counters["tests_started"] == 1, counters
    assert downloader.tests == ["stuck1"]
    assert downloader.db.items["stuck1"].priority == DownloadPriority.HIGH

    # Simulate byte movement during the test; original priority is restored.
    downloader.db.items["stuck1"].downloaded_bytes = 1000 + 131072
    # The active list may contain the object observed before start_health_test
    # in some fake implementations; refresh it explicitly to mirror DB-backed behavior.
    downloader.db.items["stuck1"].status = DownloadStatus.DOWNLOADING
    counters = await supervisor.run_once()
    assert state.testing_until is None, state
    assert state.parked_at is None, state
    print("round12 download-health audit passed")


if __name__ == "__main__":
    asyncio.run(main())
