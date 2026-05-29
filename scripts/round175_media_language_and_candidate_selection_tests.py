#!/usr/bin/env python3
"""Round 175 regressions for media language and torrent candidate selection."""

from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ai.tools.scheduling import (
    SearchMediaTorrentsTool,
    _annotate_selection_policy,
    _search_result_next_actions,
)
from src.core.categories.tv import TvShowCategory
from src.core.models import ToolExecutionContext
from src.core.scheduler_services import SchedulerServiceContext, SchedulerTorrentSearchService


class StubScheduler:
    def __init__(self):
        self.calls = []

    async def search_media_torrents(self, **kwargs):
        self.calls.append(kwargs)
        return {"candidates": [], "category_id": kwargs.get("category_id"), "name": kwargs.get("name"), "query": "stub"}


class ItalianProfileCategory:
    category_id = "tv"

    def category_download_profile(self, settings):
        return {"language": "Italian"}

    def normalize_search_language(self, language, *, explicit=False):
        return language


def test_search_tool_does_not_treat_chat_language_as_media_language():
    import asyncio

    scheduler = StubScheduler()
    tool = SearchMediaTorrentsTool(scheduler=scheduler)
    asyncio.run(tool.execute({"name": "The Boys", "category_id": "tv", "language": "English"}, ToolExecutionContext(session_id="test")))
    assert scheduler.calls, "scheduler was not called"
    assert scheduler.calls[0]["language"] is None, scheduler.calls[0]
    assert scheduler.calls[0]["language_explicit"] is False, scheduler.calls[0]

    asyncio.run(tool.execute({"name": "The Boys", "category_id": "tv", "language": "English", "language_is_explicit": True}, ToolExecutionContext(session_id="test")))
    assert scheduler.calls[-1]["language"] == "English", scheduler.calls[-1]
    assert scheduler.calls[-1]["language_explicit"] is True, scheduler.calls[-1]


def test_effective_language_uses_category_profile_before_global_language():
    context = SchedulerServiceContext(
        settings_manager=SimpleNamespace(settings=SimpleNamespace(language="English", category_settings={"media": {"download_profile": {"language": "Italian"}}})),
        db=None,
        pipeline=None,
        aggregator=None,
        downloader=None,
        categories=None,
        tvmaze=None,
        metadata_enricher=None,
    )
    service = SchedulerTorrentSearchService(context)
    category = ItalianProfileCategory()
    assert service._effective_search_language(category, requested_language=None, explicit=False, settings=context.settings_manager.settings, category_id="tv") == "Italian"


def test_effective_language_falls_back_to_global_media_language():
    context = SchedulerServiceContext(
        settings_manager=SimpleNamespace(settings=SimpleNamespace(language="Italian", category_settings={})),
        db=None,
        pipeline=None,
        aggregator=None,
        downloader=None,
        categories=None,
        tvmaze=None,
        metadata_enricher=None,
    )
    service = SchedulerTorrentSearchService(context)
    category = TvShowCategory()
    assert service._effective_search_language(category, requested_language=None, explicit=False, settings=context.settings_manager.settings, category_id="tv") == "Italian"


def test_tv_pack_relevance_does_not_match_hardy_boys_for_the_boys():
    category = TvShowCategory()
    assert category._is_relevant_season_pack_result(
        SimpleNamespace(title="The Boys S01 Complete 1080p"),
        1,
        item=SimpleNamespace(key="The Boys"),
    )
    assert not category._is_relevant_season_pack_result(
        SimpleNamespace(title="The Hardy Boys and Nancy Drew Mysteries Complete Series 1080p"),
        1,
        item=SimpleNamespace(key="The Boys"),
    )


def test_low_seed_wrong_language_candidate_is_not_clear_queue_choice():
    candidates = [
        {
            "candidate_id": "bad",
            "title": "The Boys SEASON 01 COMPLETE English Hindi 1080p",
            "seeders": 2,
            "languages": ["English", "Hindi"],
        }
    ]
    _annotate_selection_policy(candidates, preferred_language="Italian")
    assert candidates[0]["auto_queue_allowed"] is False
    assert "very low seeders" in candidates[0]["auto_queue_blocked_reason"]
    assert "preferred media language" in candidates[0]["auto_queue_blocked_reason"]
    actions = _search_result_next_actions(candidates=candidates, search_scope="bundle_preferred", result_set_id="rs", has_batch=False)
    assert not any(action.get("action") == "queue_clear_candidate" for action in actions), actions
    assert any(action.get("action") == "do_not_auto_queue_top_candidate" for action in actions), actions
    assert any(action.get("action") == "try_individual_units_before_queueing_weak_pack" for action in actions), actions


if __name__ == "__main__":
    test_search_tool_does_not_treat_chat_language_as_media_language()
    test_effective_language_uses_category_profile_before_global_language()
    test_effective_language_falls_back_to_global_media_language()
    test_tv_pack_relevance_does_not_match_hardy_boys_for_the_boys()
    test_low_seed_wrong_language_candidate_is_not_clear_queue_choice()
    print("round175 media language and candidate selection tests passed")
