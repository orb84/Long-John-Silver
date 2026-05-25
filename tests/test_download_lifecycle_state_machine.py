"""Tests for explicit download lifecycle transitions."""

import pytest

from src.core.downloader_lifecycle import DownloadLifecycleStateMachine
from src.core.models import DownloadItem, DownloadStatus


def _item(status: DownloadStatus) -> DownloadItem:
    return DownloadItem(id="d1", item_name="Example", magnet="magnet:?xt=urn:btih:1", status=status)


def test_lifecycle_allows_happy_path() -> None:
    """Queued downloads may download, seed, then complete."""
    sm = DownloadLifecycleStateMachine()
    item = _item(DownloadStatus.QUEUED)

    sm.transition(item, DownloadStatus.DOWNLOADING)
    sm.transition(item, DownloadStatus.SEEDING)
    sm.transition(item, DownloadStatus.COMPLETE)

    assert item.status == DownloadStatus.COMPLETE


def test_lifecycle_rejects_complete_to_downloading_without_force() -> None:
    """Terminal states cannot silently re-enter active download flow."""
    sm = DownloadLifecycleStateMachine()
    item = _item(DownloadStatus.COMPLETE)

    with pytest.raises(ValueError):
        sm.transition(item, DownloadStatus.DOWNLOADING)


def test_lifecycle_force_supports_repair_jobs() -> None:
    """Repair/reconciliation jobs can intentionally correct stale states."""
    sm = DownloadLifecycleStateMachine()
    item = _item(DownloadStatus.COMPLETE)

    sm.transition(item, DownloadStatus.DOWNLOADING, force=True, reason="repair")

    assert item.status == DownloadStatus.DOWNLOADING
    assert item.reason == "repair"

@pytest.mark.asyncio
async def test_monitor_failure_marks_download_failed_and_releases_slots(monkeypatch) -> None:
    """Unexpected monitor crashes should not leave active downloads stranded."""
    from src.core.downloader_lifecycle import DownloadLifecycleContext, DownloadLifecycleMonitor

    item = _item(DownloadStatus.DOWNLOADING)

    class DownloadsRepo:
        async def get_download(self, download_id):
            return item

        async def upsert_download(self, updated):
            nonlocal item
            item = updated

    class DB:
        downloads = DownloadsRepo()

    class Queue:
        def __init__(self):
            self.released = []

        def deregister_active(self, download_id):
            self.released.append(download_id)

    class Registry:
        def __init__(self):
            self.unregistered = []

        def unregister(self, download_id):
            self.unregistered.append(download_id)

    async def boom(self, download_id, handle):
        raise RuntimeError("metadata parser exploded")

    queue = Queue()
    registry = Registry()
    ctx = DownloadLifecycleContext(
        db=DB(), supervisor=None, engine=None, download_dir="/tmp", queue=queue, monitor_registry=registry
    )
    monitor = DownloadLifecycleMonitor(ctx)
    monkeypatch.setattr(DownloadLifecycleMonitor, "_wait_for_metadata", boom)

    await monitor.run("d1", object())

    assert item.status == DownloadStatus.FAILED
    assert "metadata parser exploded" in item.reason
    assert queue.released == ["d1"]
    assert registry.unregistered == ["d1"]
