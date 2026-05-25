"""
Tests for content cleanup.

Verifies episode and movie deletion logic.
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from src.core.content_cleanup import ContentCleanup
from src.core.models import Settings, QualityProfile


class TestContentCleanup:
    """Tests for content deletion operations."""

    @pytest.fixture
    def mock_settings_manager(self, tmp_path):
        """Create a mock SettingsManager with real paths."""
        tv_path = tmp_path / "TV Shows"
        movie_path = tmp_path / "Movies"
        tv_path.mkdir()
        movie_path.mkdir()

        settings = Settings(
            library_root=str(tmp_path),
            library_paths={"tv": str(tv_path), "movie": str(movie_path)},
        )
        manager = MagicMock()
        manager.settings = settings
        return manager

    @pytest.mark.asyncio
    async def test_delete_tv_item_finds_file(self, tmp_path):
        """Deleting an episode should find and remove the correct file."""
        # Create test file structure
        show_dir = tmp_path / "TV Shows" / "Test Show" / "Season 01"
        show_dir.mkdir(parents=True)
        ep_file = show_dir / "Test Show - S01E03.mkv"
        ep_file.write_text("fake video")

        db = AsyncMock()
        notifications = MagicMock()
        notifications.send_message = AsyncMock()

        settings = Settings(
            library_root=str(tmp_path),
            library_paths={"tv": str(tmp_path / "TV Shows")},
        )
        manager = MagicMock()
        manager.settings = settings

        cleanup = ContentCleanup(
            settings_manager=manager, db=db, notifications=notifications,
        )

        result = await cleanup.delete_item("tv", "Test Show", season=1, episode=3)
        assert "Deleted" in result or "S01E03" in result
        assert not ep_file.exists()

    @pytest.mark.asyncio
    async def test_delete_tv_item_not_found(self, tmp_path):
        """Deleting a non-existent episode should report file not found."""
        tv_path = tmp_path / "TV Shows"
        tv_path.mkdir()

        settings = Settings(
            library_root=str(tmp_path),
            library_paths={"tv": str(tv_path)},
        )
        manager = MagicMock()
        manager.settings = settings

        db = AsyncMock()
        notifications = MagicMock()

        cleanup = ContentCleanup(
            settings_manager=manager, db=db, notifications=notifications,
        )

        result = await cleanup.delete_item("tv", "No Show", season=1, episode=1)
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_remove_movie_item_removes_files(self, tmp_path):
        """Deleting a movie should remove video files from its directory."""
        movie_dir = tmp_path / "Movies" / "Test Movie (2024)"
        movie_dir.mkdir(parents=True)
        movie_file = movie_dir / "Test Movie (2024).mkv"
        movie_file.write_text("fake video")

        settings = Settings(
            library_root=str(tmp_path),
            library_paths={"movie": str(tmp_path / "Movies")},
        )
        manager = MagicMock()
        manager.settings = settings

        db = AsyncMock()
        notifications = MagicMock()

        cleanup = ContentCleanup(
            settings_manager=manager, db=db, notifications=notifications,
        )

        result = await cleanup.delete_item("movie", "Test Movie")
        assert "Deleted" in result or "Test Movie" in result
        assert not movie_file.exists()

    @pytest.mark.asyncio
    async def test_auto_cleanup_disabled_by_default(self, tmp_path):
        """Auto cleanup should not run when auto_delete_watched is False."""
        settings = Settings(
            library_root=str(tmp_path),
            library_paths={
                "tv": str(tmp_path / "TV Shows"),
                "movie": str(tmp_path / "Movies"),
            },
            auto_delete_watched=False,
        )
        manager = MagicMock()
        manager.settings = settings

        db = AsyncMock()
        notifications = MagicMock()

        cleanup = ContentCleanup(
            settings_manager=manager, db=db, notifications=notifications,
        )

        result = await cleanup.auto_cleanup_watched()
        assert result == []

    @pytest.mark.asyncio
    async def test_list_library_files(self, tmp_path):
        """Listing library files should discover TV episodes."""
        show_dir = tmp_path / "TV Shows" / "My Show" / "Season 01"
        show_dir.mkdir(parents=True)
        ep_file = show_dir / "My Show - S01E01.mkv"
        ep_file.write_text("fake")

        settings = Settings(
            library_root=str(tmp_path),
            library_paths={"tv": str(tmp_path / "TV Shows")},
        )
        manager = MagicMock()
        manager.settings = settings

        db = AsyncMock()
        notifications = MagicMock()

        cleanup = ContentCleanup(
            settings_manager=manager, db=db, notifications=notifications,
        )

        files = await cleanup.list_library_files(media_type="tv")
        assert len(files) >= 1
        assert any("My Show" in f.get("name", "") for f in files)
