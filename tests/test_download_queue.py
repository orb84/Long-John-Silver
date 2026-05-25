"""
Tests for the download queue API: pause, resume, priority, restart.

Tests the queue management methods on DownloadManager using a mock database.
"""

import asyncio
import hashlib
import pytest
import pytest_asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from src.core.models import DownloadItem, DownloadStatus, DownloadPriority, Settings
from src.core.database import Database


class FakeDownloadsRepository:
    """Minimal fake download repository for testing."""

    def __init__(self):
        self._downloads: dict[str, DownloadItem] = {}

    async def upsert_download(self, item: DownloadItem):
        self._downloads[item.id] = item

    async def get_download(self, download_id: str) -> DownloadItem | None:
        return self._downloads.get(download_id)

    async def get_active_downloads(self) -> list[DownloadItem]:
        active_statuses = {
            DownloadStatus.QUEUED,
            DownloadStatus.DOWNLOADING,
            DownloadStatus.PAUSED,
            DownloadStatus.SEEDING,
            DownloadStatus.STALLED,
        }
        return [d for d in self._downloads.values() if d.status in active_statuses]

    async def get_queued_downloads(self) -> list[DownloadItem]:
        queued = [d for d in self._downloads.values() if d.status == DownloadStatus.QUEUED]
        priority_order = {"high": 0, "normal": 1, "low": 2}
        queued.sort(key=lambda i: priority_order.get(i.priority.value, 1))
        return queued


class FakeDatabase:
    """Minimal fake database for testing DownloadManager queue operations."""

    def __init__(self):
        self.downloads = FakeDownloadsRepository()


class FakeSupervisor:
    """Minimal fake task supervisor."""

    def cancel(self, name: str):
        pass

    def cancel_by_prefix(self, prefix: str):
        pass


class FakeEngine:
    """Minimal fake TorrentEngine."""

    def __init__(self):
        self._handles: dict[str, MagicMock] = {}

    async def initialize(self):
        pass

    async def close(self):
        pass

    async def add_magnet(self, magnet_link: str, download_id: str) -> MagicMock:
        handle = MagicMock()
        self._handles[download_id] = handle
        return handle

    async def remove_torrent(self, download_id: str):
        self._handles.pop(download_id, None)

    def get_handle(self, download_id: str) -> MagicMock | None:
        return self._handles.get(download_id)

    async def apply_settings(self, settings: dict):
        pass


class FakeQueueManager:
    """Minimal fake QueueManager."""

    def __init__(self):
        self._active: set[str] = set()

    def register_active(self, download_id: str):
        self._active.add(download_id)

    def deregister_active(self, download_id: str):
        self._active.discard(download_id)

    def active_count(self) -> int:
        return len(self._active)

    async def run_loop(self, start_callback, pause_callback):
        pass


class FakeBandwidthManager:
    """Minimal fake BandwidthManager."""

    async def run_loop(self):
        pass


def _make_dm(fake_db: FakeDatabase) -> "DownloadManager":
    """Create a DownloadManager with faked dependencies."""
    from src.core.downloader import DownloadManager, DownloadDependencies
    dm = DownloadManager(DownloadDependencies(
        download_dir="/tmp/ljs_test",
        db=fake_db,
        supervisor=FakeSupervisor(),
        engine=FakeEngine(),
        queue=FakeQueueManager(),
        bandwidth=FakeBandwidthManager(),
        settings_manager=MagicMock(),
        max_concurrent=3,
        seed_ratio_target=2.0,
        seed_duration_hours=48,
    ))
    return dm


def _make_item(
    download_id: str = "test123",
    item_name: str = "Test Show",
    status: DownloadStatus = DownloadStatus.QUEUED,
    priority: DownloadPriority = DownloadPriority.NORMAL,
) -> DownloadItem:
    return DownloadItem(
        id=download_id,
        item_name=item_name,
        magnet="magnet:?xt=urn:btih:abc123",
        status=status,
        priority=priority,
    )


