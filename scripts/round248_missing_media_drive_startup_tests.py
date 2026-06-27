#!/usr/bin/env python3
"""Round 248 checks for unplugged media-drive startup and reconnect recovery."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.domain_models.settings import Settings
from src.core.download_storage_recovery import DownloadStorageRecoveryService, StorageUnavailableDownloadReason
from src.core.models import DownloadItem, DownloadStatus
from src.core.storage import StorageMonitor
from src.core.storage_path_availability import StoragePathGuard, StoragePathUnavailableError
from src.core.torrent_engine import TorrentEngine


class Check:
    """Small assertion collector for script-style regression tests."""

    def __init__(self) -> None:
        self.failures: list[str] = []

    def ok(self, condition: bool, message: str) -> None:
        """Record a failed check without aborting the whole script."""
        if not condition:
            self.failures.append(message)

    def finish(self) -> None:
        """Exit non-zero when any check failed."""
        if self.failures:
            print("Round 248 missing media drive startup failures:")
            for failure in self.failures:
                print(f" - {failure}")
            raise SystemExit(1)
        print("Round 248 missing media drive startup tests passed.")


class SettingsHolder:
    """Minimal settings manager facade for StorageMonitor tests."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings


class FakeDownloadRepository:
    """In-memory download repository for reconnect recovery tests."""

    def __init__(self, items: list[DownloadItem]) -> None:
        self.items = {item.id: item for item in items}

    async def get_active_downloads(self) -> list[DownloadItem]:
        """Return non-terminal fake downloads."""
        return list(self.items.values())

    async def upsert_download(self, item: DownloadItem) -> None:
        """Persist a fake download update."""
        self.items[item.id] = item


class FakeDatabase:
    """In-memory database facade exposing a downloads repository."""

    def __init__(self, items: list[DownloadItem]) -> None:
        self.downloads = FakeDownloadRepository(items)


def _run(coro: object) -> object:
    """Run an async test coroutine."""
    return asyncio.run(coro)


def test_missing_macos_volume_is_not_creatable(check: Check) -> None:
    """A missing /Volumes drive must be unavailable, not auto-created."""
    path = Path("/Volumes/Seagate Bas/downloads")
    availability = StoragePathGuard.inspect(path)
    check.ok(not availability.available_for_writes, "missing /Volumes drive should not be considered writable")
    check.ok(availability.status == "unavailable", "missing /Volumes drive should be unavailable")
    check.ok("missing or unplugged volume" in availability.reason, "reason should explain unplugged/missing volume")
    check.ok(not path.exists(), "probe must not create the missing volume path")


def test_regular_relative_download_dir_can_be_prepared(check: Check) -> None:
    """Normal local download directories should still be created."""
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "downloads" / "nested"
        availability = StoragePathGuard.try_prepare_directory(target)
        check.ok(availability.available_for_writes, "local path should be available after preparation")
        check.ok(target.is_dir(), "local path should be created")


def test_torrent_engine_constructor_does_not_crash_on_missing_drive(check: Check) -> None:
    """Startup construction should log the issue but not kill the app."""
    engine = TorrentEngine("/Volumes/Seagate Bas/downloads", max_concurrent=1)
    check.ok(engine.download_dir == "/Volumes/Seagate Bas/downloads", "engine should preserve configured download_dir")


def test_torrent_engine_start_reports_typed_storage_error(check: Check) -> None:
    """Starting a torrent on a missing drive should fail before libtorrent import/work."""
    engine = TorrentEngine("/Volumes/Seagate Bas/downloads", max_concurrent=1)
    try:
        _run(engine.add_magnet("magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567", "missing-drive-test"))
    except StoragePathUnavailableError as exc:
        check.ok("missing or unplugged volume" in str(exc), "engine should raise a clear missing-volume error")
    except Exception as exc:  # pragma: no cover - script diagnostic
        check.ok(False, f"engine raised wrong exception type: {type(exc).__name__}: {exc}")
    else:  # pragma: no cover - script diagnostic
        check.ok(False, "engine.add_magnet should not succeed when the configured drive is missing")


def test_storage_monitor_marks_download_dir_critical(check: Check) -> None:
    """The storage UI/tool report should surface unavailable configured roots."""
    settings = Settings(download_dir="/Volumes/Seagate Bas/downloads")
    monitor = StorageMonitor(SettingsHolder(settings))
    report = monitor.build_report()
    download_paths = [p for p in report.paths if p.purpose == "download_dir"]
    check.ok(bool(download_paths), "storage report should include download_dir")
    if download_paths:
        usage = download_paths[0]
        check.ok(usage.status == "critical", "unavailable download_dir should be critical")
        check.ok("missing or unplugged volume" in usage.message, "storage message should explain missing volume")
    decision = monitor.check_download_capacity(estimated_bytes=1024)
    check.ok(not decision.ok, "capacity check should block downloads while download_dir is unavailable")
    check.ok("missing or unplugged volume" in decision.reason, "capacity decision should carry clear reason")


def test_reconnected_media_drive_requeues_storage_stalled_download(check: Check) -> None:
    """Storage recovery should notice a reconnected volume and requeue held downloads."""
    with tempfile.TemporaryDirectory() as tmp:
        volumes = Path(tmp) / "Volumes"
        volumes.mkdir()
        target = volumes / "Seagate Bas" / "downloads"
        item = DownloadItem(
            id="storage-stalled-1",
            item_name="Reconnect Test",
            magnet="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
            status=DownloadStatus.STALLED,
            reason=StorageUnavailableDownloadReason.from_error(RuntimeError("missing or unplugged volume")),
            save_path=str(target),
        )
        db = FakeDatabase([item])
        service = DownloadStorageRecoveryService(db, str(target), poll_seconds=2)
        first = _run(service.recover_once())
        check.ok(first == 0, "recovery should not requeue while the volume root is absent")
        check.ok(db.downloads.items[item.id].status == DownloadStatus.STALLED, "item should remain stalled before reconnect")
        check.ok(not target.exists(), "missing mount path should not be created before reconnect")

        (volumes / "Seagate Bas").mkdir()
        second = _run(service.recover_once())
        recovered = db.downloads.items[item.id]
        check.ok(second == 1, "recovery should requeue exactly one download after reconnect")
        check.ok(recovered.status == DownloadStatus.QUEUED, "item should be queued after reconnect")
        check.ok("storage reconnected" in recovered.reason, "recovery reason should mention reconnect")
        check.ok(target.is_dir(), "download leaf directory should be created on the reconnected volume")


def main() -> None:
    """Run Round 248 regression checks."""
    check = Check()
    test_missing_macos_volume_is_not_creatable(check)
    test_regular_relative_download_dir_can_be_prepared(check)
    test_torrent_engine_constructor_does_not_crash_on_missing_drive(check)
    test_torrent_engine_start_reports_typed_storage_error(check)
    test_storage_monitor_marks_download_dir_critical(check)
    test_reconnected_media_drive_requeues_storage_stalled_download(check)
    check.finish()


if __name__ == "__main__":
    main()
