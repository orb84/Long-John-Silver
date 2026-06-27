#!/usr/bin/env python3
"""Round 262 regressions for hard-stopping unsafe TV auto-downloads.

The logs showed completed or opt-out TV library shows still searching/starting
background rows after restart/cancel.  These checks cover the generic-category
handoffs that prevent that class of failure without teaching core code TV
semantics.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import types
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("aiosqlite", types.SimpleNamespace(Connection=object, Row=dict, Cursor=object))

from src.core.categories.base_contract import CategoryContractMixin
from src.core.categories.tv import TvShowCategory
from src.core.domain_models.media import ItemList
from src.core.downloader import DownloadManager
from src.core.models import DownloadImportContext, DownloadItem, DownloadStatus, TvShowItem
from src.core.scheduler import MediaScheduler
TV_CATEGORY_ID = "t" + "v"


class Check:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def ok(self, condition: bool, message: str) -> None:
        if not condition:
            self.failures.append(message)

    def finish(self) -> None:
        if self.failures:
            print("Round 262 TV auto-download hard-stop failures:")
            for failure in self.failures:
                print(f" - {failure}")
            raise SystemExit(1)
        print("round262_tv_autodownload_hard_stop_tests: OK")


def run(coro: Any) -> Any:
    return asyncio.run(coro)


class FakeSettings:
    def __init__(self, item: TvShowItem, *, global_auto: bool = True) -> None:
        self.auto_download = global_auto
        self.language = "Italian"
        self.tracked_items = ItemList(items=[item])


class FakeSettingsManager:
    def __init__(self, item: TvShowItem, *, global_auto: bool = True) -> None:
        self.settings = FakeSettings(item, global_auto=global_auto)


class FakeRegistry:
    def __init__(self, category: Any) -> None:
        self._category = category

    def get(self, category_id: str) -> Any:
        return self._category if category_id == TV_CATEGORY_ID else None


class FakeUnitRepo:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    async def list_category_units(self, category_id: str, item_id: str, status: str | None = None) -> list[dict[str, Any]]:
        return [row for row in self.rows if status is None or row.get("status") == status]


class FakeDb:
    def __init__(self, units: list[dict[str, Any]] | None = None) -> None:
        self.media = FakeUnitRepo(units or [])


class FakeReleaseWatchRepo:
    def __init__(self) -> None:
        self.cancelled: list[tuple[str, str, str, str]] = []

    async def cancel_unit(self, category_id: str, item_id: str, unit_key: str, *, error: str = "", outcome: dict[str, Any] | None = None) -> None:
        self.cancelled.append((category_id, item_id, unit_key, error))


class FakeDownloadsRepo:
    def __init__(self, rows: list[DownloadItem]) -> None:
        self.rows = {row.id: row for row in rows}
        self.upserts: list[DownloadItem] = []

    async def get_active_downloads(self) -> list[DownloadItem]:
        return list(self.rows.values())

    async def upsert_download(self, item: DownloadItem) -> None:
        self.rows[item.id] = item
        self.upserts.append(item)


class FakeRecoveryDb:
    def __init__(self, rows: list[DownloadItem]) -> None:
        self.downloads = FakeDownloadsRepo(rows)


class FakeEngine:
    async def initialize(self) -> None:
        return None


class FakeQueue:
    def __init__(self) -> None:
        self.synced: list[list[str]] = []

    def sync_active(self, ids: list[str]) -> None:
        self.synced.append(list(ids))

    def active_count(self) -> int:
        return 0


class FakeDeps:
    def __init__(self, settings_manager: FakeSettingsManager, registry: FakeRegistry) -> None:
        self.download_dir = "/tmp"
        self.db = None
        self.supervisor = None
        self.settings_manager = settings_manager
        self.max_concurrent = 3
        self.engine = None
        self.queue = None
        self.bandwidth = None
        self.seed_ratio_target = 0.0
        self.seed_duration_hours = 0.0
        self.category_registry = registry
        self.storage_monitor = None
        self.torrent_resolver = None
        self.blacklist = None


def tv_download_context(item_id: str, season: int, episode: int) -> DownloadImportContext:
    return DownloadImportContext(
        category_id="tv",
        item_id=item_id,
        canonical_title=item_id,
        display_title=item_id,
        season=season,
        episode=episode,
        unit_descriptor={
            "granularity": "episode",
            "label": f"S{season:02d}E{episode:02d}",
            "stable_key": f"S{season:02d}E{episode:02d}",
            "coordinates": {"season": season, "episode": episode},
        },
    )


def test_tv_release_watch_requires_current_item_true(check: Check) -> None:
    item = TvShowItem(key="Silicon Valley", auto_download=False)
    category = TvShowCategory()
    scheduler = object.__new__(MediaScheduler)
    scheduler._settings_manager = FakeSettingsManager(item, global_auto=True)
    check.ok(
        scheduler._release_watch_auto_download_allowed({"auto_download": True}, item, category) is False,
        "TV stale requirements.auto_download=true must not override current per-show off",
    )
    item.auto_download = True
    check.ok(
        scheduler._release_watch_auto_download_allowed({"auto_download": False}, item, category) is True,
        "TV current per-show on must be enough even if an old requirements snapshot says false",
    )


def test_generic_default_hook_preserves_legacy_requirement_behavior(check: Check) -> None:
    hook_owner = CategoryContractMixin()
    item = types.SimpleNamespace(auto_download=None)
    check.ok(
        hook_owner.release_watch_auto_download_allowed(item, {"auto_download": True}, types.SimpleNamespace(auto_download=False)) is True,
        "base categories may still honor stored requirements when they have no stricter policy",
    )


def test_queued_background_start_uses_tv_safe_default(check: Check) -> None:
    item = TvShowItem(key="Gomorra", auto_download=None)
    manager = object.__new__(DownloadManager)
    manager._settings_manager = FakeSettingsManager(item, global_auto=True)
    manager._deps = types.SimpleNamespace(category_registry=FakeRegistry(TvShowCategory()))
    manager._explicit_start_allowed = set()
    row = DownloadItem(
        id="gom1",
        item_name="Gomorra",
        item_id="Gomorra",
        category_id="tv",
        magnet="magnet:?xt=urn:btih:g",
        reason="Auto-discovery for Gomorra S01E01 Italian",
        import_context=tv_download_context("Gomorra", 1, 1),
    )
    check.ok(manager._tracked_item_auto_download_override(row) is False, "TV null/off must be a hard background-start veto even if global automation is on")
    check.ok(run(manager._can_start_queued_download(row)) is False, "unsafe TV auto rows must not start from the queue")


def test_recover_does_not_resume_old_background_downloading_row(check: Check) -> None:
    item = TvShowItem(key="Silicon Valley", auto_download=False)
    row = DownloadItem(
        id="sv1",
        item_name="Silicon Valley",
        item_id="Silicon Valley",
        category_id="tv",
        magnet="magnet:?xt=urn:btih:sv",
        status=DownloadStatus.DOWNLOADING,
        reason="Auto-discovery for Silicon Valley S06E08 Italian",
        import_context=tv_download_context("Silicon Valley", 6, 8),
    )
    manager = object.__new__(DownloadManager)
    manager._settings_manager = FakeSettingsManager(item, global_auto=True)
    manager._deps = types.SimpleNamespace(category_registry=FakeRegistry(TvShowCategory()))
    manager._db = FakeRecoveryDb([row])
    manager._queue = FakeQueue()
    manager._max_concurrent = 3
    manager._explicit_start_allowed = set()
    manager.repair_partial_file_suffixes = lambda: asyncio.sleep(0)
    started: list[str] = []

    async def fake_start(download: DownloadItem) -> None:
        started.append(download.id)

    manager._start_download = fake_start
    run(manager.recover_downloads())
    check.ok(started == [], "old unsafe background DOWNLOADING rows must be held on restart instead of resumed")
    check.ok("sv1" not in manager._explicit_start_allowed, "old background rows must not become implicitly user-approved")


def test_tv_discovery_skips_existing_episode(check: Check) -> None:
    category = TvShowCategory()
    item = TvShowItem(key="Silicon Valley")
    context = types.SimpleNamespace(db=FakeDb([
        {"season": 6, "episode": 8, "status": "downloaded", "unit_key": "Silicon Valley S06E08"},
    ]))
    check.ok(run(category.discovery_already_satisfied(item, "S06E08", context)) is True, "TV discovery should skip an episode already in canonical downloaded units")


def test_user_cancel_suppresses_matching_release_watch(check: Check) -> None:
    repo = FakeReleaseWatchRepo()
    manager = object.__new__(DownloadManager)
    manager._db = types.SimpleNamespace(release_watches=repo)
    row = DownloadItem(
        id="cancel1",
        item_name="Battlestar Galactica",
        item_id="Battlestar Galactica",
        category_id="tv",
        magnet="magnet:?xt=urn:btih:bsg",
        import_context=tv_download_context("Battlestar Galactica", 1, 14),
    )
    run(manager._cancel_matching_release_watch_for_download(row))
    check.ok(repo.cancelled == [("tv", "Battlestar Galactica", "S01E14", "matching download was cancelled by user")], "cancelled download should cancel the matching release watch unit")


def test_source_retires_stale_watch_plan_units(check: Check) -> None:
    scheduler_source = (ROOT / "src/core/scheduler.py").read_text(encoding="utf-8")
    repo_source = (ROOT / "src/core/repositories/release_watch.py").read_text(encoding="utf-8")
    check.ok("retire_missing_for_item" in scheduler_source, "watch-plan rebuilds must retire stale release-watch rows")
    check.ok("cancel_unit" in repo_source, "release watches need an explicit unit cancellation path")


if __name__ == "__main__":
    check = Check()
    test_tv_release_watch_requires_current_item_true(check)
    test_generic_default_hook_preserves_legacy_requirement_behavior(check)
    test_queued_background_start_uses_tv_safe_default(check)
    test_recover_does_not_resume_old_background_downloading_row(check)
    test_tv_discovery_skips_existing_episode(check)
    test_user_cancel_suppresses_matching_release_watch(check)
    test_source_retires_stale_watch_plan_units(check)
    check.finish()
