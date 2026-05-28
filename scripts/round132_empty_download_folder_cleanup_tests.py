#!/usr/bin/env python3
"""Round 132 regression tests for empty season-pack download folder cleanup."""

from __future__ import annotations

import asyncio
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.download_handler import DownloadCompletionHandler
from src.core.models import DownloadFileInfo, DownloadImportContext, DownloadItem, DownloadPriority, DownloadStatus, Settings


class DummyDownloader:
    """Minimal downloader facade for completion-handler tests."""

    def __init__(self, item: DownloadItem) -> None:
        self.item = item
        self.updated = 0

    async def update_download(self, item: DownloadItem) -> None:
        self.item = item
        self.updated += 1

    async def get_download(self, _download_id: str) -> DownloadItem:
        return self.item


class DummyNotifications:
    def __init__(self) -> None:
        self.sent = []

    async def send_download_complete(self, *args, **kwargs) -> None:
        self.sent.append((args, kwargs))


class DummyLibrarian:
    """Organizes files by moving them into a library folder."""

    def __init__(self, library_root: Path) -> None:
        self.library_root = library_root

    def organize_file(self, *, source: Path, item_name: str = "", season=None, episode=None, episode_title=None, year=None, category_id: str = "") -> str:
        target_dir = self.library_root / (category_id or "media") / item_name / (f"Season {int(season):02d}" if season else "")
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / source.name
        shutil.move(str(source), str(target))
        return str(target)


def make_item(download_root: Path, release_folder: str = "Show.S01.Pack") -> DownloadItem:
    return DownloadItem(
        id="dl-season-pack",
        item_name="Example Show",
        magnet="magnet:?xt=urn:btih:123",
        status=DownloadStatus.COMPLETE,
        priority=DownloadPriority.NORMAL,
        progress=1.0,
        category_id="tv",
        save_path=str(download_root),
        import_context=DownloadImportContext(category_id="tv", canonical_title="Example Show", display_title="Example Show"),
        files=[
            DownloadFileInfo(file_index=0, file_path=f"{release_folder}/Example.Show.S01E01.mkv", size=10, status="complete", season=1, episode=1),
            DownloadFileInfo(file_index=1, file_path=f"{release_folder}/Example.Show.S01E02.mkv", size=10, status="complete", season=1, episode=2),
        ],
    )


async def test_final_move_removes_empty_release_folder() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        download_root = base / "downloads"
        library_root = base / "library"
        release = download_root / "Show.S01.Pack"
        release.mkdir(parents=True)
        for name in ("Example.Show.S01E01.mkv", "Example.Show.S01E02.mkv"):
            (release / name).write_text("episode", encoding="utf-8")

        item = make_item(download_root)
        downloader = DummyDownloader(item)
        handler = DownloadCompletionHandler(
            downloader=downloader,
            librarian=DummyLibrarian(library_root),
            notifications=DummyNotifications(),
            settings=Settings(download_dir=str(download_root), library_root=str(library_root)),
            download_dir=download_root,
        )

        changed = await handler._organize_missing_staging_payloads(item)
        assert changed is True
        assert downloader.updated == 1
        assert not release.exists(), "empty season-pack release folder should be removed after moving all files"
        assert download_root.exists(), "download root itself must never be removed"
        assert len(list((library_root / "tv" / "Example Show" / "Season 01").glob("*.mkv"))) == 2


def test_non_empty_release_folder_is_preserved() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        download_root = base / "downloads"
        release = download_root / "Show.S01.Pack"
        release.mkdir(parents=True)
        source = release / "Example.Show.S01E01.mkv"
        source.write_text("episode", encoding="utf-8")
        (release / "readme.nfo").write_text("keep me", encoding="utf-8")

        item = make_item(download_root)
        handler = DownloadCompletionHandler(
            downloader=DummyDownloader(item),
            librarian=DummyLibrarian(base / "library"),
            notifications=DummyNotifications(),
            settings=Settings(download_dir=str(download_root), library_root=str(base / "library")),
            download_dir=download_root,
        )
        source.unlink()
        removed = handler._cleanup_empty_download_parents([source], item=item)
        assert removed == 0
        assert release.exists(), "folders with sidecars/user files must be preserved"


def test_cleanup_refuses_outside_paths_and_root() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        download_root = base / "downloads"
        download_root.mkdir()
        outside = base / "outside" / "file.mkv"
        outside.parent.mkdir()
        outside.write_text("x", encoding="utf-8")
        item = make_item(download_root)
        handler = DownloadCompletionHandler(
            downloader=DummyDownloader(item),
            librarian=DummyLibrarian(base / "library"),
            notifications=DummyNotifications(),
            settings=Settings(download_dir=str(download_root), library_root=str(base / "library")),
            download_dir=download_root,
        )
        outside.unlink()
        removed = handler._cleanup_empty_download_parents([outside, download_root / "fake.mkv"], item=item)
        assert removed == 0
        assert download_root.exists()
        assert outside.parent.exists(), "outside folders must never be cleaned by download cleanup"


def main() -> None:
    asyncio.run(test_final_move_removes_empty_release_folder())
    test_non_empty_release_folder_is_preserved()
    test_cleanup_refuses_outside_paths_and_root()
    print("Round 132 empty download folder cleanup tests passed")


if __name__ == "__main__":
    main()
