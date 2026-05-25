"""
Tests for the download manager: seed ratio logic and lifecycle monitoring.

Tests SeedingPolicy directly (extracted from DownloadManager) and
DownloadManager callback management using proper dependency injection.
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from src.core.models import DownloadItem, DownloadStatus


# ── Fake Dependencies ──────────────────────────────────────────


class FakeEngine:
    """Minimal fake TorrentEngine for testing."""

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

    async def apply_settings(self, settings: dict):
        pass

    def get_handle(self, download_id: str) -> MagicMock | None:
        return self._handles.get(download_id)


class FakeQueueManager:
    """Minimal fake QueueManager for testing."""

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
    """Minimal fake BandwidthManager for testing."""

    async def run_loop(self):
        pass


class FakeSupervisor:
    """Minimal fake TaskSupervisor for testing."""

    def cancel(self, name: str):
        pass

    def cancel_by_prefix(self, prefix: str):
        pass

    def spawn_restartable(self, name, coro_factory, criticality=None):
        import asyncio
        task = asyncio.create_task(coro_factory())
        return task

    def spawn_one_shot(self, name, coro):
        import asyncio
        task = asyncio.create_task(coro)
        return task


# ── Seeding Policy Tests ───────────────────────────────────────


class TestSeedingPolicy:
    """Tests for seed ratio/duration stop logic in SeedingPolicy."""

    def _make_policy(self, ratio_target=2.0, duration_hours=48):
        from src.core.downloader_lifecycle import SeedingPolicy
        return SeedingPolicy(seed_ratio_target=ratio_target, seed_duration_hours=duration_hours)

    def _make_item(self, completed_hours_ago: float = 50.0) -> DownloadItem:
        return DownloadItem(
            id="test123",
            item_name="Test Show",
            magnet="magnet:?xt=urn:btih:abc",
            completed_at=datetime.now(timezone.utc) - timedelta(hours=completed_hours_ago),
        )

    def test_no_stop_without_completed_at(self):
        policy = self._make_policy()
        item = DownloadItem(id="x", item_name="x", magnet="magnet:1")
        assert policy.should_stop(item, upload_rate=0) is False

    def test_no_stop_before_duration(self):
        policy = self._make_policy(duration_hours=48)
        item = self._make_item(completed_hours_ago=10.0)
        assert policy.should_stop(item, upload_rate=0) is False

    def test_stop_when_ratio_reached(self):
        policy = self._make_policy(ratio_target=2.0, duration_hours=48)
        item = self._make_item(completed_hours_ago=50.0)
        assert policy.should_stop(item, upload_rate=100.0, seed_ratio=2.5) is True

    def test_stop_when_upload_zero_and_duration_met(self):
        policy = self._make_policy(ratio_target=2.0, duration_hours=48)
        item = self._make_item(completed_hours_ago=50.0)
        assert policy.should_stop(item, upload_rate=0, seed_ratio=0.5) is True

    def test_no_stop_when_still_seeding(self):
        policy = self._make_policy(ratio_target=2.0, duration_hours=48)
        item = self._make_item(completed_hours_ago=50.0)
        assert policy.should_stop(item, upload_rate=500.0, seed_ratio=1.0) is False

    def test_ratio_exactly_at_target(self):
        policy = self._make_policy(ratio_target=2.0, duration_hours=48)
        item = self._make_item(completed_hours_ago=49.0)
        assert policy.should_stop(item, upload_rate=50.0, seed_ratio=2.0) is True

    def test_custom_high_ratio_target(self):
        policy = self._make_policy(ratio_target=5.0, duration_hours=24)
        item = self._make_item(completed_hours_ago=25.0)
        assert policy.should_stop(item, upload_rate=200.0, seed_ratio=3.0) is False

    def test_custom_high_ratio_target_met(self):
        policy = self._make_policy(ratio_target=5.0, duration_hours=24)
        item = self._make_item(completed_hours_ago=25.0)
        assert policy.should_stop(item, upload_rate=200.0, seed_ratio=5.5) is True

    def test_zero_upload_rate_with_zero_ratio(self):
        """Duration met, no upload activity, ratio 0 — should stop (no peers)."""
        policy = self._make_policy(ratio_target=2.0, duration_hours=48)
        item = self._make_item(completed_hours_ago=50.0)
        assert policy.should_stop(item, upload_rate=0, seed_ratio=0.0) is True


# ── Completion Callback Tests (via DownloadLifecycleMonitor) ────


class TestCompletionCallback:
    """Tests for the download completion callback via DownloadLifecycleMonitor."""

    @pytest.mark.asyncio
    async def test_set_completion_callback_stores_callback(self):
        """set_completion_callback should store the provided callable."""
        from src.core.downloader import DownloadManager, DownloadDependencies
        dm = DownloadManager(DownloadDependencies(
            download_dir="/tmp/ljs_test",
            db=MagicMock(),
            supervisor=FakeSupervisor(),
            engine=FakeEngine(),
            queue=FakeQueueManager(),
            bandwidth=FakeBandwidthManager(),
            settings_manager=MagicMock(),
        ))
        callback = lambda x: None
        dm.set_completion_callback(callback)
        assert dm._on_complete_callback is callback

    @pytest.mark.asyncio
    async def test_set_completion_callback_overrides_previous(self):
        """Setting a new callback should replace the old one."""
        from src.core.downloader import DownloadManager, DownloadDependencies
        dm = DownloadManager(DownloadDependencies(
            download_dir="/tmp/ljs_test",
            db=MagicMock(),
            supervisor=FakeSupervisor(),
            engine=FakeEngine(),
            queue=FakeQueueManager(),
            bandwidth=FakeBandwidthManager(),
            settings_manager=MagicMock(),
        ))
        old = lambda x: None
        new = lambda x: None
        dm.set_completion_callback(old)
        dm.set_completion_callback(new)
        assert dm._on_complete_callback is new
        assert dm._on_complete_callback is not old

    @pytest.mark.asyncio
    async def test_completion_callback_fires_when_seeding_ends(self):
        """The completion callback should fire when seeding monitor sets COMPLETE."""
        from src.core.downloader_lifecycle import (
            DownloadLifecycleContext,
            DownloadLifecycleMonitor,
            SeedingPolicy,
        )

        ctx = DownloadLifecycleContext(
            db=MagicMock(),
            supervisor=FakeSupervisor(),
            engine=FakeEngine(),
            download_dir="/tmp",
            seed_policy=SeedingPolicy(seed_ratio_target=2.0, seed_duration_hours=48),
            on_stats_callback=None,
            on_ready_callback=None,
            on_complete_callback=None,
        )
        ctx.db.downloads = MagicMock()
        ctx.db.downloads.get_download = AsyncMock(return_value=DownloadItem(
            id='test123', item_name='Test', magnet='magnet:?xt=urn:btih:abc',
            completed_at=datetime.now(timezone.utc) - timedelta(hours=50),
        ))
        ctx.db.downloads.upsert_download = AsyncMock()

        handle = MagicMock()
        handle.status.return_value = MagicMock(
            all_time_download=1000,
            total_done=1000,
            all_time_upload=2500,
            upload_rate=100,
        )

        callback = AsyncMock()
        ctx.on_complete_callback = callback
        monitor = DownloadLifecycleMonitor(ctx)

        with patch('asyncio.sleep', AsyncMock(return_value=None)):
            await monitor._monitor_seeding('test123', handle)

        callback.assert_awaited_once_with('test123')

    @pytest.mark.asyncio
    async def test_completion_callback_handles_exception_gracefully(self):
        """An exception in the callback should not crash _monitor_seeding."""
        from src.core.downloader_lifecycle import (
            DownloadLifecycleContext,
            DownloadLifecycleMonitor,
            SeedingPolicy,
        )

        ctx = DownloadLifecycleContext(
            db=MagicMock(),
            supervisor=FakeSupervisor(),
            engine=FakeEngine(),
            download_dir="/tmp",
            seed_policy=SeedingPolicy(seed_ratio_target=2.0, seed_duration_hours=48),
            on_stats_callback=None,
            on_ready_callback=None,
            on_complete_callback=None,
        )
        ctx.db.downloads = MagicMock()
        ctx.db.downloads.get_download = AsyncMock(return_value=DownloadItem(
            id='test123', item_name='Test', magnet='magnet:?xt=urn:btih:abc',
            completed_at=datetime.now(timezone.utc) - timedelta(hours=50),
        ))
        ctx.db.downloads.upsert_download = AsyncMock()

        handle = MagicMock()
        handle.status.return_value = MagicMock(
            all_time_download=1000,
            total_done=1000,
            all_time_upload=2500,
            upload_rate=100,
        )

        failing_callback = AsyncMock(side_effect=RuntimeError('callback crashed'))
        ctx.on_complete_callback = failing_callback
        monitor = DownloadLifecycleMonitor(ctx)

        with patch('asyncio.sleep', AsyncMock(return_value=None)):
            await monitor._monitor_seeding('test123', handle)

        failing_callback.assert_awaited_once_with('test123')
        ctx.db.downloads.upsert_download.assert_awaited()

    @pytest.mark.asyncio
    async def test_completion_callback_not_called_when_not_set(self):
        """No callback should be invoked when on_complete_callback is None."""
        from src.core.downloader_lifecycle import (
            DownloadLifecycleContext,
            DownloadLifecycleMonitor,
            SeedingPolicy,
        )

        ctx = DownloadLifecycleContext(
            db=MagicMock(),
            supervisor=FakeSupervisor(),
            engine=FakeEngine(),
            download_dir="/tmp",
            seed_policy=SeedingPolicy(seed_ratio_target=2.0, seed_duration_hours=48),
            on_stats_callback=None,
            on_ready_callback=None,
            on_complete_callback=None,
        )
        ctx.db.downloads = MagicMock()
        ctx.db.downloads.get_download = AsyncMock(return_value=DownloadItem(
            id='test123', item_name='Test', magnet='magnet:?xt=urn:btih:abc',
            completed_at=datetime.now(timezone.utc) - timedelta(hours=50),
        ))
        ctx.db.downloads.upsert_download = AsyncMock()

        handle = MagicMock()
        handle.status.return_value = MagicMock(
            all_time_download=1000,
            total_done=1000,
            all_time_upload=2500,
            upload_rate=100,
        )

        monitor = DownloadLifecycleMonitor(ctx)

        with patch('asyncio.sleep', AsyncMock(return_value=None)):
            await monitor._monitor_seeding('test123', handle)

        ctx.db.downloads.upsert_download.assert_awaited()


# ── Ready Callback Tests ───────────────────────────────────────


class TestReadyCallback:
    """Tests for the seeding-started (ready) callback."""

    def _make_dm(self):
        from src.core.downloader import DownloadManager, DownloadDependencies
        return DownloadManager(DownloadDependencies(
            download_dir="/tmp/ljs_test",
            db=MagicMock(),
            supervisor=FakeSupervisor(),
            engine=FakeEngine(),
            queue=FakeQueueManager(),
            bandwidth=FakeBandwidthManager(),
            settings_manager=MagicMock(),
        ))

    def test_set_ready_callback_stores_callback(self):
        dm = self._make_dm()
        callback = lambda x: None
        dm.set_ready_callback(callback)
        assert dm._on_ready_callback is callback

    def test_set_ready_callback_overrides_previous(self):
        dm = self._make_dm()
        old = lambda x: None
        new = lambda x: None
        dm.set_ready_callback(old)
        dm.set_ready_callback(new)
        assert dm._on_ready_callback is new
        assert dm._on_ready_callback is not old


# ── SetFilePriority Tests via Dependency Injection ─────────────


class TestSetFilePriority:
    """Tests for per-file priority management."""

    def _make_dm(self):
        from src.core.downloader import DownloadManager, DownloadDependencies
        return DownloadManager(DownloadDependencies(
            download_dir="/tmp/ljs_test",
            db=MagicMock(),
            supervisor=FakeSupervisor(),
            engine=FakeEngine(),
            queue=FakeQueueManager(),
            bandwidth=FakeBandwidthManager(),
            settings_manager=MagicMock(),
        ))

    @pytest.mark.asyncio
    async def test_set_file_priority_nonexistent_returns_false(self):
        dm = self._make_dm()
        dm._db.downloads = MagicMock()
        dm._db.downloads.get_download = AsyncMock(return_value=None)
        result = await dm.set_file_priority('nonexistent', 0, 7)
        assert result is False

    @pytest.mark.asyncio
    async def test_set_file_priority_updates_model(self):
        from src.core.models import DownloadItem, DownloadFileInfo
        dm = self._make_dm()

        item = DownloadItem(
            id='test', item_name='Test', magnet='magnet:?xt=urn:btih:test',
            files=[
                DownloadFileInfo(file_index=0, file_path='e01.mkv', priority=4),
                DownloadFileInfo(file_index=1, file_path='e02.mkv', priority=4),
            ],
        )

        dm._db = MagicMock()
        dm._db.downloads = MagicMock()
        dm._db.downloads.get_download = AsyncMock(return_value=item)
        dm._db.downloads.upsert_download = AsyncMock()

        result = await dm.set_file_priority('test', 0, 7)
        assert result is True
        assert item.files[0].priority == 7
        assert item.files[1].priority == 4  # unchanged
        dm._db.downloads.upsert_download.assert_awaited_once_with(item)


# ── TorrentFileMetadataParser Tests ───────────────────────────


class TestTorrentFileMetadataParser:
    """Tests for the extracted TorrentFileMetadataParser."""

    @pytest.mark.asyncio
    async def test_builds_stats_files_from_infos(self):
        from src.core.downloader_lifecycle import TorrentFileMetadataParser
        from src.core.models import DownloadFileInfo

        infos = [
            DownloadFileInfo(file_index=0, file_path='S01E01.mkv', size=500, season=1, episode=1, priority=7, status='pending'),
            DownloadFileInfo(file_index=1, file_path='S01E02.mkv', size=400, season=1, episode=2, priority=6, status='complete'),
        ]
        parser = TorrentFileMetadataParser()
        stats = parser.build_stats_files(infos)
        assert len(stats) == 2
        assert stats[0]['file_index'] == 0
        assert stats[0]['episode'] == 1
        assert stats[0]['priority'] == 7
        assert stats[0]['status'] == 'pending'
        assert stats[1]['file_index'] == 1
        assert stats[1]['episode'] == 2
        assert stats[1]['progress'] == 1.0


# ── DownloadProgressStore Tests ────────────────────────────────


class TestDownloadProgressStore:
    """Tests for the extracted DownloadProgressStore."""

    def test_update_item_sets_fields(self):
        from src.core.downloader_lifecycle import DownloadProgressStore
        from src.core.models import DownloadItem

        item = DownloadItem(id='x', item_name='X', magnet='magnet:1')
        stats = {
            "progress": 0.5,
            "download_rate": 1000,
            "upload_rate": 200,
            "num_peers": 5,
            "total_size": 10000,
            "downloaded_bytes": 5000,
            "eta_seconds": 5.0,
        }
        store = DownloadProgressStore()
        updated = store.update_item(item, stats)
        assert updated.progress == 0.5
        assert updated.download_rate == 1000
        assert updated.upload_rate == 200
        assert updated.num_peers == 5
        assert updated.total_size == 10000
        assert updated.downloaded_bytes == 5000
        assert updated.eta_seconds == 5.0

    def test_mark_all_complete(self):
        from src.core.downloader_lifecycle import DownloadProgressStore
        from src.core.models import DownloadItem, DownloadFileInfo

        item = DownloadItem(
            id='x', item_name='X', magnet='magnet:1',
            files=[
                DownloadFileInfo(file_index=0, file_path='a.mkv', status='downloading'),
                DownloadFileInfo(file_index=1, file_path='b.mkv', status='pending'),
            ],
        )
        store = DownloadProgressStore()
        store.mark_all_complete(item)
        for f in item.files:
            assert f.status == 'complete'


# ── ComputeTargetPath Tests (unchanged) ───────────────────────


class TestComputeTargetPath:
    """Tests for TvShowCategory.compute_target_path."""

    def test_compute_target_path_basic(self, settings):
        from src.core.categories.tv import TvShowCategory
        cat = TvShowCategory()
        target = cat.compute_target_path(
            source_name='Show.S05E01.mkv',
            item_name='Test Show',
            season=5,
            episode=1,
            library_root=f"{settings.library_root}/TV Shows",
        )
        assert target is not None
        assert 'Test Show' in str(target)
        assert 'Season 5' in str(target)
        assert 'S05E01' in str(target.name)

    def test_compute_target_path_with_quality(self, settings):
        from src.core.categories.tv import TvShowCategory
        cat = TvShowCategory()
        target = cat.compute_target_path(
            source_name='Show.S05E01.1080p.mkv',
            item_name='Test Show',
            season=5,
            episode=1,
            library_root=f"{settings.library_root}/TV Shows",
        )
        assert target is not None
        assert target.suffix == '.mkv'
        assert 'Test Show' in str(target)

    def test_compute_target_path_creates_no_dirs(self, settings, tmp_path):
        from src.core.categories.tv import TvShowCategory
        cat = TvShowCategory()
        target = cat.compute_target_path(
            source_name='Show.S01E01.mkv',
            item_name='NoDir',
            season=1,
            episode=1,
            library_root=str(tmp_path / 'library' / 'TV Shows'),
        )
        assert not target.parent.exists()
