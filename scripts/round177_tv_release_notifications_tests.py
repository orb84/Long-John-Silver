#!/usr/bin/env python3
"""Round 177 regressions for TV release events, frontier suggestions, and language fallbacks."""

from pathlib import Path
from types import SimpleNamespace
import asyncio
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.categories.tv import TvShowCategory
from src.core.categories.workflows.tv_suggestions import TvSuggestionWorkflow
from src.core.models import TvShowItem
from src.core.scheduler_services import SchedulerServiceContext, SchedulerTorrentSearchService


class FakeMediaRepo:
    def __init__(self, downloaded):
        self._downloaded = downloaded

    async def list_category_units(self, category_id, item_id, status="downloaded"):
        return [{"season": s, "episode": e} for s, e in self._downloaded]


class FakeReleaseWatches:
    def __init__(self):
        self.rows = []
        self.completed = []

    async def upsert(self, **kwargs):
        self.rows.append(kwargs)
        return len(self.rows)

    async def complete(self, category_id, item_id, unit_key):
        self.completed.append((category_id, item_id, unit_key))


class FakePipeline:
    def __init__(self, result=True):
        self.calls = []
        self.result = result

    async def run_discovery(self, item, episode_label=None, force=False, language=None):
        self.calls.append({"item": item.key, "episode_label": episode_label, "force": force, "language": language})
        return self.result


class FakeNotifications:
    def __init__(self):
        self.messages = []

    async def notify(self, message, **kwargs):
        self.messages.append({"message": message, **kwargs})
        return len(self.messages)


def _context(downloaded, pipeline=None):
    watches = FakeReleaseWatches()
    db = SimpleNamespace(media=FakeMediaRepo(downloaded), release_watches=watches)
    settings = SimpleNamespace(language="Italian")
    return SimpleNamespace(db=db, pipeline=pipeline or FakePipeline(), settings=settings), watches


def test_comma_joined_language_profile_uses_primary_only():
    settings = SimpleNamespace(language="English", category_settings={"media": {"download_profile": {"language": "Italian, English, Spanish"}}})
    service = SchedulerTorrentSearchService(SchedulerServiceContext(
        settings_manager=SimpleNamespace(settings=settings),
        db=None, pipeline=None, aggregator=None, downloader=None, categories=None, tvmaze=None, metadata_enricher=None,
    ))
    assert service._effective_search_language(TvShowCategory(), requested_language=None, explicit=False, settings=settings, category_id="tv") == "Italian"


def test_frontier_suggestion_beats_old_historical_gaps():
    workflow = TvSuggestionWorkflow(db=SimpleNamespace())
    missing = [(1, 1, "Pilot"), (5, 10, "Finale")]
    downloaded = {(5, i) for i in range(1, 10)}
    suggestions = workflow._download_batch_suggestions(TvShowItem(key="For All Mankind", language="Italian"), missing, downloaded, "now", {"provider_episode_count": 50, "downloaded_episode_count": 9, "library_evidence_source": "canonical_library_object"})
    assert suggestions[0].action_type == "download_latest_frontier"
    assert "S05E10" in suggestions[0].title
    assert suggestions[0].priority > 100


def test_tv_release_event_autodownloads_only_preferred_language_frontier():
    category = TvShowCategory()
    item = TvShowItem(key="For All Mankind", language="Italian", auto_download=True)
    ctx, watches = _context({(5, i) for i in range(1, 10)}, pipeline=FakePipeline(result=True))
    notifications = FakeNotifications()
    result = asyncio.run(category.handle_release_event(
        item,
        {"trigger": "rss", "unit_label": "S05E10", "source_result": {"title": "For All Mankind S05E10 ITA 1080p", "magnet": "magnet:?x"}},
        ctx,
        notifications=notifications,
    ))
    assert result["status"] == "queued", result
    assert ctx.pipeline.calls == [{"item": "For All Mankind", "episode_label": "S05E10", "force": False, "language": "Italian"}]
    assert not notifications.messages
    assert watches.completed == [("tv", "For All Mankind", "S05E10")]


def test_tv_release_event_notifies_and_watches_non_preferred_language():
    category = TvShowCategory()
    item = TvShowItem(key="For All Mankind", language="Italian", auto_download=True)
    ctx, watches = _context({(5, i) for i in range(1, 10)}, pipeline=FakePipeline(result=True))
    notifications = FakeNotifications()
    result = asyncio.run(category.handle_release_event(
        item,
        {"trigger": "rss", "unit_label": "S05E10", "source_result": {"title": "For All Mankind S05E10 English Hindi 1080p", "magnet": "magnet:?x"}},
        ctx,
        notifications=notifications,
    ))
    assert result["status"] == "notified", result
    assert result["candidate_language_status"] == "non_preferred"
    assert ctx.pipeline.calls == []
    assert watches.rows and watches.rows[0]["interval_hours"] == 2.0
    assert notifications.messages and notifications.messages[0]["event_type"] == "tv_release_available"
    assert notifications.messages[0]["actions"][0]["category_workflow"]["workflow"] == "download_specific_episode"


def test_tv_release_notification_action_preserves_rss_candidate():
    category = TvShowCategory()
    item = TvShowItem(key="For All Mankind", language="Italian", auto_download=False)
    ctx, watches = _context({(5, i) for i in range(1, 10)}, pipeline=FakePipeline(result=True))
    notifications = FakeNotifications()
    result = asyncio.run(category.handle_release_event(
        item,
        {"trigger": "rss", "unit_label": "S05E10", "source_result": {"title": "For All Mankind S05E10 ITA 1080p", "magnet": "magnet:?xt=urn:btih:abc", "size_bytes": 1234, "seeders": 42}},
        ctx,
        notifications=notifications,
    ))
    assert result["status"] == "notified", result
    action_args = notifications.messages[0]["actions"][0]["category_workflow"]["arguments"]
    assert action_args["magnet"] == "magnet:?xt=urn:btih:abc"
    assert action_args["torrent_title"] == "For All Mankind S05E10 ITA 1080p"
    assert action_args["approved_from_notification"] is True


if __name__ == "__main__":
    test_comma_joined_language_profile_uses_primary_only()
    test_frontier_suggestion_beats_old_historical_gaps()
    test_tv_release_event_autodownloads_only_preferred_language_frontier()
    test_tv_release_event_notifies_and_watches_non_preferred_language()
    test_tv_release_notification_action_preserves_rss_candidate()
    print("round177 TV release notification tests passed")
