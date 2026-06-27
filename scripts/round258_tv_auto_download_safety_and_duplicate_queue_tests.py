#!/usr/bin/env python3
"""Round 258 regressions for safe TV automation and duplicate queue guards.

The Round 257/258 logs showed old library shows auto-starting a large backlog,
including duplicate rows for the same TV units, until the disk filled.  TV
new-episode automation must now be opt-in by default, stale watch snapshots must
not override the current item switch, and download identity dedupe must work even
when provider IDs are missing but the category-owned unit descriptor is present.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import types
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("aiosqlite", types.SimpleNamespace(Connection=object, Row=dict, Cursor=object))

from src.core.categories.tv import TvShowCategory
from src.core.domain_models.media import ItemList
from src.core.models import DownloadImportContext, DownloadItem, TvShowItem
from src.core.repositories.download import _import_contexts_overlap
from src.core.scheduler import MediaScheduler
from src.core.downloader import DownloadManager


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
            print("Round 258 TV auto-download safety failures:")
            for failure in self.failures:
                print(f" - {failure}")
            raise SystemExit(1)
        print("round258_tv_auto_download_safety_and_duplicate_queue_tests: OK")


def run(coro: Any) -> Any:
    """Run an async test scenario."""
    return asyncio.run(coro)


class FakeSettings:
    """Settings double for scheduler and TV hook checks."""

    def __init__(self, item: TvShowItem) -> None:
        self.auto_download = False
        self.language = "English"
        self.tracked_items = ItemList(items=[item])


class FakeSettingsManager:
    """Settings manager double that records saves."""

    def __init__(self, item: TvShowItem) -> None:
        self.settings = FakeSettings(item)
        self.saved = 0

    def save(self, _settings: Any | None = None) -> None:
        """Record a settings save call."""
        self.saved += 1


def context_for_scheduler(item: TvShowItem) -> MediaScheduler:
    """Create a mostly-uninitialized MediaScheduler for pure helper calls."""
    scheduler = object.__new__(MediaScheduler)
    scheduler._settings_manager = FakeSettingsManager(item)
    return scheduler


def episode_context(season: int, episode: int, *, metadata: dict[str, Any] | None = None) -> DownloadImportContext:
    """Return a provider-light TV import context for one episode."""
    return DownloadImportContext(
        category_id="tv",
        item_id="Star City",
        canonical_title="Star City",
        display_title="Star City",
        season=season,
        episode=episode,
        unit_descriptor={
            "granularity": "episode",
            "label": f"S{season:02d}E{episode:02d}",
            "stable_key": f"S{season:02d}E{episode:02d}",
            "sort_key": [season, episode],
            "coordinates": {"season": season, "episode": episode},
        },
        metadata_snapshot=metadata or {},
    )


def season_context(season: int) -> DownloadImportContext:
    """Return a provider-light TV import context for a whole season."""
    return DownloadImportContext(
        category_id="tv",
        item_id="Star City",
        canonical_title="Star City",
        display_title="Star City",
        season=season,
        unit_descriptor={
            "granularity": "season",
            "label": f"Season {season}",
            "stable_key": f"S{season:02d}",
            "sort_key": [season, 0],
            "coordinates": {"season": season},
        },
    )


def test_release_watch_stale_snapshot_cannot_override_current_off(check: Check) -> None:
    """A stale requirements.auto_download=true row must not override item off."""
    item = TvShowItem(key="Star City", auto_download=False)
    scheduler = context_for_scheduler(item)
    allowed = scheduler._release_watch_auto_download_allowed({"auto_download": True}, item)
    check.ok(allowed is False, "current per-show auto_download=False must veto stale watch snapshots")


def test_provider_light_contexts_dedupe_same_episode(check: Check) -> None:
    """Rows without provider IDs should still dedupe by category item + descriptor."""
    first = episode_context(1, 3)
    second = episode_context(1, 3)
    check.ok(first.stable_provider_key.startswith("category:tv:star city"), "provider-light contexts need a category/item fallback key")
    check.ok(_import_contexts_overlap(first, second), "same TV episode descriptors should overlap without provider IDs")


def test_provider_light_contexts_dedupe_season_pack_against_episode(check: Check) -> None:
    """A queued season pack should suppress duplicate episode rows for that season."""
    pack = season_context(1)
    episode = episode_context(1, 4)
    other_season = episode_context(2, 1)
    check.ok(_import_contexts_overlap(pack, episode), "season descriptor should overlap episode descriptors in the same item/season")
    check.ok(not _import_contexts_overlap(pack, other_season), "season descriptor must not overlap other seasons")


def test_manual_active_airing_episode_does_not_enable_show_automation(check: Check) -> None:
    """Manual downloads do not silently opt shows into follow-up automation."""
    next_week = (datetime.now(timezone.utc) + timedelta(days=7)).date().isoformat()
    item = TvShowItem(
        key="Star City",
        auto_download=False,
        metadata={
            "status": "Running",
            "next_episode": {"season": 1, "number": 5, "airdate": next_week},
        },
    )
    settings = FakeSettingsManager(item)
    category = TvShowCategory()
    changed = run(category.maybe_enable_auto_download_after_user_download(
        item_id="Star City",
        import_context=episode_context(1, 4),
        settings_manager=settings,
        context=types.SimpleNamespace(metadata_clients={}),
    ))
    check.ok(changed is False, "active-airing manual episode download must not infer per-show automation")
    check.ok(item.auto_download is False, "the tracked item should remain auto_download=False until the checkbox/config is explicitly enabled")
    check.ok(settings.saved == 0, "manual download should not save an inferred automation setting")


def test_manual_historical_episode_does_not_enable_show_automation(check: Check) -> None:
    """Manual backfills of old/historical seasons must stay opt-in."""
    item = TvShowItem(
        key="Old Show",
        auto_download=False,
        metadata={"status": "Ended"},
    )
    settings = FakeSettingsManager(item)
    category = TvShowCategory()
    changed = run(category.maybe_enable_auto_download_after_user_download(
        item_id="Old Show",
        import_context=episode_context(1, 1),
        settings_manager=settings,
        context=types.SimpleNamespace(metadata_clients={}),
    ))
    check.ok(changed is False, "historical manual downloads must not enable background automation")
    check.ok(item.auto_download is False, "historical manual downloads should leave the item opt-in switch off")
    check.ok(settings.saved == 0, "no settings save should occur for historical downloads")


def test_download_gate_uses_import_context_title_for_per_item_off(check: Check) -> None:
    """Background rows with variant titles should still find the tracked item override."""
    item = TvShowItem(key="For All Mankind", auto_download=False)
    manager = object.__new__(DownloadManager)
    manager._settings_manager = FakeSettingsManager(item)
    row = DownloadItem(
        id="dup1",
        item_name="For All ManKind",
        item_id="for.all.mankind",
        category_id="tv",
        magnet="magnet:?xt=urn:btih:abc",
        import_context=DownloadImportContext(
            category_id="tv",
            item_id="For All ManKind",
            canonical_title="For All Mankind",
            display_title="For All ManKind",
            season=5,
            episode=1,
            unit_descriptor={"stable_key": "S05E01", "coordinates": {"season": 5, "episode": 1}},
        ),
    )
    check.ok(manager._tracked_item_auto_download_override(row) is False, "per-show auto_download=False must be found through normalized import-context title identity")


def test_source_contains_startup_duplicate_brake(check: Check) -> None:
    """Keep the startup guard that prevents old duplicate queues from resuming."""
    source = (ROOT / "src/core/downloader.py").read_text(encoding="utf-8")
    check.ok("_cancel_duplicate_active_media_identity_rows" in source, "download manager should scan duplicate active identities at startup")
    check.ok("cleanup_files=False" in source, "startup duplicate cancellation should not delete payload files")


if __name__ == "__main__":
    check = Check()
    test_release_watch_stale_snapshot_cannot_override_current_off(check)
    test_provider_light_contexts_dedupe_same_episode(check)
    test_provider_light_contexts_dedupe_season_pack_against_episode(check)
    test_manual_active_airing_episode_does_not_enable_show_automation(check)
    test_manual_historical_episode_does_not_enable_show_automation(check)
    test_download_gate_uses_import_context_title_for_per_item_off(check)
    test_source_contains_startup_duplicate_brake(check)
    check.finish()
