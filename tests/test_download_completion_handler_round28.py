"""Round 28 regression tests for completion-to-library organization."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core.download_handler import DownloadCompletionHandler
from src.core.models import DownloadFileInfo, DownloadItem, DownloadStatus, Settings


class FakeDownloader:
    """Small in-memory downloader facade used by completion handler tests."""

    def __init__(self, item):
        self.item = item
        self.updated = []

    async def get_download(self, download_id):
        return self.item if self.item.id == download_id else None

    async def get_recent_downloads(self, limit=200):
        return [self.item]

    async def update_download(self, item):
        self.item = item
        self.updated.append(item)


class FakeNotifications:
    """Records completion notifications without external services."""

    def __init__(self):
        self.messages = []

    async def send_download_complete(self, item_name, season, episode):
        self.messages.append((item_name, season, episode))


class SettingsAwareCategory:
    """Category whose path planner fails if settings are not injected."""

    category_id = "tv"

    def __init__(self, root: Path):
        self.root = root

    def get_root_path(self, settings):
        # Reproduces the old failure shape if settings is None.
        settings.category_settings
        return str(self.root)

    def compute_target_path(self, source_name, item_name, season, episode, **kwargs):
        settings = kwargs.get("settings")
        settings.category_settings
        return Path(kwargs["library_root"]) / item_name / f"Season {season:02d}" / source_name


class FakeCategoryRegistry:
    """Minimal category registry for handler tests."""

    def __init__(self, category):
        self.category = category

    def get(self, category_id):
        return self.category if category_id == self.category.category_id else None


class MovingLibrarian:
    """Librarian fake that moves sources into a test library root."""

    def __init__(self, root: Path):
        self.root = root
        self.moves = []

    def organize_file(self, source, item_name="", season=None, episode=None, **kwargs):
        dest_dir = self.root / item_name / f"Season {int(season or 1):02d}"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / source.name
        source.rename(dest)
        self.moves.append((source, dest))
        return dest


@pytest.mark.asyncio
async def test_ready_callback_passes_live_settings_to_category_path_planner(tmp_path):
    """A completed torrent should be exposed to the library without settings=None crashes."""
    downloads = tmp_path / "downloads"
    library = tmp_path / "library"
    downloads.mkdir()
    source = downloads / "Show.S01E01.mkv"
    source.write_bytes(b"episode")
    item = DownloadItem(
        id="dl1",
        item_name="Show",
        magnet="magnet:?xt=urn:btih:test",
        status=DownloadStatus.SEEDING,
        progress=1.0,
        category_id="tv",
        save_path=str(downloads),
        files=[DownloadFileInfo(file_index=0, file_path="Show.S01E01.mkv", size=7, status="complete", season=1, episode=1)],
    )
    downloader = FakeDownloader(item)
    handler = DownloadCompletionHandler(
        downloader=downloader,
        librarian=MovingLibrarian(library),
        notifications=FakeNotifications(),
        category_registry=FakeCategoryRegistry(SettingsAwareCategory(library)),
        settings=None,
        settings_manager=SimpleNamespace(settings=Settings()),
        download_dir=downloads,
    )

    await handler.on_download_ready("dl1")

    organized = downloader.item.files[0].organized_path
    assert organized is not None
    assert Path(organized).exists()
    assert Path(organized).read_bytes() == b"episode"


@pytest.mark.asyncio
async def test_completion_fallback_moves_all_unorganized_multifile_payloads(tmp_path):
    """If ready-time import was missed, completion should not move only the largest file."""
    downloads = tmp_path / "downloads"
    library = tmp_path / "library"
    downloads.mkdir()
    (downloads / "Show.S01E01.mkv").write_bytes(b"ep1")
    (downloads / "Show.S01E02.mkv").write_bytes(b"ep2")
    item = DownloadItem(
        id="dl2",
        item_name="Show",
        magnet="magnet:?xt=urn:btih:test2",
        status=DownloadStatus.COMPLETE,
        progress=1.0,
        category_id="tv",
        save_path=str(downloads),
        files=[
            DownloadFileInfo(file_index=0, file_path="Show.S01E01.mkv", size=3, status="complete", season=1, episode=1),
            DownloadFileInfo(file_index=1, file_path="Show.S01E02.mkv", size=3, status="complete", season=1, episode=2),
        ],
    )
    librarian = MovingLibrarian(library)
    handler = DownloadCompletionHandler(
        downloader=FakeDownloader(item),
        librarian=librarian,
        notifications=FakeNotifications(),
        category_registry=FakeCategoryRegistry(SettingsAwareCategory(library)),
        settings=Settings(),
        download_dir=downloads,
    )

    repaired = await handler.reconcile_completed_imports()

    assert repaired == 1
    assert len(librarian.moves) == 2
    assert (library / "Show" / "Season 01" / "Show.S01E01.mkv").exists()
    assert (library / "Show" / "Season 01" / "Show.S01E02.mkv").exists()
