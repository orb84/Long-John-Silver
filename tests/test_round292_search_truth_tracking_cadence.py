"""Round 292 regressions for search truth, acquisition continuity, and dormant lifecycle work."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import aiosqlite
import pytest

from src.ai.goal_state import AgentGoalStateManager
from src.ai.tool_policy import AgentToolPolicy
from src.core.categories.movie import MovieCategory
from src.core.categories.title_authority import CategoryTitleAuthority
from src.core.categories.tv import TvShowCategory
from src.core.category_lifecycle import CategoryLifecycleEngine
from src.core.models import CategoryItem, Intent
from src.web.chat_turn_registry import ChatTurnRegistry


class _Result(SimpleNamespace):
    pass


def test_year_backed_one_word_movie_accepts_normal_release_metadata() -> None:
    title = "Oppenheimer 2023 ITA ENG 1080p x265 IMAX Paso77"
    assert CategoryTitleAuthority.matches_any_alias(title, ["Oppenheimer"], disambiguating_year=2023)
    assert not CategoryTitleAuthority.matches_any_alias(
        "Oppenheimer The Real Story 2023 1080p", ["Oppenheimer"], disambiguating_year=2023
    )


def test_movie_final_filter_consumes_category_title_identity_verdict() -> None:
    movie = MovieCategory()
    candidate = {
        "title": "Oppenheimer (2023) BluRay 1080p.H264 Ita Eng AC3 5.1 Sub Ita Eng Group",
        "title_identity": {"matches_item": True, "source": "movie_provider_alias_and_year"},
        "unit_descriptor": {"label": "Oppenheimer", "coordinates": {"title": "Oppenheimer", "year": 2023}},
    }
    assert movie.filter_agent_candidate_payloads_for_request([candidate], language="Italian") == [candidate]


@pytest.mark.asyncio
async def test_movie_search_stops_before_low_value_alias_fanout_after_enough_valid_results() -> None:
    movie = MovieCategory()
    item = movie.create_item(
        "Oppenheimer",
        year=2023,
        language="Italian",
        metadata={"title": "Oppenheimer", "year": 2023, "title_aliases": ["Gadget", "Openheimers"]},
    )
    calls: list[str] = []

    class Aggregator:
        async def search(self, query: str, **kwargs):
            calls.append(query)
            return [
                _Result(
                    title=f"Oppenheimer 2023 ITA ENG 1080p x265 IMAX Group{i}",
                    magnet=f"magnet:?xt=urn:btih:{i:040d}",
                    source="fixture",
                    seeders=100 - i,
                )
                for i in range(45)
            ]

    results, _ = await movie.search_agent_candidates(
        item,
        language="Italian",
        context=SimpleNamespace(aggregator=Aggregator(), metadata_enricher=None),
    )
    assert len(results) >= 40
    assert calls == ["Oppenheimer 2023 ITA"]
    assert all("Gadget" not in query and "Openheimers" not in query for query in calls)


@pytest.mark.asyncio
async def test_movie_search_keeps_querying_when_identity_pool_lacks_requested_language() -> None:
    movie = MovieCategory()
    item = movie.create_item(
        "Oppenheimer", year=2023, language="Italian",
        metadata={"title": "Oppenheimer", "year": 2023},
    )
    calls: list[str] = []

    class Aggregator:
        async def search(self, query: str, **kwargs):
            calls.append(query)
            language_tag = "ENG" if len(calls) == 1 else "ITA"
            return [
                _Result(
                    title=f"Oppenheimer 2023 {language_tag} 1080p x265 Group{i}",
                    magnet=f"magnet:?xt=urn:btih:{len(calls):02d}{i:038d}",
                    source="fixture",
                    seeders=100 - i,
                )
                for i in range(24)
            ]

    results, _ = await movie.search_agent_candidates(
        item, language="Italian",
        context=SimpleNamespace(aggregator=Aggregator(), metadata_enricher=None),
    )
    assert len(calls) >= 2
    assert any("ITA" in str(getattr(result, "title", "")) for result in results)


def test_search_intent_can_refine_queueable_media_candidates() -> None:
    allowed = AgentToolPolicy().allowed_tool_names(Intent.SEARCH)
    assert "search_media_torrents" in allowed
    assert "queue_download" not in allowed


class _GoalSystem:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get_preference(self, key: str):
        return self.values.get(key)

    async def set_preference(self, key: str, value: str):
        self.values[key] = value


class _GoalDb:
    def __init__(self) -> None:
        self.system = _GoalSystem()


@pytest.mark.asyncio
async def test_search_followup_preserves_structured_acquisition_category() -> None:
    db = _GoalDb()
    manager = AgentGoalStateManager(db)
    await db.system.set_preference(
        "agent_active_goal_session",
        json.dumps({
            "goal_id": "g1",
            "session_id": "session",
            "intent": "download",
            "user_goal": "download Oppenheimer in Italian",
            "category_id": "movie",
            "status": "active",
            "constraints": {},
            "result_sets": [{"result_set_id": "r1", "category_id": "movie", "candidate_count": 2}],
            "next_actions": [],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }),
    )
    # Avoid repository lookup complexity; the already persisted goal itself is
    # the structured continuity authority being exercised here.
    manager._recent_result_set_summaries = lambda session_id: asyncio.sleep(0, result=[  # type: ignore[method-assign]
        {"result_set_id": "r1", "category_id": "movie", "candidate_count": 2}
    ])
    context = await manager.build_context_and_update(
        session_id="session", user_prompt="search harder", intent=Intent.SEARCH, category_id=None
    )
    active = await manager.active_goal("session")
    assert active is not None and active.category_id == "movie"
    assert "search_media_torrents" in context


@pytest.mark.asyncio
async def test_chat_turn_registry_blocks_same_session_across_independent_callers() -> None:
    registry = ChatTurnRegistry()
    blocker = asyncio.Event()

    async def run():
        await blocker.wait()

    started, first = await registry.start("same-session", "one", run)
    assert started is True
    second_started, second = await registry.start("same-session", "two", run)
    assert second_started is False
    assert second.turn_id == "one"
    await registry.cancel("same-session", "one")
    with pytest.raises(asyncio.CancelledError):
        await first.task
    await registry.release("same-session", "one")


def test_tv_new_item_keeps_tracking_unset_until_provider_lifecycle_is_known() -> None:
    item = TvShowCategory().create_item("New Show", auto_download=None)
    assert item.auto_download is None


@pytest.mark.asyncio
async def test_tv_active_show_defaults_tracking_on_but_user_false_remains_authoritative() -> None:
    tv = TvShowCategory()
    context = SimpleNamespace(db=None, settings=SimpleNamespace(language="English"), metadata_clients={})
    active_metadata = {"status": "Returning Series", "next_episode": {"season": 2, "number": 4, "airdate": "2099-01-01"}}

    unset = tv.create_item("Active", auto_download=None, metadata=active_metadata)
    plan = await tv.build_watch_plan(unset, context)
    assert plan.item_updates.get("auto_download") is True

    explicit_off = tv.create_item("Active", auto_download=False, metadata=active_metadata)
    off_plan = await tv.build_watch_plan(explicit_off, context)
    assert "auto_download" not in off_plan.item_updates
    assert explicit_off.auto_download is False


class _LifecycleMediaRepo:
    async def get_category_metadata(self, category_id, item_id, provider=None): return []
    async def list_category_units(self, category_id, item_id, **kwargs): return []

class _LifecycleDownloadsRepo:
    async def get_suggested_actions(self, **kwargs): return []

class _LifecycleSystemRepo:
    async def get_taste_profile_snapshot(self, user_id, category_id): return None

class _LifecycleDb:
    def __init__(self, connection):
        self._connection = connection
        self.media = _LifecycleMediaRepo()
        self.downloads = _LifecycleDownloadsRepo()
        self.system = _LifecycleSystemRepo()
    async def get_connection(self): return self._connection


@pytest.mark.asyncio
async def test_lifecycle_preflight_leaves_future_clean_item_fully_dormant() -> None:
    connection = await aiosqlite.connect(":memory:")
    connection.row_factory = aiosqlite.Row
    try:
        await connection.executescript(Path("migrations/106_item_lifecycle_policy_ledger.sql").read_text())
        await connection.commit()
        engine = CategoryLifecycleEngine(_LifecycleDb(connection))
        item = CategoryItem(key="Oppenheimer", category_id="movie")
        future = (datetime.now(timezone.utc) + timedelta(days=180)).isoformat()
        await engine.record_processing_result(
            item, purpose="scheduled_check", status="success", reason="stable",
            next_check_at=future, valid_until=future,
        )
        assert await engine.scheduled_work_is_due(item) is False
        await engine.invalidate_item("movie", "Oppenheimer", reason="library_changed")
        assert await engine.scheduled_work_is_due(item) is True
    finally:
        await connection.close()


def test_movie_stable_lifecycle_uses_very_long_cadence() -> None:
    movie = MovieCategory()
    item = movie.create_item("Oppenheimer", year=2023, metadata={"tmdb_id": 872585}, state={"library_present": True})
    result = movie.lifecycle_decision(item, {"policy": movie.lifecycle_policy()})
    due = datetime.fromisoformat(result["next_check_at"])
    assert due - datetime.now(timezone.utc) > timedelta(days=175)


def test_periodic_full_library_scan_is_daily_safety_net() -> None:
    source = Path("src/core/scheduler.py").read_text(encoding="utf-8")
    assert 'self.scan_library, interval_seconds=SECONDS_PER_DAY' in source
    assert 'self._watch_library_filesystem_job, interval_seconds=120' in source
