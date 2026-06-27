#!/usr/bin/env python3
"""Round 265 regressions for payload-based movie collections and staged file priorities."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import types
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("aiosqlite", types.SimpleNamespace(Connection=object, Row=dict, Cursor=object))

from src.core.categories.movie import MovieCategory
from src.core.domain_models.downloads import DownloadFileInfo, DownloadImportContext, DownloadItem, SearchResult
from src.core.domain_models.settings import Settings
from src.core.downloader_lifecycle import TorrentRuntimePriorityController


class Check:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def ok(self, condition: bool, message: str) -> None:
        if not condition:
            self.failures.append(message)

    def finish(self) -> None:
        if self.failures:
            print("Round 265 failures:")
            for failure in self.failures:
                print(f" - {failure}")
            raise SystemExit(1)
        print("round265_payload_collection_priority_staging_tests: OK")


class FakeTorrentInfo:
    def __init__(self, count: int) -> None:
        self._count = count

    def num_files(self) -> int:
        return self._count


class FakeHandle:
    def __init__(self, count: int) -> None:
        self._info = FakeTorrentInfo(count)
        self.applied: list[list[int]] = []

    def has_metadata(self) -> bool:
        return True

    def torrent_file(self) -> FakeTorrentInfo:
        return self._info

    def prioritize_files(self, priorities: list[int]) -> None:
        self.applied.append(list(priorities))


def test_movie_collection_detection_uses_payload_not_title(check: Check) -> None:
    category = MovieCategory()
    title_only = SearchResult(title="Broad release title with no cached file evidence", magnet="magnet:?xt=urn:btih:titleonly")
    with_files = types.SimpleNamespace(
        title="Ordinary release title",
        files=[
            {"path": "release-root/First Film (1971).mkv"},
            {"path": "release-root/Second Film (1974).avi"},
            {"path": "release-root/img/Second Film (1974) cover.jpg"},
        ],
    )
    check.ok(category.torrent_bundle_candidate_context(title_only) is None, "movie collection detection must not infer bundle status from the title alone")
    context = category.torrent_bundle_candidate_context(with_files)
    check.ok(bool(context and context.get("bundle_type") == "movie_collection"), "movie collection detection should use multiple primary movie files as evidence")
    check.ok(context.get("unit_count") == 2, "collection unit count should come from distinct movie payload files")


def test_download_collection_import_uses_payload_structure_without_bundle_hint(check: Check) -> None:
    category = MovieCategory()
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(library_root=str(Path(tmp) / "library"))
        item = DownloadItem(
            id="d1",
            item_name="broad user query",
            category_id="movie",
            magnet="magnet:?xt=urn:btih:payload",
            torrent_title="Ordinary Release Root",
            import_context=DownloadImportContext(
                category_id="movie",
                item_id="broad user query",
                release_title="Ordinary Release Root",
                candidate_snapshot={"title": "Ordinary Release Root"},
            ),
            files=[
                DownloadFileInfo(file_index=0, file_path="Ordinary Release Root/First Film (1971).mkv", status="complete"),
                DownloadFileInfo(file_index=1, file_path="Ordinary Release Root/Second Film (1974).avi", status="complete"),
            ],
        )
        file_info = item.files[1]
        target = category.download_target_for_item(
            Path("/downloads/Ordinary Release Root/Second Film (1974).avi"),
            item,
            settings,
            source_name=file_info.file_path,
            file_info=file_info,
            metadata={"title": "broad user query", "year": None},
        )
        check.ok("broad user query" not in str(target), "collection import must not rename every child movie to the broad user query")
        check.ok(target.name == "Second Film (1974).avi", "collection import should preserve the child movie filename")
        check.ok(target.parent.name == "Ordinary Release Root", "collection import should preserve the release root folder")


def test_distinct_priorities_are_staged_not_parallel(check: Check) -> None:
    item = DownloadItem(id="dl1", item_name="Collection", category_id="movie", magnet="magnet:?xt=urn:btih:p")
    item.files = [
        DownloadFileInfo(file_index=0, file_path="A.mkv", size=100, downloaded_bytes=0, priority=7, status="pending"),
        DownloadFileInfo(file_index=1, file_path="B.mkv", size=100, downloaded_bytes=0, priority=6, status="pending"),
        DownloadFileInfo(file_index=2, file_path="C.mkv", size=100, downloaded_bytes=0, priority=5, status="pending"),
    ]
    handle = FakeHandle(3)
    controller = TorrentRuntimePriorityController()
    controller.apply("dl1", handle, item)
    check.ok(handle.applied[-1] == [7, 0, 0], "distinct file priorities should enable only the highest unfinished band")
    item.files[0].downloaded_bytes = 100
    item.files[0].status = "complete"
    controller.apply("dl1", handle, item)
    check.ok(handle.applied[-1] == [0, 6, 0], "staged priorities should advance after the earlier file completes")


def test_equal_priorities_remain_parallel(check: Check) -> None:
    item = DownloadItem(id="dl2", item_name="Parallel", category_id="movie", magnet="magnet:?xt=urn:btih:q")
    item.files = [
        DownloadFileInfo(file_index=0, file_path="A.mkv", size=100, priority=4, status="pending"),
        DownloadFileInfo(file_index=1, file_path="B.mkv", size=100, priority=4, status="pending"),
    ]
    handle = FakeHandle(2)
    TorrentRuntimePriorityController().apply("dl2", handle, item)
    check.ok(handle.applied[-1] == [4, 4], "equal positive priorities should remain parallel")


def test_movie_category_has_no_literal_pack_marketing_detector(check: Check) -> None:
    source = (ROOT / "src/core/categories/movie.py").read_text().casefold()
    check.ok("megapack" not in source and "mega\\s*pack" not in source and "movie\\s*pack" not in source, "movie category must not contain literal collection marketing-word detectors")


if __name__ == "__main__":
    check = Check()
    test_movie_collection_detection_uses_payload_not_title(check)
    test_download_collection_import_uses_payload_structure_without_bundle_hint(check)
    test_distinct_priorities_are_staged_not_parallel(check)
    test_equal_priorities_remain_parallel(check)
    test_movie_category_has_no_literal_pack_marketing_detector(check)
    check.finish()
