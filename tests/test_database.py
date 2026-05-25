"""Tests for the database layer."""

import pytest
import pytest_asyncio
import tempfile
from pathlib import Path
from src.core.database import Database
from src.core.models import DownloadItem, DownloadStatus, BlacklistEntry


@pytest_asyncio.fixture
async def db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    database = Database(db_path=db_path)
    await database.initialize()
    yield database
    await database.close()
    Path(db_path).unlink()


@pytest.mark.asyncio
async def test_download_crud(db):
    """Downloads should be creatable and retrievable."""
    from datetime import datetime
    from src.core.models import DownloadFileInfo
    item = DownloadItem(
        id="test123",
        item_name="Test Show",
        magnet="magnet:?xt=urn:test",
        status=DownloadStatus.QUEUED,
        created_at=datetime.now(),
        files=[
            DownloadFileInfo(
                file_index=0,
                file_path="Season 1/Test.S01E01.mkv",
                size=1024 * 1024 * 500,
                priority=4,
                season=1,
                episode=1,
                status="pending"
            )
        ]
    )
    await db.downloads.upsert_download(item)

    retrieved = await db.downloads.get_download("test123")
    assert retrieved is not None
    assert retrieved.item_name == "Test Show"
    assert retrieved.status == DownloadStatus.QUEUED
    assert len(retrieved.files) == 1
    assert retrieved.files[0].file_path == "Season 1/Test.S01E01.mkv"
    assert retrieved.files[0].season == 1
    assert retrieved.files[0].episode == 1


@pytest.mark.asyncio
async def test_blacklist_crud(db):
    """Blacklist entries should be storable and retrievable."""
    entry = BlacklistEntry(pattern=".*CAM.*", reason="Low quality")
    await db.downloads.add_blacklist_entry(entry)

    entries = await db.downloads.get_blacklist()
    assert len(entries) == 1
    assert entries[0].pattern == ".*CAM.*"

    await db.downloads.remove_blacklist_entry(".*CAM.*")
    entries = await db.downloads.get_blacklist()
    assert len(entries) == 0


@pytest.mark.asyncio
async def test_preferences(db):
    """Preferences should be storable and retrievable."""
    await db.system.set_preference("theme", "dark")
    value = await db.system.get_preference("theme")
    assert value == "dark"

    all_prefs = await db.system.get_all_preferences()
    assert "theme" in all_prefs


@pytest.mark.asyncio
async def test_category_item_progress(db):
    """Category item progress should be updatable and retrievable."""
    await db.media.update_item_progress("tv", "Breaking Bad", 5, 14)
    progress = await db.media.get_item_progress("tv", "Breaking Bad")
    assert progress is not None
    assert progress["last_season"] == 5
    assert progress["last_episode"] == 14