class TestPauseDownload:
    """Tests for DownloadManager.pause_download()."""

    @pytest.mark.asyncio
    async def test_pause_queued_download(self):
        db = FakeDatabase()
        dm = _make_dm(db)
        item = _make_item(status=DownloadStatus.QUEUED)
        await db.downloads.upsert_download(item)

        result = await dm.pause_download("test123")

        assert result is not None
        assert result.status == DownloadStatus.PAUSED
        assert "test123" not in dm._active_downloads

    @pytest.mark.asyncio
    async def test_pause_downloading_download(self):
        db = FakeDatabase()
        dm = _make_dm(db)
        item = _make_item(status=DownloadStatus.DOWNLOADING)
        await db.downloads.upsert_download(item)
        dm._active_downloads.add("test123")

        result = await dm.pause_download("test123")

        assert result is not None
        assert result.status == DownloadStatus.PAUSED
        assert "test123" not in dm._active_downloads

    @pytest.mark.asyncio
    async def test_pause_nonexistent_returns_none(self):
        db = FakeDatabase()
        dm = _make_dm(db)

        result = await dm.pause_download("nonexistent")

        assert result is None

    @pytest.mark.asyncio
    async def test_pause_complete_returns_none(self):
        db = FakeDatabase()
        dm = _make_dm(db)
        item = _make_item(status=DownloadStatus.COMPLETE)
        await db.downloads.upsert_download(item)

        result = await dm.pause_download("test123")

        assert result is None

    @pytest.mark.asyncio
    async def test_pause_already_paused_returns_none(self):
        db = FakeDatabase()
        dm = _make_dm(db)
        item = _make_item(status=DownloadStatus.PAUSED)
        await db.downloads.upsert_download(item)

        result = await dm.pause_download("test123")

        assert result is None


class TestResumeDownload:
    """Tests for DownloadManager.resume_download()."""

    @pytest.mark.asyncio
    async def test_resume_paused_download_requeues(self):
        db = FakeDatabase()
        dm = _make_dm(db)
        item = _make_item(status=DownloadStatus.PAUSED)
        await db.downloads.upsert_download(item)

        result = await dm.resume_download("test123")

        assert result is not None
        # Without a handle, resume re-queues
        assert result.status == DownloadStatus.QUEUED

    @pytest.mark.asyncio
    async def test_resume_nonexistent_returns_none(self):
        db = FakeDatabase()
        dm = _make_dm(db)

        result = await dm.resume_download("nonexistent")

        assert result is None

    @pytest.mark.asyncio
    async def test_resume_downloading_returns_none(self):
        db = FakeDatabase()
        dm = _make_dm(db)
        item = _make_item(status=DownloadStatus.DOWNLOADING)
        await db.downloads.upsert_download(item)

        result = await dm.resume_download("test123")

        assert result is None


class TestSetPriority:
    """Tests for DownloadManager.set_priority()."""

    @pytest.mark.asyncio
    async def test_set_priority_queued(self):
        db = FakeDatabase()
        dm = _make_dm(db)
        item = _make_item(status=DownloadStatus.QUEUED, priority=DownloadPriority.NORMAL)
        await db.downloads.upsert_download(item)

        result = await dm.set_priority("test123", DownloadPriority.HIGH)

        assert result is not None
        assert result.priority == DownloadPriority.HIGH

    @pytest.mark.asyncio
    async def test_set_priority_paused(self):
        db = FakeDatabase()
        dm = _make_dm(db)
        item = _make_item(status=DownloadStatus.PAUSED, priority=DownloadPriority.LOW)
        await db.downloads.upsert_download(item)

        result = await dm.set_priority("test123", DownloadPriority.HIGH)

        assert result is not None
        assert result.priority == DownloadPriority.HIGH

    @pytest.mark.asyncio
    async def test_set_priority_downloading_updates_active_priority(self):
        db = FakeDatabase()
        dm = _make_dm(db)
        item = _make_item(status=DownloadStatus.DOWNLOADING)
        await db.downloads.upsert_download(item)

        result = await dm.set_priority("test123", DownloadPriority.HIGH)

        assert result is not None
        assert result.priority == DownloadPriority.HIGH

    @pytest.mark.asyncio
    async def test_set_priority_nonexistent_returns_none(self):
        db = FakeDatabase()
        dm = _make_dm(db)

        result = await dm.set_priority("nonexistent", DownloadPriority.HIGH)

        assert result is None

    @pytest.mark.asyncio
    async def test_set_priority_with_none_defaults_to_normal(self):
        db = FakeDatabase()
        dm = _make_dm(db)
        item = _make_item(status=DownloadStatus.QUEUED, priority=DownloadPriority.LOW)
        await db.downloads.upsert_download(item)

        result = await dm.set_priority("test123", None)

        assert result is not None
        assert result.priority == DownloadPriority.NORMAL


