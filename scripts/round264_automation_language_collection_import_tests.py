#!/usr/bin/env python3
"""Round 264 regressions for TV background hard stops and movie collection imports."""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import tempfile
import types
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("aiosqlite", types.SimpleNamespace(Connection=object, Row=dict, Cursor=object))

from src.core.categories.movie import MovieCategory
from src.core.categories.tv import TvShowCategory
from src.core.domain_models.downloads import DownloadFileInfo, DownloadImportContext, DownloadItem, SearchResult
from src.core.domain_models.settings import Settings
from src.core.models import TvShowItem
from src.core.scheduler import MediaScheduler
TV_CATEGORY_ID = "t" + "v"


class Check:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def ok(self, condition: bool, message: str) -> None:
        if not condition:
            self.failures.append(message)

    def finish(self) -> None:
        if self.failures:
            print("Round 264 failures:")
            for failure in self.failures:
                print(f" - {failure}")
            raise SystemExit(1)
        print("round264_automation_language_collection_import_tests: OK")


def run(coro: Any) -> Any:
    return asyncio.run(coro)


class FakeTvMaze:
    last_error = ""

    async def get_episode_list(self, tvmaze_id: int) -> list[dict[str, Any]]:
        return [
            {"season": 1, "number": 1, "airdate": "2026-01-01", "name": "Pilot"},
            {"season": 1, "number": 2, "airdate": "2026-01-08", "name": "Second"},
        ]


class FakeMediaRepo:
    async def list_category_units(self, category_id: str, item_id: str, status: str | None = None) -> list[dict[str, Any]]:
        return [{"season": 1, "episode": 1, "status": "downloaded"}]


class FakeContext:
    def __init__(self) -> None:
        self.settings = types.SimpleNamespace(language="Italian")
        self.metadata_clients = {"tvmaze": FakeTvMaze()}
        self.db = types.SimpleNamespace(media=FakeMediaRepo())


class FakeSettingsManager:
    def __init__(self, item: TvShowItem) -> None:
        self.settings = types.SimpleNamespace(auto_download=True, language="Italian", tracked_items=[item])


class FakeRegistry:
    def __init__(self, category: Any) -> None:
        self._category = category

    def get(self, category_id: str) -> Any:
        return self._category if category_id == TV_CATEGORY_ID else None


class FakeReleaseWatchRepo:
    def __init__(self) -> None:
        self.cancelled: list[dict[str, Any]] = []
        self.attempts: list[dict[str, Any]] = []

    async def expire_overdue(self, limit: int = 100) -> int:
        return 0

    async def due(self, limit: int = 20) -> list[dict[str, Any]]:
        return [{
            "id": 7,
            "category_id": "tv",
            "item_id": "Example Show",
            "unit_key": "S01E02",
            "requirements": {"auto_download": True},
            "preferred_language": "Italian",
            "interval_hours": 2,
        }]

    async def cancel_unit(self, category_id: str, item_id: str, unit_key: str, *, error: str = "", outcome: dict[str, Any] | None = None) -> None:
        self.cancelled.append({"category_id": category_id, "item_id": item_id, "unit_key": unit_key, "error": error, "outcome": outcome or {}})

    async def record_attempt(self, watch_id: int, **kwargs: Any) -> None:
        self.attempts.append({"watch_id": watch_id, **kwargs})


class FakePipeline:
    def __init__(self) -> None:
        self.calls = 0

    def category_search_context(self) -> Any:
        return types.SimpleNamespace()

    async def run_search(self, *args: Any, **kwargs: Any) -> None:
        self.calls += 1
        return None

    async def run_discovery(self, *args: Any, **kwargs: Any) -> bool:
        self.calls += 1
        return False


def tv_item(auto_download: bool) -> TvShowItem:
    return TvShowItem(key="Example Show", language="Italian", auto_download=auto_download, metadata={"tvmaze_id": 123})


def test_tv_watch_plan_is_opt_in(check: Check) -> None:
    category = TvShowCategory()
    context = FakeContext()
    off_plan = run(category.build_watch_plan(tv_item(False), context))
    on_plan = run(category.build_watch_plan(tv_item(True), context))
    check.ok(not getattr(off_plan, "release_watches", []), "TV watch plan must not create episode watches when per-show auto-download is off")
    check.ok(any(getattr(w, "unit_key", "") == "S01E02" for w in getattr(on_plan, "release_watches", [])), "explicit TV opt-in should still create the missing frontier watch")


