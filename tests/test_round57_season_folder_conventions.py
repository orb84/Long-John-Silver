"""Round 57 regressions for preserving existing TV season-folder conventions."""

from pathlib import Path
from types import SimpleNamespace

from src.core.categories.season_folders import SeasonFolderLayout
from src.core.categories.tv import TvShowCategory
from src.core.downloader_sharing_mixin import DownloadSharingMixin
from src.core.models import DownloadItem, Settings


class _Registry:
    def __init__(self, category):
        self.category = category

    def get(self, category_id):
        return self.category if category_id == self.category.category_id else None


class _SharingResolver(DownloadSharingMixin):
    def __init__(self, settings, category, download_dir: Path):
        self._settings_manager = SimpleNamespace(settings=settings)
        self._deps = SimpleNamespace(category_registry=_Registry(category))
        self._download_dir = download_dir


def _settings(root: Path) -> Settings:
    return Settings(
        category_settings={
            "tv": {
                "library_path": str(root),
                "naming_template": "{series_title}/Season {season:02d}/{series_title} - S{season:02d}E{episode:02d}",
            }
        },
        library_paths={"tv": str(root)},
    )


def test_tv_path_planner_reuses_existing_unpadded_season_folder(tmp_path):
    """A configured padded template must not split an existing ``Season 5`` folder."""
    root = tmp_path / "TV Shows"
    existing = root / "For All Mankind" / "Season 5"
    existing.mkdir(parents=True)

    category = TvShowCategory()
    target = category.compute_target_path(
        source_name="For.All.Mankind.S05E04.mkv",
        item_name="For All Mankind",
        season=5,
        episode=4,
        settings=_settings(root),
        library_root=str(root),
    )

    assert target.parent == existing
    assert "Season 05" not in str(target)


def test_tv_path_planner_keeps_existing_padded_convention(tmp_path):
    """Libraries that already use ``Season 01`` keep that convention."""
    root = tmp_path / "TV Shows"
    existing = root / "Show" / "Season 01"
    existing.mkdir(parents=True)

    category = TvShowCategory()
    target = category.compute_target_path(
        source_name="Show.S01E02.mkv",
        item_name="Show",
        season=1,
        episode=2,
        settings=_settings(root),
        library_root=str(root),
    )

    assert target.parent == existing


def test_duplicate_season_folders_are_merged_to_existing_style(tmp_path):
    """Scan repair should merge ``Season 05`` into ``Season 5`` without overwriting."""
    show_dir = tmp_path / "TV Shows" / "For All Mankind"
    unpadded = show_dir / "Season 5"
    padded = show_dir / "Season 05"
    unpadded.mkdir(parents=True)
    padded.mkdir(parents=True)
    (unpadded / "For.All.Mankind.S05E01.mkv").write_bytes(b"old")
    (padded / "For.All.Mankind.S05E04.mkv").write_bytes(b"new")

    moved = SeasonFolderLayout.repair_duplicate_season_folders(show_dir)

    assert moved == 1
    assert (unpadded / "For.All.Mankind.S05E01.mkv").exists()
    assert (unpadded / "For.All.Mankind.S05E04.mkv").exists()
    assert not padded.exists()


def test_seed_in_place_save_path_reuses_existing_season_folder(tmp_path):
    """Seed-in-place downloads must also join the existing season folder."""
    root = tmp_path / "TV Shows"
    existing = root / "For All Mankind" / "Season 5"
    existing.mkdir(parents=True)
    settings = _settings(root)
    settings.sharing.enabled = True
    settings.sharing.mode = "seed_in_place"

    category = TvShowCategory()
    resolver = _SharingResolver(settings, category, tmp_path / "downloads")
    save_path, sharing_enabled = resolver._resolve_torrent_save_path(
        DownloadItem(
            id="dl-s5e4",
            item_name="For All Mankind",
            magnet="magnet:?xt=urn:btih:test",
            category_id="tv",
            season=5,
            episode=4,
        )
    )

    assert sharing_enabled is True
    assert save_path == existing.resolve()