class TestRestartDownload:
    """Tests for DownloadManager.restart_download()."""

    @pytest.mark.asyncio
    async def test_restart_failed_download(self):
        db = FakeDatabase()
        dm = _make_dm(db)
        item = _make_item(status=DownloadStatus.FAILED)
        item.progress = 0.67
        await db.downloads.upsert_download(item)

        result = await dm.restart_download("test123")

        assert result is not None
        assert result.status == DownloadStatus.QUEUED
        assert result.progress == 0.0

    @pytest.mark.asyncio
    async def test_restart_cancelled_download(self):
        db = FakeDatabase()
        dm = _make_dm(db)
        item = _make_item(status=DownloadStatus.CANCELLED)
        await db.downloads.upsert_download(item)

        result = await dm.restart_download("test123")

        assert result is not None
        assert result.status == DownloadStatus.QUEUED

    @pytest.mark.asyncio
    async def test_restart_queued_returns_none(self):
        db = FakeDatabase()
        dm = _make_dm(db)
        item = _make_item(status=DownloadStatus.QUEUED)
        await db.downloads.upsert_download(item)

        result = await dm.restart_download("test123")

        assert result is None

    @pytest.mark.asyncio
    async def test_restart_nonexistent_returns_none(self):
        db = FakeDatabase()
        dm = _make_dm(db)

        result = await dm.restart_download("nonexistent")

        assert result is None


class TestGetQueuedDownloads:
    """Tests for DownloadManager.get_queued_downloads()."""

    @pytest.mark.asyncio
    async def test_queued_sorted_by_priority(self):
        db = FakeDatabase()
        dm = _make_dm(db)
        low = _make_item(download_id="low1", priority=DownloadPriority.LOW)
        normal = _make_item(download_id="norm1", priority=DownloadPriority.NORMAL)
        high = _make_item(download_id="high1", priority=DownloadPriority.HIGH)
        await db.downloads.upsert_download(high)
        await db.downloads.upsert_download(low)
        await db.downloads.upsert_download(normal)

        result = await dm.get_queued_downloads()

        assert len(result) == 3
        assert result[0].id == "high1"
        assert result[1].id == "norm1"
        assert result[2].id == "low1"

    @pytest.mark.asyncio
    async def test_excludes_non_queued(self):
        db = FakeDatabase()
        dm = _make_dm(db)
        queued = _make_item(download_id="q1", status=DownloadStatus.QUEUED)
        downloading = _make_item(download_id="d1", status=DownloadStatus.DOWNLOADING)
        paused = _make_item(download_id="p1", status=DownloadStatus.PAUSED)
        await db.downloads.upsert_download(queued)
        await db.downloads.upsert_download(downloading)
        await db.downloads.upsert_download(paused)

        result = await dm.get_queued_downloads()

        assert len(result) == 1
        assert result[0].id == "q1"


class TestGetDownload:
    """Tests for DownloadManager.get_download()."""

    @pytest.mark.asyncio
    async def test_get_existing_download(self):
        db = FakeDatabase()
        dm = _make_dm(db)
        item = _make_item(download_id="abc")
        await db.downloads.upsert_download(item)

        result = await dm.get_download("abc")

        assert result is not None
        assert result.id == "abc"

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_none(self):
        db = FakeDatabase()
        dm = _make_dm(db)

        result = await dm.get_download("nonexistent")

        assert result is None


class TestRecoverSkipsPaused:
    """Tests that recover_downloads skips paused items."""

    @pytest.mark.asyncio
    async def test_recover_skips_paused(self):
        db = FakeDatabase()
        dm = _make_dm(db)
        queued = _make_item(download_id="q1", status=DownloadStatus.QUEUED)
        paused = _make_item(download_id="p1", status=DownloadStatus.PAUSED)
        downloading = _make_item(download_id="d1", status=DownloadStatus.DOWNLOADING)
        await db.downloads.upsert_download(queued)
        await db.downloads.upsert_download(paused)
        await db.downloads.upsert_download(downloading)

        # _start_download needs a session, so we mock it
        dm._start_download = AsyncMock()
        await dm.recover_downloads()

        # Only queued and downloading should be started (2 calls)
        # Paused should be skipped
        assert dm._start_download.call_count == 2


class TestAddMagnet:
    """Tests for DownloadManager.add_magnet()."""

    @pytest.mark.asyncio
    async def test_concurrent_same_magnet_returns_existing(self):
        db = FakeDatabase()
        dm = _make_dm(db)
        magnet = "magnet:?xt=urn:btih:foobar123"
        dm._start_download = AsyncMock()

        async def add():
            return await dm.add_magnet(magnet, item_name="Test Show")

        results = await asyncio.gather(add(), add(), add())
        # All three should return the same DownloadItem (idempotent)
        assert len({id(r) for r in results}) == 1
        assert results[0].id == hashlib.md5(magnet.encode()).hexdigest()[:12]

    @pytest.mark.asyncio
    async def test_concurrent_same_magnet_starts_only_once(self):
        db = FakeDatabase()
        dm = _make_dm(db)
        magnet = "magnet:?xt=urn:btih:unique456"
        dm._start_download = AsyncMock()

        async def add():
            return await dm.add_magnet(magnet, item_name="Test Show")

        await asyncio.gather(add(), add(), add())
        # _start_download should only be called once (first caller starts it)
        assert dm._start_download.call_count == 1