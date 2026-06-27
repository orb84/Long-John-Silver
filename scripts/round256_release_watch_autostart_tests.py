#!/usr/bin/env python3
"""Round 256 regressions for release-watch auto-start handoff.

Logs from Star City showed the release watcher finding S01E03/S01E04 and
calling the download path, but queued/duplicate rows remained held because the
queue start gate only looked at the global auto-download flag.  TV now owns a
per-show default-on policy, so the generic queue must honor the tracked item
``auto_download`` envelope before falling back to the global setting.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import tempfile
import types
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("aiosqlite", types.SimpleNamespace(Connection=object, Row=dict, Cursor=object))

from src.core.download_dependencies import DownloadDependencies
from src.core.downloader import DownloadManager
from src.core.domain_models.media import ItemList
from src.core.models import DownloadItem, DownloadStatus, TvShowItem


class Check:
    """Small assertion collector for script-style regression checks."""

    def __init__(self) -> None:
        self.failures: list[str] = []

    def ok(self, condition: bool, message: str) -> None:
        """Record a failure when ``condition`` is false."""
        if not condition:
            self.failures.append(message)

    def finish(self) -> None:
        """Exit non-zero when any check failed."""
        if self.failures:
            print("Round 256 release-watch auto-start failures:")
            for failure in self.failures:
                print(f" - {failure}")
            raise SystemExit(1)
        print("round256_release_watch_autostart_tests: OK")


def run(coro: Any) -> Any:
    """Run one async check."""
    return asyncio.run(coro)


class FakeSettings:
    """Settings double with global automation deliberately disabled."""

    def __init__(self, item_auto: bool | None = True) -> None:
        self.auto_download = False
        self.language = "Italian"
        self.tracked_items = ItemList(items=[TvShowItem(key="Star City", auto_download=item_auto)])


class FakeSettingsManager:
    """Settings manager double."""

    def __init__(self, item_auto: bool | None = True) -> None:
        self.settings = FakeSettings(item_auto=item_auto)


class FakeDownloadsRepo:
    """Downloads repo double unused by start-gate checks."""

    async def upsert_download(self, _item: Any) -> None:
        return None


class FakeDB:
    """Database double."""

    def __init__(self) -> None:
        self.downloads = FakeDownloadsRepo()


class FakeSupervisor:
    """Task supervisor double."""

    def cancel(self, _name: str) -> None:
        return None

    def cancel_by_prefix(self, _prefix: str) -> None:
        return None

    def spawn_restartable(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class FakeEngine:
    """Torrent engine double."""

    async def initialize(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def remove_torrent(self, _download_id: str) -> None:
        return None


class FakeQueue:
    """Queue double."""

    def active_count(self) -> int:
        return 0

    def deregister_active(self, _download_id: str) -> None:
        return None

    async def run_loop(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class FakeBandwidth:
    """Bandwidth manager double."""

    async def run_loop(self) -> None:
        return None


def manager(item_auto: bool | None = True) -> DownloadManager:
    """Build a DownloadManager with only the deps needed for start-gate checks."""
    return DownloadManager(
        DownloadDependencies(
            download_dir=tempfile.mkdtemp(prefix="ljs-round256-"),
            db=FakeDB(),
            supervisor=FakeSupervisor(),
            engine=FakeEngine(),
            queue=FakeQueue(),
            bandwidth=FakeBandwidth(),
            settings_manager=FakeSettingsManager(item_auto=item_auto),
            max_concurrent=1,
        )
    )


def queued_star_city(reason: str = "Auto-discovery for Star City S01E03") -> DownloadItem:
    """Return a representative release-watch queued download row."""
    return DownloadItem(
        id="star-s01e03",
        item_name="Star City",
        item_id="Star City",
        category_id="tv",
        magnet="magnet:?xt=urn:btih:star",
        status=DownloadStatus.QUEUED,
        reason=reason,
    )


def test_item_policy_overrides_global_disabled(check: Check) -> None:
    """Per-show auto-download enabled must allow release-watch rows to start."""
    allowed = run(manager(item_auto=True)._can_start_queued_download(queued_star_city()))
    check.ok(allowed is True, "tracked item auto_download=True should override global auto_download=False")


def test_item_policy_disabled_blocks_background_rows(check: Check) -> None:
    """A per-show checkbox off value must still hold background rows."""
    allowed = run(manager(item_auto=False)._can_start_queued_download(queued_star_city()))
    check.ok(allowed is False, "tracked item auto_download=False should block release-watch rows")


def test_unknown_item_still_inherits_global_disabled(check: Check) -> None:
    """Rows without a tracked-item override should keep the old global behavior."""
    item = queued_star_city()
    item.item_id = "Unknown Show"
    item.item_name = "Unknown Show"
    allowed = run(manager(item_auto=True)._can_start_queued_download(item))
    check.ok(allowed is False, "unknown background rows should still inherit global auto_download=False")


def test_user_approved_rows_still_start(check: Check) -> None:
    """Explicit user approval remains independent of automation settings."""
    allowed = run(manager(item_auto=False)._can_start_queued_download(queued_star_city("user approved discovery for Star City")))
    check.ok(allowed is True, "user-approved rows should start even when per-item automation is off")


def test_duplicate_and_blocked_notification_paths_present(check: Check) -> None:
    """Keep source-level guards for the duplicate/persistent failure path from logs."""
    downloader = (ROOT / "src/core/downloader.py").read_text(encoding="utf-8")
    scheduler = (ROOT / "src/core/scheduler.py").read_text(encoding="utf-8")
    check.ok(
        "Auto-download duplicate magnet" in downloader,
        "duplicate queued magnets should be promoted when tracked-item automation allows start",
    )
    check.ok(
        "_tracked_item_auto_download_override" in downloader,
        "download queue gate should consult tracked item automation policy",
    )
    check.ok(
        "release_watch_queue_blocked" in scheduler,
        "storage/availability blocks should create visible release-watch notifications",
    )
    check.ok(
        "download blocked" in scheduler,
        "release-watch storage failures should explain that the release was found but blocked",
    )


if __name__ == "__main__":
    check = Check()
    test_item_policy_overrides_global_disabled(check)
    test_item_policy_disabled_blocks_background_rows(check)
    test_unknown_item_still_inherits_global_disabled(check)
    test_user_approved_rows_still_start(check)
    test_duplicate_and_blocked_notification_paths_present(check)
    check.finish()
