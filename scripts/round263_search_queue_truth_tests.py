#!/usr/bin/env python3
"""Round 263 regressions for stale search suppression and truthful queue receipts.

The logs showed two bad behaviours after the Round 262 hard stop: a stale TV
release watch still performed notify-only searches for an episode already in the
library, and the assistant claimed generic/tag torrent candidates were queued
when duplicate/failed rows prevented a new active download. These tests cover the
category-owned hooks and tool receipts without hard-coding any fixture title.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import types
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("aiosqlite", types.SimpleNamespace(Connection=object, Row=dict, Cursor=object))

from src.ai.tools.queue_download_support import QueueDownloadRequest, QueueDownloadService
from src.core.categories.tv import TvShowCategory
from src.core.domain_models.media import ItemList
from src.core.models import DownloadItem, DownloadStatus, ToolExecutionContext, TvShowItem
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
            print("Round 263 search/queue truth failures:")
            for failure in self.failures:
                print(f" - {failure}")
            raise SystemExit(1)
        print("round263_search_queue_truth_tests: OK")


def run(coro: Any) -> Any:
    return asyncio.run(coro)


class FakeSettings:
    def __init__(self, item: TvShowItem) -> None:
        self.auto_download = True
        self.language = "English"
        self.tracked_items = ItemList(items=[item])


class FakeSettingsManager:
    def __init__(self, item: TvShowItem) -> None:
        self.settings = FakeSettings(item)


class FakeRegistry:
    def __init__(self, category: Any) -> None:
        self._category = category

    def get(self, category_id: str) -> Any:
        return self._category if category_id == TV_CATEGORY_ID else None


class FakeReleaseWatchRepo:
    def __init__(self) -> None:
        self.completed: list[tuple[str, str, str, dict[str, Any]]] = []
        self.attempts: list[dict[str, Any]] = []

    async def expire_overdue(self, limit: int = 100) -> int:
        return 0

    async def due(self, limit: int = 20) -> list[dict[str, Any]]:
        return [{
            "id": 42,
            "category_id": "tv",
            "item_id": "Example Show",
            "unit_key": "S01E02",
            "requirements": {"auto_download": False},
            "preferred_language": "English",
            "interval_hours": 2,
        }]

    async def complete(self, category_id: str, item_id: str, unit_key: str, *, outcome: dict[str, Any] | None = None) -> None:
        self.completed.append((category_id, item_id, unit_key, outcome or {}))

    async def record_attempt(self, watch_id: int, **kwargs: Any) -> None:
        self.attempts.append({"watch_id": watch_id, **kwargs})


class FakeDb:
    def __init__(self, repo: FakeReleaseWatchRepo) -> None:
        self.release_watches = repo


class FakePipeline:
    def __init__(self) -> None:
        self.search_calls = 0

    def category_search_context(self) -> Any:
        return types.SimpleNamespace()

    async def run_search(self, *args: Any, **kwargs: Any) -> None:
        self.search_calls += 1
        return None

    async def run_discovery(self, *args: Any, **kwargs: Any) -> bool:
        self.search_calls += 1
        return False


class AlreadySatisfiedCategory:
    async def discovery_already_satisfied(self, item: Any, unit_label: str | None, context: Any | None = None) -> bool:
        return True

    def release_watch_auto_download_allowed(self, item: Any, requirements: dict[str, Any], settings: Any) -> bool:
        return False


class FakeCanonicalBuilder:
    async def build(self, category_id: str, item_id: str, *, settings_item: Any | None = None, active_downloads: list[Any] | None = None) -> dict[str, Any]:
        return {
            "category_id": category_id,
            "item_id": item_id,
            "seasons": [{"season_number": 1, "episodes": [{"season": 1, "episode": 2, "status": "downloaded"}]}],
            "computed": {"local_episode_keys": ["S01E02"]},
        }


class FakeQueueScheduler:
    def __init__(self, result: dict[str, Any]) -> None:
        self._categories = None
        self.result = result

    async def queue_download(self, **kwargs: Any) -> dict[str, Any]:
        return self.result


def test_release_watch_satisfaction_stops_notify_search(check: Check) -> None:
    item = TvShowItem(key="Example Show", auto_download=False)
    repo = FakeReleaseWatchRepo()
    pipeline = FakePipeline()
    scheduler = object.__new__(MediaScheduler)
    scheduler._db = FakeDb(repo)
    scheduler._categories = FakeRegistry(AlreadySatisfiedCategory())
    scheduler._settings_manager = FakeSettingsManager(item)
    scheduler._pipeline = pipeline
    scheduler._notifications = None
    run(scheduler.process_release_watches())
    check.ok(pipeline.search_calls == 0, "already-satisfied release watches must not call search/discovery")
    check.ok(repo.completed and repo.completed[0][3].get("status") == "already_satisfied", "already-local watch should be recorded as completed")


def test_tv_satisfaction_uses_canonical_library_object(check: Check) -> None:
    category = TvShowCategory()
    item = TvShowItem(key="Example Show")
    context = types.SimpleNamespace(library_objects=FakeCanonicalBuilder())
    check.ok(run(category.discovery_already_satisfied(item, "S01E02", context)) is True, "TV satisfaction must read canonical library objects, not only raw unit rows")


def test_scheduler_queue_receipt_rejects_failed_duplicate(check: Check) -> None:
    scheduler = object.__new__(MediaScheduler)
    row = DownloadItem(
        id="failed1",
        item_name="Example Movie",
        category_id="movie",
        magnet="magnet:?xt=urn:btih:deadbeef",
        status=DownloadStatus.FAILED,
    )
    receipt = scheduler._queue_download_receipt(row)
    check.ok(receipt.get("status") == "not_queued", "failed duplicate rows must not be reported as queued")
    check.ok(bool(receipt.get("error")), "not-queued receipts need a user-safe error")


def test_queue_tool_rejects_not_queued_scheduler_result(check: Check) -> None:
    service = QueueDownloadService(FakeQueueScheduler({
        "status": "not_queued",
        "download_status": "failed",
        "download_id": "old1",
        "error": "A matching download row is failed; no new queue row was created.",
    }))
    request = QueueDownloadRequest.from_arguments({"candidate_ids": ["c1"], "category_id": "movie"}, ToolExecutionContext(session_id="s"))
    entry = {
        "candidate_id": "c1",
        "candidate": {"title": "Example Candidate 1080p", "magnet": "magnet:?xt=urn:btih:c1", "category_id": "movie"},
        "cache_data": {"name": "broad tag", "item_id": "broad tag", "category_id": "movie"},
    }
    result = run(service._queue_one_entry(request, entry, 0, 1))
    check.ok(bool(result.get("error")), "queue tool must not convert not_queued scheduler receipts into success")


def test_generic_tag_candidates_get_release_level_identity(check: Check) -> None:
    service = QueueDownloadService(FakeQueueScheduler({"status": "queued", "download_id": "new1", "download_status": "queued"}))
    request = QueueDownloadRequest.from_arguments({"candidate_ids": ["c1"], "category_id": "movie"}, ToolExecutionContext(session_id="s"))
    candidate = {"title": "Distinct Candidate Film 2001 1080p", "magnet": "magnet:?xt=urn:btih:c1", "category_id": "movie"}
    context = service._import_context_for_candidate(
        request,
        "broad tag",
        candidate,
        {"name": "broad tag", "item_id": "broad tag", "category_id": "movie"},
        None,
        None,
        {},
    )
    check.ok(context.get("item_id") == "Distinct Candidate Film 2001 1080p", "unresolved broad/tag candidates must not all collapse to the query text")
    check.ok("broad tag" not in context.get("stable_provider_key", ""), "stable provider key should use the concrete release identity when no provider ID exists")


if __name__ == "__main__":
    check = Check()
    test_release_watch_satisfaction_stops_notify_search(check)
    test_tv_satisfaction_uses_canonical_library_object(check)
    test_scheduler_queue_receipt_rejects_failed_duplicate(check)
    test_queue_tool_rejects_not_queued_scheduler_result(check)
    test_generic_tag_candidates_get_release_level_identity(check)
    check.finish()
