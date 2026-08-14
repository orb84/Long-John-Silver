"""Executable Round 292 incident regression checks without external services."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ai.tool_policy import AgentToolPolicy
from src.core.categories.movie import MovieCategory
from src.core.categories.title_authority import CategoryTitleAuthority
from src.core.categories.tv import TvShowCategory
from src.core.category_lifecycle import CategoryLifecycleEngine
from src.core.scheduler_services import SchedulerServiceContext, SchedulerTorrentSearchService
from src.core.models import CategoryItem, Intent
from src.web.chat_turn_registry import ChatTurnRegistry


class _Result(SimpleNamespace):
    pass


class _FakeLifecycle(CategoryLifecycleEngine):
    def __init__(self, state):
        self._state = state

    async def get_processing_state(self, category_id, item_id):
        return self._state


class _TVMaze:
    async def search(self, title):
        return [{"id": 42, "name": title, "status": "Running"}]

    async def get_show_details(self, show_id):
        return {
            "id": show_id,
            "name": "New Show",
            "status": "Running",
            "next_episode": {"season": 1, "number": 3, "airdate": "2099-01-01"},
        }


async def _check_movie_search() -> None:
    movie = MovieCategory()
    item = movie.create_item(
        "Oppenheimer",
        year=2023,
        language="Italian",
        metadata={"title": "Oppenheimer", "year": 2023, "title_aliases": ["Gadget", "Openheimers"]},
    )
    calls = []

    class Aggregator:
        async def search(self, query, **kwargs):
            calls.append(query)
            return [
                _Result(
                    title=f"Oppenheimer 2023 ITA ENG 1080p x265 IMAX Group{i}",
                    magnet=f"magnet:?xt=urn:btih:{i:040d}",
                    source="fixture",
                    seeders=100 - i,
                )
                for i in range(24)
            ]

    results, _ = await movie.search_agent_candidates(
        item,
        language="Italian",
        context=SimpleNamespace(aggregator=Aggregator(), metadata_enricher=None),
    )
    assert len(results) == 24
    assert calls == ["Oppenheimer 2023 ITA"], calls


async def _check_movie_language_constraint_prevents_premature_stop() -> None:
    movie = MovieCategory()
    item = movie.create_item(
        "Oppenheimer", year=2023, language="Italian",
        metadata={"title": "Oppenheimer", "year": 2023},
    )
    calls = []

    class Aggregator:
        async def search(self, query, **kwargs):
            calls.append(query)
            marker = "ENG" if len(calls) == 1 else "ITA"
            return [
                _Result(
                    title=f"Oppenheimer 2023 {marker} 1080p x265 Group{i}",
                    magnet=f"magnet:?xt=urn:btih:{len(calls):02d}{i:038d}",
                    source="fixture",
                    seeders=50 - i,
                )
                for i in range(24)
            ]

    await movie.search_agent_candidates(
        item, language="Italian",
        context=SimpleNamespace(aggregator=Aggregator(), metadata_enricher=None),
    )
    assert len(calls) >= 2, calls


async def _check_tv_default() -> None:
    tv = TvShowCategory()
    item = tv.create_item("New Show", auto_download=None)
    assert item.auto_download is None
    context = SimpleNamespace(
        db=None,
        settings=SimpleNamespace(language="English"),
        metadata_clients={"tvmaze": _TVMaze()},
    )
    plan = await tv.build_watch_plan(item, context)
    assert plan.item_updates.get("auto_download") is True, plan.item_updates
    explicit_off = tv.create_item("New Show", auto_download=False)
    off_plan = await tv.build_watch_plan(explicit_off, context)
    assert "auto_download" not in off_plan.item_updates
    assert explicit_off.auto_download is False


async def _check_turn_registry() -> None:
    registry = ChatTurnRegistry()
    blocker = asyncio.Event()

    async def run():
        await blocker.wait()

    started, first = await registry.start("session", "first", run)
    assert started
    started2, current = await registry.start("session", "second", run)
    assert not started2 and current.turn_id == "first"
    await registry.cancel("session", "first")
    try:
        await first.task
    except asyncio.CancelledError:
        pass
    await registry.release("session", "first")


async def _check_lifecycle_preflight() -> None:
    future = (datetime.now(timezone.utc) + timedelta(days=180)).isoformat()
    clean = _FakeLifecycle({"next_check_at": future, "invalidated_by": "[]"})
    assert not await clean.scheduled_work_is_due(CategoryItem(key="Oppenheimer", category_id="movie"))
    invalid = _FakeLifecycle({"next_check_at": future, "invalidated_by": '["library_changed"]'})
    assert await invalid.scheduled_work_is_due(CategoryItem(key="Oppenheimer", category_id="movie"))


def _check_movie_companion_strategy_is_consumed() -> None:
    class Categories:
        def __init__(self):
            self._items = {"movie": MovieCategory()}

        def get(self, category_id):
            return self._items.get(category_id)

    context = SchedulerServiceContext(
        settings_manager=SimpleNamespace(settings=SimpleNamespace()),
        db=None,
        downloader=None,
        pipeline=None,
        aggregator=None,
        categories=Categories(),
    )
    service = SchedulerTorrentSearchService(context)
    settings = SimpleNamespace(
        soulseek=SimpleNamespace(download_preference="torrent_first", parallel_search_enabled=True)
    )
    strategy = service._source_strategy("movie", "Oppenheimer", "item", settings)
    assert strategy["category_strategy"]["foreground_companion_mode"] == "fallback_if_primary_empty"
    source = Path("src/core/scheduler_services.py").read_text(encoding="utf-8")
    assert 'companion_mode == "fallback_if_primary_empty" and not results' in source
    assert 'status": "not_needed_primary_sufficient"' in source


def _check_both_chat_transports_share_session_registry() -> None:
    source = Path("src/web/app.py").read_text(encoding="utf-8")
    assert source.count("await chat_turns.start(") >= 2
    assert 'id="library_scan"' in Path("src/core/scheduler.py").read_text(encoding="utf-8")


async def main() -> None:
    assert CategoryTitleAuthority.matches_any_alias(
        "Oppenheimer 2023 ITA ENG 1080p x265 IMAX Paso77", ["Oppenheimer"], disambiguating_year=2023
    )
    assert not CategoryTitleAuthority.matches_any_alias(
        "Oppenheimer The Real Story 2023", ["Oppenheimer"], disambiguating_year=2023
    )
    policy = AgentToolPolicy()
    assert "search_media_torrents" in policy.allowed_tool_names(Intent.SEARCH)
    acquisition = policy.allowed_tool_names(Intent.SEARCH, acquisition_continuation=True)
    assert acquisition == {
        "enquire_about_media", "metadata_lookup", "search_media_torrents", "inspect_torrent_candidate",
        "get_storage_status", "list_downloads", "category_web_research",
    }
    assert "search_media_torrents" not in policy.allowed_tool_names(Intent.CHAT)
    assert MovieCategory().lifecycle_policy()["upgrade_scan_interval_days"] >= 180
    scheduler = Path("src/core/scheduler.py").read_text(encoding="utf-8")
    assert "scheduled_work_is_due(item)" in scheduler
    assert "self.scan_library, interval_seconds=SECONDS_PER_DAY" in scheduler
    _check_movie_companion_strategy_is_consumed()
    _check_both_chat_transports_share_session_registry()
    await _check_movie_search()
    await _check_movie_language_constraint_prevents_premature_stop()
    await _check_tv_default()
    await _check_turn_registry()
    await _check_lifecycle_preflight()
    print("ROUND292_SEARCH_TRUTH_TRACKING_CADENCE_PASS")


if __name__ == "__main__":
    asyncio.run(main())
