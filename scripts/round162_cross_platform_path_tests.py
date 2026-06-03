#!/usr/bin/env python3
"""Round 162 cross-platform path normalization and archive extraction tests."""

from __future__ import annotations

from pathlib import Path
import sys
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.categories.identity import basename_from_pathish, clean_display_title, clean_path_fragment, clean_path_segment
from src.core.categories.path_planner import CategoryPathPlanner
from src.core.download_handler import DownloadCompletionHandler
from src.core.models import DownloadItem, Settings
from src.core.categories.movie import MovieCategory
from src.utils.archive_safety import UnsafeArchivePath, safe_extract_tar, safe_extract_zip


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_pathish_basename_handles_posix_and_windows_remote_paths() -> None:
    raw = r"music\Albums\P\Persiana Jones - 1999 - Puerto Hurraco\03, Spacco Tutto.mp3"
    require(basename_from_pathish(raw) == "03, Spacco Tutto.mp3", "Windows-style remote path should reduce to basename")
    require(
        basename_from_pathish("Music/Artist/Album/04: Bad?Name.mp3") == "04 Bad Name.mp3",
        "POSIX remote basename should be made portable for Windows too",
    )
    require(DownloadCompletionHandler._clean_source_name(raw) == "03, Spacco Tutto.mp3", "completion handler should use cross-platform basename helper")


def test_path_fragments_are_portable_but_preserve_intended_template_hierarchy() -> None:
    require(clean_display_title("Movie Title (2024)") == "Movie Title (2024)", "balanced parentheses should survive display cleaning")
    require(clean_path_segment("CON.mp3") == "_CON.mp3", "Windows device names should be made safe")
    fragment = clean_path_fragment(r"Artist: Bad/Album?*Name\Disc <1>|Final")
    require(fragment == "Artist Bad/Album Name/Disc 1 Final", f"unexpected fragment: {fragment!r}")


def test_category_path_planner_does_not_leak_backslashes_or_windows_invalid_chars() -> None:
    planner = CategoryPathPlanner()
    target = planner.compute_target_path_from_fields(
        source_name=r"music\Albums\A\01: Intro?.flac",
        template="{title}/{filename_stem}",
        library_root="/library/Music",
        fields={"title": r"Artist: Bad\Album?"},
    )
    text = target.as_posix()
    require("\\" not in text, f"target leaked backslash: {target}")
    require("?" not in text and ":" not in text, f"target leaked Windows-invalid chars: {target}")
    require(text.endswith("/Artist Bad/Album/01 Intro.flac"), f"unexpected target: {target}")


def test_movie_ready_time_target_is_portable() -> None:
    category = MovieCategory()
    settings = Settings(library_root="/library")
    item = DownloadItem(id="test", item_name="Movie: Title?", magnet="magnet:?xt=urn:btih:test", category_id="movie")
    target = category.download_target_for_item(
        Path("/downloads/source.mkv"),
        item,
        settings,
        source_name=r"releases\Movie.Title.2024\Movie: Title?.mkv",
        metadata={"title": "Movie: Title?", "year": 2024},
    )
    text = target.as_posix()
    require("?" not in text and ":" not in text and "\\" not in text, f"movie target not portable: {target}")
    require("Movie Title (2024)" in text, f"movie year folder should remain readable: {target}")


def test_safe_zip_extraction_normalizes_separators_and_blocks_traversal() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        archive = root / "sample.zip"
        out = root / "out"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr(r"folder\nested\file.txt", "ok")
        safe_extract_zip(archive, out)
        require((out / "folder" / "nested" / "file.txt").read_text() == "ok", "zip backslash members should extract as directories")

        evil = root / "evil.zip"
        with zipfile.ZipFile(evil, "w") as zf:
            zf.writestr(r"..\evil.txt", "bad")
        try:
            safe_extract_zip(evil, out)
        except UnsafeArchivePath:
            pass
        else:
            raise AssertionError("zip traversal was not blocked")


def test_safe_tar_extraction_blocks_traversal() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        archive = root / "evil.tar.gz"
        payload = root / "payload.txt"
        payload.write_text("bad")
        with tarfile.open(archive, "w:gz") as tf:
            tf.add(payload, arcname="../evil.txt")
        try:
            safe_extract_tar(archive, root / "out")
        except UnsafeArchivePath:
            pass
        else:
            raise AssertionError("tar traversal was not blocked")


def main() -> None:
    test_pathish_basename_handles_posix_and_windows_remote_paths()
    test_path_fragments_are_portable_but_preserve_intended_template_hierarchy()
    test_category_path_planner_does_not_leak_backslashes_or_windows_invalid_chars()
    test_movie_ready_time_target_is_portable()
    test_safe_zip_extraction_normalizes_separators_and_blocks_traversal()
    test_safe_tar_extraction_blocks_traversal()
    print("Round 162 cross-platform path tests passed")


if __name__ == "__main__":
    main()
