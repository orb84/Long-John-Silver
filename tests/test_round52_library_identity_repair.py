"""Round 52 regressions: clean library identity and collection folders."""

from pathlib import Path

import pytest

from src.core.categories.movie import MovieCategory
from src.core.models import LibraryScanResult, MovieItem, ScannedLibraryItem, Settings, ItemList


async def _async_value(value):
    return value


@pytest.mark.asyncio
async def test_movie_collection_folder_is_flattened_not_cataloged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    movie_root = tmp_path / "Movies"
    collection = movie_root / "Sonic Trilogia"
    first = collection / "Sonic.The.Hedgehog.2020.1080p.ITA.ENG"
    second = collection / "Sonic.The.Hedgehog.2.2022.1080p.ITA.ENG"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "Sonic.The.Hedgehog.2020.1080p.mkv").write_text("dummy")
    (second / "Sonic.The.Hedgehog.2.2022.1080p.mkv").write_text("dummy")

    category = MovieCategory()
    monkeypatch.setattr(category, "detect_language", lambda *args, **kwargs: _async_value("Italian"))

    scanned = await category.scan(str(movie_root))
    names = {item.name for item in scanned}

    assert "Sonic Trilogia" not in names
    assert names == {"Sonic The Hedgehog", "Sonic The Hedgehog 2"}


@pytest.mark.asyncio
async def test_movie_scan_skips_tv_shaped_folder_in_movie_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    movie_root = tmp_path / "Movies"
    show_dir = movie_root / "Dexter.Original.Sin.S01.1080p.ITA-ENG.WEBRip.x265"
    show_dir.mkdir(parents=True)
    (show_dir / "Dexter.Original.Sin.S01E01.1080p.mkv").write_text("dummy")
    (show_dir / "Dexter.Original.Sin.S01E02.1080p.mkv").write_text("dummy")

    category = MovieCategory()
    monkeypatch.setattr(category, "detect_language", lambda *args, **kwargs: _async_value("Italian"))

    scanned = await category.scan(str(movie_root))

    assert scanned == []


@pytest.mark.asyncio
async def test_reconcile_merges_dirty_alias_when_clean_scan_exists(db):
    from src.core.scheduler import MediaScheduler

    dirty = "Sinners.2025.ITA.ENG.1080p.WEBRip.x265-GROUP"
    clean = "Sinners"
    await db.media.upsert_category_item(
        "movie",
        clean,
        {"category_id": "movie", "item_id": clean, "key": clean, "display_name": clean, "discovered": True, "status": "discovered"},
    )
    await db.media.upsert_category_item(
        "movie",
        dirty,
        {"category_id": "movie", "item_id": dirty, "key": dirty, "display_name": dirty, "discovered": True, "status": "discovered"},
    )
    await db.media.record_unit_downloaded("movie", dirty, "file", {"unit_type": "file", "status": "downloaded"})

    settings = Settings(tracked_items=ItemList(items=[MovieItem(key=dirty, discovered=True)]))
    saved = {"called": False}

    scheduler = object.__new__(MediaScheduler)
    scheduler._db = db
    scheduler._settings_manager = type("SM", (), {"settings": settings, "save": lambda self, value: saved.__setitem__("called", True)})()
    scheduler._event_bus = None
    scheduler._emit_status = lambda *args, **kwargs: None

    result = await scheduler._reconcile_removed_library_entries(
        LibraryScanResult(items=[ScannedLibraryItem(name=clean, category_id="movie", file_count=1)])
    )

    assert result["removed_items"] == 1
    assert await db.media.get_category_item("movie", dirty) is None
    assert await db.media.get_category_item("movie", clean) is not None
    assert saved["called"] is True
    assert all(item.key != dirty for item in settings.tracked_items.items)


@pytest.mark.asyncio
async def test_reconcile_removes_absent_discovered_item_even_without_units(db):
    from src.core.scheduler import MediaScheduler

    await db.media.upsert_category_item(
        "movie",
        "Sonic Trilogia",
        {"category_id": "movie", "item_id": "Sonic Trilogia", "key": "Sonic Trilogia", "discovered": True, "status": "discovered"},
    )
    settings = Settings(tracked_items=ItemList(items=[MovieItem(key="Sonic Trilogia", discovered=True)]))

    scheduler = object.__new__(MediaScheduler)
    scheduler._db = db
    scheduler._settings_manager = type("SM", (), {"settings": settings, "save": lambda self, value: None})()
    scheduler._event_bus = None
    scheduler._emit_status = lambda *args, **kwargs: None

    result = await scheduler._reconcile_removed_library_entries(LibraryScanResult(items=[]))

    assert result["removed_items"] == 1
    assert await db.media.get_category_item("movie", "Sonic Trilogia") is None
    assert settings.tracked_items.items == []