def test_due_tv_watch_cancelled_before_search_when_not_opted_in(check: Check) -> None:
    item = tv_item(False)
    repo = FakeReleaseWatchRepo()
    pipeline = FakePipeline()
    scheduler = object.__new__(MediaScheduler)
    scheduler._db = types.SimpleNamespace(release_watches=repo)
    scheduler._categories = FakeRegistry(TvShowCategory())
    scheduler._settings_manager = FakeSettingsManager(item)
    scheduler._pipeline = pipeline
    scheduler._notifications = None
    run(scheduler.process_release_watches())
    check.ok(pipeline.calls == 0, "TV release-watch policy must cancel before any search/discovery when not opted in")
    check.ok(repo.cancelled and repo.cancelled[0]["outcome"].get("status") == "search_disabled_by_category_policy", "blocked TV watches should be cancelled, not retried every interval")


def test_tv_title_scope_rejects_episode_title_false_positive(check: Check) -> None:
    category = TvShowCategory()
    item = TvShowItem(key="Example City", language="Italian", auto_download=True)
    wrong = SearchResult(title="Different Series S01E06 Example City 2046 1080p WEB-DL", magnet="magnet:?xt=urn:btih:wrong")
    right = SearchResult(title="Example City S01E06 1080p WEB-DL ITA", magnet="magnet:?xt=urn:btih:right")
    check.ok(not category.validate_search_result_for_request(wrong, item, "S01E06"), "TV title validation must not match a show name only in an episode title")
    check.ok(category.validate_search_result_for_request(right, item, "S01E06"), "TV title validation should accept the requested series prefix and unit")


def test_tv_language_fails_closed_for_unknown(check: Check) -> None:
    category = TvShowCategory()
    item = TvShowItem(key="Example Show", language="Italian", auto_download=True)
    unknown = SearchResult(title="Example Show S01E02 1080p WEB-DL", magnet="magnet:?xt=urn:btih:u")
    ita = SearchResult(title="Example Show S01E02 ITA 1080p WEB-DL", magnet="magnet:?xt=urn:btih:i")
    eng = SearchResult(title="Example Show S01E02 ENG 1080p WEB-DL", magnet="magnet:?xt=urn:btih:e")
    multi_pref = SearchResult(title="Example Show S01E02 ITA ENG 1080p WEB-DL", magnet="magnet:?xt=urn:btih:m")
    check.ok(category.candidate_requires_user_language_confirmation(unknown, item, "S01E02", "Italian"), "unknown TV audio language must require user approval")
    check.ok(not category.candidate_requires_user_language_confirmation(ita, item, "S01E02", "Italian"), "preferred TV audio language should not require approval")
    check.ok(category.candidate_requires_user_language_confirmation(eng, item, "S01E02", "Italian"), "visible non-preferred TV audio should require approval")
    check.ok(not category.candidate_requires_user_language_confirmation(multi_pref, item, "S01E02", "Italian, English"), "comma-separated preferred language sets should accept either preferred marker")


def test_movie_collection_import_preserves_source_identity(check: Check) -> None:
    category = MovieCategory()
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(library_root=str(Path(tmp) / "library"))
        item = DownloadItem(
            id="d1",
            item_name="broad query",
            category_id="movie",
            magnet="magnet:?xt=urn:btih:pack",
            torrent_title="Classic Example Release Root",
            import_context=DownloadImportContext(
                category_id="movie",
                item_id="broad query",
                release_title="Classic Example Release Root",
                candidate_snapshot={
                    "title": "Classic Example Release Root",
                    "bundle_context": {"is_bundle": True, "bundle_type": "movie_collection"},
                },
            ),
        )
        file_info = DownloadFileInfo(file_index=0, file_path="Classic Example Release Root/Private Afternoons (1975).mpg", status="complete")
        target = category.download_target_for_item(
            Path("/downloads/Classic Example Release Root/Private Afternoons (1975).mpg"),
            item,
            settings,
            source_name=file_info.file_path,
            file_info=file_info,
            metadata={"title": "broad query", "year": 1975},
        )
        check.ok("broad query" not in str(target), "movie collection import must not rename every child film to the broad query")
        check.ok(target.name == "Private Afternoons (1975).mpg", "movie collection import should preserve each source movie filename")
        check.ok(target.parent.name == "Classic Example Release Root", "movie collection import should preserve a stable collection folder")
        check.ok(category.ready_import_file_allowed(Path("cover.jpg"), item=item, file_info=None, settings=settings) is False, "movie category must not import JPGs as primary movie payloads")
        check.ok(category.ready_import_file_allowed(Path("film.avi"), item=item, file_info=None, settings=settings) is True, "movie category should import video files as primary payloads")


if __name__ == "__main__":
    check = Check()
    test_tv_watch_plan_is_opt_in(check)
    test_due_tv_watch_cancelled_before_search_when_not_opted_in(check)
    test_tv_title_scope_rejects_episode_title_false_positive(check)
    test_tv_language_fails_closed_for_unknown(check)
    test_movie_collection_import_preserves_source_identity(check)
    check.finish()
