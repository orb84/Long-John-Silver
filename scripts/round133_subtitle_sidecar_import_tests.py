#!/usr/bin/env python3
"""Round 133 regression tests for video subtitle sidecar import/rename."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.categories.tv import TvShowCategory
from src.core.categories.movie import MovieCategory
from src.core.categories.video_sidecars import plan_video_sidecar_imports
from src.core.download_handler import DownloadCompletionHandler
from src.core.models import DownloadItem, DownloadPriority, DownloadStatus, Settings


class DummyDownloader:
    async def update_download(self, item):
        pass


class DummyLibrarian:
    pass


class DummyNotifications:
    async def send_download_complete(self, *args, **kwargs):
        pass


def make_item(download_root: Path, *, category_id: str = "tv") -> DownloadItem:
    return DownloadItem(
        id="dl-sidecar",
        item_name="Example Show",
        magnet="magnet:?xt=urn:btih:123",
        status=DownloadStatus.SEEDING,
        priority=DownloadPriority.NORMAL,
        progress=1.0,
        category_id=category_id,
        save_path=str(download_root),
    )


def test_category_plan_preserves_language_and_flags_after_rename() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        source = base / "Release.Name.S01E01.mkv"
        target = base / "library" / "Example Show - S01E01.mkv"
        source.write_text("video", encoding="utf-8")
        (base / "Release.Name.S01E01.en.srt").write_text("en", encoding="utf-8")
        (base / "Release.Name.S01E01.eng.forced.ass").write_text("forced", encoding="utf-8")
        (base / "Release.Name.S01E02.en.srt").write_text("other episode", encoding="utf-8")
        (base / "unrelated.srt").write_text("wrong", encoding="utf-8")

        plans = plan_video_sidecar_imports(source_path=source, imported_path=target)
        planned_targets = sorted(Path(plan["target"]).name for plan in plans)
        assert planned_targets == [
            "Example Show - S01E01.en.srt",
            "Example Show - S01E01.eng.forced.ass",
        ]


def test_tv_category_sidecar_planner_is_category_owned() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        source = base / "Release.Name.S01E01.mkv"
        target = base / "Example Show - S01E01.mkv"
        source.write_text("video", encoding="utf-8")
        (base / "Release.Name.S01E01.it.srt").write_text("ciao", encoding="utf-8")

        category = TvShowCategory()
        plans = category.related_sidecar_imports_for_file(
            source_path=source,
            imported_path=target,
            item=make_item(base),
            settings=Settings(),
        )
        assert len(plans) == 1
        assert Path(plans[0]["target"]).name == "Example Show - S01E01.it.srt"


def test_video_category_scans_language_coded_sidecars() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        video = base / "Example Movie (2020).mkv"
        video.write_text("video", encoding="utf-8")
        (base / "Example Movie (2020).en.srt").write_text("en", encoding="utf-8")
        (base / "Example Movie (2020).ita.forced.ass").write_text("forced", encoding="utf-8")
        (base / "Other Movie.en.srt").write_text("wrong", encoding="utf-8")
        found = sorted(Path(path).name for path in MovieCategory._subtitle_sidecars(str(video)))
        assert found == ["Example Movie (2020).en.srt", "Example Movie (2020).ita.forced.ass"]


async def test_ready_time_copies_and_renames_subtitles_without_mutating_torrent_payload() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        download_root = base / "downloads"
        library_root = base / "library"
        release = download_root / "Release.Name.S01"
        release.mkdir(parents=True)
        source = release / "Release.Name.S01E01.mkv"
        imported = library_root / "TV Shows" / "Example Show" / "Season 01" / "Example Show - S01E01.mkv"
        imported.parent.mkdir(parents=True)
        source.write_text("video", encoding="utf-8")
        imported.write_text("video", encoding="utf-8")
        subtitle = release / "Release.Name.S01E01.en.forced.srt"
        subtitle.write_text("subtitle", encoding="utf-8")

        settings = Settings(download_dir=str(download_root), library_root=str(library_root))
        category = TvShowCategory()
        handler = DownloadCompletionHandler(
            downloader=DummyDownloader(),
            librarian=DummyLibrarian(),
            notifications=DummyNotifications(),
            settings=settings,
            download_dir=download_root,
        )
        sidecars, consumed = await handler._materialize_related_sidecars(
            category=category,
            item=make_item(download_root),
            settings=settings,
            source=source,
            imported=imported,
            file_info=None,
            mode="copy",
        )
        assert consumed == []
        assert subtitle.exists(), "ready-time import must not mutate torrent-owned sidecars"
        assert [p.name for p in sidecars] == ["Example Show - S01E01.en.forced.srt"]
        assert (imported.parent / "Example Show - S01E01.en.forced.srt").read_text(encoding="utf-8") == "subtitle"


async def test_final_import_moves_and_renames_subtitles_so_release_folder_can_disappear() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        download_root = base / "downloads"
        library_root = base / "library"
        release = download_root / "Release.Name.Movie"
        release.mkdir(parents=True)
        source = release / "Release.Name.2020.mkv"
        imported = library_root / "Movies" / "Example Movie (2020)" / "Example Movie (2020).mkv"
        imported.parent.mkdir(parents=True)
        source.write_text("video", encoding="utf-8")
        imported.write_text("video", encoding="utf-8")
        subtitle = release / "Release.Name.2020.eng.sdh.srt"
        subtitle.write_text("subtitle", encoding="utf-8")

        settings = Settings(download_dir=str(download_root), library_root=str(library_root))
        category = MovieCategory()
        handler = DownloadCompletionHandler(
            downloader=DummyDownloader(),
            librarian=DummyLibrarian(),
            notifications=DummyNotifications(),
            settings=settings,
            download_dir=download_root,
        )
        sidecars, consumed = await handler._materialize_related_sidecars(
            category=category,
            item=make_item(download_root, category_id="movie"),
            settings=settings,
            source=source,
            imported=imported,
            file_info=None,
            mode="move",
        )
        assert [p.name for p in sidecars] == ["Example Movie (2020).eng.sdh.srt"]
        assert consumed == [subtitle]
        assert not subtitle.exists(), "final import should remove/move staging sidecar"
        assert (imported.parent / "Example Movie (2020).eng.sdh.srt").exists()
        source.unlink()
        removed = handler._cleanup_empty_download_parents([source, *consumed], item=make_item(download_root, category_id="movie"))
        assert removed == 1
        assert not release.exists(), "release folder should not survive only because subtitle sidecars were left behind"


def main() -> None:
    test_category_plan_preserves_language_and_flags_after_rename()
    test_tv_category_sidecar_planner_is_category_owned()
    test_video_category_scans_language_coded_sidecars()
    asyncio.run(test_ready_time_copies_and_renames_subtitles_without_mutating_torrent_payload())
    asyncio.run(test_final_import_moves_and_renames_subtitles_so_release_folder_can_disappear())
    print("Round 133 subtitle sidecar import tests passed")


if __name__ == "__main__":
    main()
