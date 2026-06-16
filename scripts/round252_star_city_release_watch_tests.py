#!/usr/bin/env python3
"""Round 252 regressions for aired-missing TV release watches.

The Star City failure showed that TV suggestions could detect aired missing
frontier episodes, while the release-watch scheduler had no concrete rows to
search/retry because TV watch planning only looked for future ``next_episode``
metadata.  These tests keep the fix category-owned: TV builds SxxEyy watches;
the generic scheduler merely stores/retries them.
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

from src.core.categories.tv import TvShowCategory
from src.core.categories.workflows.tv_suggestions import TvSuggestionWorkflow
from src.core.models import TvShowItem


class Check:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def ok(self, condition: bool, message: str) -> None:
        if not condition:
            self.failures.append(message)

    def finish(self) -> None:
        if self.failures:
            print("Round 252 Star City release-watch failures:")
            for failure in self.failures:
                print(f" - {failure}")
            raise SystemExit(1)
        print("round252_star_city_release_watch_tests: OK")


def run(coro: Any) -> Any:
    return asyncio.run(coro)


class FakeSettings:
    language = "Italian"
    auto_download = True


class FakeMediaRepo:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    async def list_category_units(self, *_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return list(self.rows)

    async def get_category_metadata(self, *_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return []


class FakeDownloadRepo:
    def __init__(self) -> None:
        self.cleared: list[tuple[str, str]] = []
        self.upserted: list[Any] = []

    async def clear_suggestions_for_item(self, category_id: str, item_key: str) -> None:
        self.cleared.append((category_id, item_key))

    async def upsert_suggested_action(self, suggestion: Any) -> None:
        self.upserted.append(suggestion)

    async def get_upgrade_candidates(self, *_args: Any, **_kwargs: Any) -> list[Any]:
        return []


class FakeDatabase:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.media = FakeMediaRepo(rows)
        self.downloads = FakeDownloadRepo()


class FakeTVMaze:
    def __init__(self, episodes: list[dict[str, Any]], *, last_error: str = "") -> None:
        self.episodes = episodes
        self.last_error = last_error

    async def get_episode_list(self, _show_id: int) -> list[dict[str, Any]]:
        return list(self.episodes)

    async def search(self, _query: str) -> list[dict[str, Any]]:
        return [{"id": 1001, "name": "Star City"}]


class FakeContext:
    def __init__(self, db: FakeDatabase, tvmaze: FakeTVMaze) -> None:
        self.db = db
        self.settings = FakeSettings()
        self.metadata_clients = {"tvmaze": tvmaze}


def star_city_item() -> TvShowItem:
    return TvShowItem(
        key="Star City",
        language="Italian",
        auto_download=True,
        tvmaze_id=1001,
        metadata={"tvmaze": {"id": 1001, "status": "Running", "schedule": {"days": ["Friday"], "time": "03:00"}}},
    )


def star_city_episodes() -> list[dict[str, Any]]:
    return [
        {"season": 1, "number": 1, "name": "Episode 1", "airdate": "2026-05-23"},
        {"season": 1, "number": 2, "name": "Episode 2", "airdate": "2026-05-30"},
        {"season": 1, "number": 3, "name": "Episode 3", "airdate": "2026-06-06"},
        {"season": 1, "number": 4, "name": "Episode 4", "airdate": "2026-06-12"},
        {"season": 1, "number": 5, "name": "Episode 5", "airdate": "2099-01-01"},
    ]


def test_aired_missing_frontier_builds_release_watches(check: Check) -> None:
    async def scenario():
        db = FakeDatabase([
            {"season": 1, "episode": 1, "status": "downloaded"},
            {"season": 1, "episode": 2, "status": "downloaded"},
        ])
        context = FakeContext(db, FakeTVMaze(star_city_episodes()))
        plan = await TvShowCategory().build_watch_plan(star_city_item(), context)
        return plan

    plan = run(scenario())
    keys = [watch.unit_key for watch in plan.release_watches]
    check.ok(plan.mode == "release_watch", "aired missing frontier should create an active release-watch plan")
    check.ok("S01E03" in keys and "S01E04" in keys, "S01E03 and S01E04 should become concrete retry watches")
    check.ok("S01E05" not in keys, "future unaired episodes should not be treated as already missing")
    check.ok(all(w.requirements.get("auto_download") is True for w in plan.release_watches), "watch requirements should preserve per-item auto-download")
    check.ok(any(w.payload.get("watch_trigger") == "already_aired_missing_frontier" for w in plan.release_watches), "watch payload should explain aired-missing trigger")
    queries = [feed.query for feed in plan.rss_feeds]
    check.ok("Star City S01E03" in queries and "Star City S01E04" in queries, "RSS/search feed queries should be concrete episode windows")


def test_provider_outage_does_not_clear_existing_suggestions(check: Check) -> None:
    async def scenario():
        db = FakeDatabase([
            {"season": 1, "episode": 1, "status": "downloaded"},
            {"season": 1, "episode": 2, "status": "downloaded"},
        ])
        tvmaze = FakeTVMaze([], last_error="timeout")
        workflow = TvSuggestionWorkflow(db=db, tvmaze_client=tvmaze)
        count = await workflow.compile_one(star_city_item())
        return count, db.downloads.cleared, db.downloads.upserted

    count, cleared, upserted = run(scenario())
    check.ok(count == 0, "provider outage should not fabricate new suggestions")
    check.ok(cleared == [], "provider outage should preserve existing suggestions instead of clearing them")
    check.ok(upserted == [], "provider outage should not persist empty/fake suggestions")


if __name__ == "__main__":
    check = Check()
    test_aired_missing_frontier_builds_release_watches(check)
    test_provider_outage_does_not_clear_existing_suggestions(check)
    check.finish()
