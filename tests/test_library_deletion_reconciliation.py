"""Regression tests for library filesystem deletion reconciliation."""

from pathlib import Path

import pytest

from src.core.models import LibraryScanResult, ScannedLibraryItem


@pytest.mark.asyncio
async def test_repository_delete_category_item_removes_owned_rows(db) -> None:
    """Deleting an item should not leave orphan units/metadata/index rows behind."""
    await db.media.upsert_category_item(
        "tv",
        "Stale Show",
        {
            "category_id": "tv",
            "item_id": "Stale Show",
            "key": "Stale Show",
            "display_name": "Stale Show",
            "properties": {"tmdb_id": 123},
        },
    )
    await db.media.record_unit_downloaded("tv", "Stale Show", "S01E01", {"unit_type": "episode", "season": 1, "episode": 1})
    await db.media.update_item_progress("tv", "Stale Show", {"last_season": 1, "last_episode": 1})
    await db.media.upsert_category_metadata("tv", "Stale Show", "tmdb", {"title": "Stale Show"}, "123")

    await db.media.delete_category_item("tv", "Stale Show")

    assert await db.media.get_category_item("tv", "Stale Show") is None
    assert await db.media.list_category_units("tv", "Stale Show") == []
    assert await db.media.get_category_metadata("tv", "Stale Show") == []


@pytest.mark.asyncio
async def test_reconcile_removed_library_entries_clears_tracked_downloaded_units(db, settings) -> None:
    """A tracked item whose folder vanished should stay tracked but lose stale local progress."""
    from src.core.scheduler import MediaScheduler

    await db.media.upsert_category_item("tv", "Test Show", {"category_id": "tv", "item_id": "Test Show", "key": "Test Show"})
    await db.media.record_unit_downloaded("tv", "Test Show", "S01E01", {"unit_type": "episode", "season": 1, "episode": 1})
    await db.media.update_item_progress("tv", "Test Show", {"last_season": 1, "last_episode": 1})

    scheduler = object.__new__(MediaScheduler)
    scheduler._db = db
    scheduler._settings_manager = type("SM", (), {"settings": settings, "save": lambda self, value: None})()
    scheduler._event_bus = None
    scheduler._emit_status = lambda *args, **kwargs: None

    result = await scheduler._reconcile_removed_library_entries(LibraryScanResult(items=[]))

    item = await db.media.get_category_item("tv", "Test Show")
    assert item is not None
    assert item["status"] == "missing_from_library"
    assert item["state"]["library_present"] is False
    assert result["cleared_units"] == 2
    assert await db.media.list_category_units("tv", "Test Show") == []


@pytest.mark.asyncio
async def test_reconcile_removed_library_entries_removes_discovered_items(db, settings) -> None:
    """An auto-discovered item removed from disk should disappear from the catalog."""
    from src.core.scheduler import MediaScheduler

    await db.media.upsert_category_item(
        "tv",
        "Discovered Show",
        {"category_id": "tv", "item_id": "Discovered Show", "key": "Discovered Show", "discovered": True, "status": "discovered"},
    )
    await db.media.record_unit_downloaded("tv", "Discovered Show", "S01E01", {"unit_type": "episode", "season": 1, "episode": 1})

    scheduler = object.__new__(MediaScheduler)
    scheduler._db = db
    scheduler._settings_manager = type("SM", (), {"settings": settings, "save": lambda self, value: None})()
    scheduler._event_bus = None
    scheduler._emit_status = lambda *args, **kwargs: None

    result = await scheduler._reconcile_removed_library_entries(LibraryScanResult(items=[]))

    assert result["removed_items"] == 1
    assert await db.media.get_category_item("tv", "Discovered Show") is None


def test_scheduler_reconciles_removed_items_after_unit_sync() -> None:
    """Static guard for scan order: insert/update, sync units, then prune absent items."""
    text = Path("src/core/scheduler.py").read_text()
    assert "await self._reconcile_removed_library_entries(result)" in text
    assert text.index("await self._sync_scanned_units(result)") < text.index("await self._reconcile_removed_library_entries(result)")
    assert "self._scheduler.add_job(\n            self._watch_library_filesystem_job" in text


def test_frontend_handles_stale_detail_cards_without_uncaught_promise() -> None:
    """The detail modal should absorb stale-card 404s and refresh the catalog."""
    modal = Path("src/web/static/js/components/categoryItemDetailModal.js").read_text()
    panel = Path("src/web/static/js/components/bootyPanel.js").read_text()
    client = Path("src/web/static/js/api/actionClient.js").read_text()

    assert "err.status === 404" in modal
    assert "window.bootyPanel.loadCatalog()" in modal
    assert "library_reconciled" in panel
    assert "error.status = response.status" in client
