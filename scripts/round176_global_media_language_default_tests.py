#!/usr/bin/env python3
"""Round 176 regressions for global media language defaults.

The setup/Compass language is a default media/download preference, but the
current chat message language must never override it.
"""
from pathlib import Path
from types import SimpleNamespace
import asyncio
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ai.tools.scheduling import SearchMediaTorrentsTool
from src.core.categories.tv import TvShowCategory
from src.core.models import ToolExecutionContext
from src.core.scheduler_services import SchedulerServiceContext, SchedulerTorrentSearchService


class StubScheduler:
    def __init__(self):
        self.calls = []

    async def search_media_torrents(self, **kwargs):
        self.calls.append(kwargs)
        return {"candidates": [], "category_id": kwargs.get("category_id"), "name": kwargs.get("name")}


def _service(settings):
    return SchedulerTorrentSearchService(SchedulerServiceContext(
        settings_manager=SimpleNamespace(settings=settings),
        db=None,
        pipeline=None,
        aggregator=None,
        downloader=None,
        categories=None,
        tvmaze=None,
        metadata_enricher=None,
    ))


def test_chat_language_argument_is_discarded_but_global_preference_remains_available():
    scheduler = StubScheduler()
    tool = SearchMediaTorrentsTool(scheduler=scheduler)
    asyncio.run(tool.execute({"name": "The Boys", "category_id": "tv", "language": "English"}, ToolExecutionContext(session_id="test")))
    assert scheduler.calls[0]["language"] is None, scheduler.calls[0]
    assert scheduler.calls[0]["language_explicit"] is False, scheduler.calls[0]

    service = _service(SimpleNamespace(language="Italian", category_settings={}))
    assert service._effective_search_language(
        TvShowCategory(), requested_language=None, explicit=False, settings=service._context.settings_manager.settings, category_id="tv"
    ) == "Italian"


def test_profile_language_beats_global_language_and_explicit_beats_profile():
    settings = SimpleNamespace(language="Italian", category_settings={"media": {"download_profile": {"language": "Spanish"}}})
    service = _service(settings)
    category = TvShowCategory()
    assert service._effective_search_language(category, requested_language=None, explicit=False, settings=settings, category_id="tv") == "Spanish"
    assert service._effective_search_language(category, requested_language="Japanese", explicit=True, settings=settings, category_id="tv") == "Japanese"


if __name__ == "__main__":
    test_chat_language_argument_is_discarded_but_global_preference_remains_available()
    test_profile_language_beats_global_language_and_explicit_beats_profile()
    print("round176 global media language default tests passed")
