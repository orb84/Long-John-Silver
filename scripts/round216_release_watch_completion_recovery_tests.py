#!/usr/bin/env python3
"""Round 216 release-watch completion/recovery regression tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
import sys

sys.modules.setdefault("aiosqlite", SimpleNamespace(OperationalError=Exception, Connection=object, Cursor=object))

from src.core.download_handler import DownloadCompletionHandler
from src.core.domain_models.enums import DownloadStatus
from src.core.scheduler import MediaScheduler
from src.core.categories.registry import CategoryRegistry
from src.core.categories.tv_workflows import TvWorkflowMixin


class FakeReleaseRepo:
    def __init__(self, queued=None):
        self.completed = []
        self.retryable = []
        self.queued = []
        self.upserts = []
        self._queued = queued or []
    async def complete(self, category_id, item_id, unit_key):
        self.completed.append((category_id, item_id, unit_key))
    async def stale_queued(self, *, older_than_minutes=30, limit=50):
        return list(self._queued)
    async def reset_to_retryable(self, category_id, item_id, unit_key, **kwargs):
        self.retryable.append((category_id, item_id, unit_key, kwargs))
    async def mark_queued(self, category_id, item_id, unit_key, *, outcome=None):
        self.queued.append((category_id, item_id, unit_key, dict(outcome or {})))
    async def upsert(self, **kwargs):
        self.upserts.append(dict(kwargs)); return len(self.upserts)


class FakeDb:
    def __init__(self, repo):
        self.release_watches = repo


class FakeDownloader:
    def __init__(self, active=None, recent=None):
        self._active = active or []
        self._recent = recent or []
    async def get_active_downloads(self):
        return list(self._active)
    async def get_recent_downloads(self, limit=250):
        return list(self._recent)


class FakePipeline:
    async def run_discovery(self, item, episode_label=None, force=False, language=""):
        return True


class TvWorkflowProbe(TvWorkflowMixin):
    category_id = "tv"

    @staticmethod
    def _unit_coordinates(value):
        import re
        match = re.search(r"S(\d{2})E(\d{2})", str(value or ""), re.I)
        return (int(match.group(1)), int(match.group(2))) if match else (None, None)

    @staticmethod
    def _tv_language_status(text, preferred_language):
        return "preferred"

    @staticmethod
    def _candidate_language_status(text, preferred_language):
        return "preferred"

    @staticmethod
    def _metadata_mapping(value):
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _release_watch_requirements_from_item(item, context, preferred_language):
        return {"preferred_language": preferred_language, "auto_download": True}

    async def _notify_release(self, *args, **kwargs):
        return None

    def release_watch_notification_action(self, *args, **kwargs):
        return {}

    def unit_descriptor_from_agent_args(self, *, season=None, episode=None, **_):
        label = f"S{int(season):02d}E{int(episode):02d}" if season and episode else ""
        return {"stable_key": label, "label": label, "coordinates": {"season": season, "episode": episode}}

    def torrent_bundle_candidate_context(self, *args, **kwargs):
        return None

    @staticmethod
    def _is_frontier_episode(downloaded, season, episode):
        return True

    def create_item(self, title, language="English"):
        return SimpleNamespace(key=title, language=language, metadata={})


async def test_import_completion_marks_matching_release_watch_complete() -> None:
    repo = FakeReleaseRepo()
    handler = DownloadCompletionHandler(
        downloader=SimpleNamespace(),
        librarian=SimpleNamespace(),
        notifications=SimpleNamespace(),
        db=FakeDb(repo),
    )
    item = SimpleNamespace(
        category_id="tv",
        item_id="The Boys",
        item_name="The Boys",
        import_context=SimpleNamespace(unit_descriptor={"stable_key": "S01E01", "label": "S01E01"}),
        unit_descriptor={"stable_key": "S01E01", "label": "S01E01"},
        season=1,
        episode=1,
    )
    await handler._complete_release_watches_for_imported_unit(item)
    assert ("tv", "The Boys", "S01E01") in repo.completed


async def test_stale_queued_watch_returns_to_retryable_when_download_vanishes() -> None:
    repo = FakeReleaseRepo(queued=[{"category_id": "tv", "item_id": "The Boys", "unit_key": "S01E02", "interval_hours": 2.0}])
    sched = SimpleNamespace(_downloader=FakeDownloader(active=[], recent=[]))
    await MediaScheduler._recover_stale_queued_release_watches(sched, repo)
    assert repo.retryable and repo.retryable[0][:3] == ("tv", "The Boys", "S01E02")


async def test_stale_queued_watch_completes_when_matching_complete_download_exists() -> None:
    repo = FakeReleaseRepo(queued=[{"category_id": "tv", "item_id": "The Boys", "unit_key": "S01E03", "interval_hours": 2.0}])
    complete_download = SimpleNamespace(
        category_id="tv",
        item_id="The Boys",
        item_name="The Boys",
        status=DownloadStatus.COMPLETE,
        unit_descriptor={"stable_key": "S01E03"},
        import_context=SimpleNamespace(unit_descriptor={"stable_key": "S01E03"}),
    )
    sched = SimpleNamespace(_downloader=FakeDownloader(active=[], recent=[complete_download]))
    await MediaScheduler._recover_stale_queued_release_watches(sched, repo)
    assert repo.completed == [("tv", "The Boys", "S01E03")]
    assert not repo.retryable


async def test_tv_release_event_marks_auto_queue_as_queued_not_completed() -> None:
    repo = FakeReleaseRepo()
    workflow = TvWorkflowProbe()
    item = SimpleNamespace(key="The Boys", language="English", auto_download=True, metadata={})
    context = SimpleNamespace(
        db=FakeDb(repo),
        settings=SimpleNamespace(language="English", auto_download=True),
        pipeline=FakePipeline(),
    )
    event = {"unit_label": "S01E01", "source_result": {"title": "The Boys S01E01", "magnet": "magnet:?xt=urn:btih:abc"}}
    result = await workflow.handle_release_event(item, event, context, notifications=None, lifecycle=None)
    assert result["status"] == "queued"
    assert repo.queued == [("tv", "The Boys", "S01E01", {"status": "queued_by_tv_release_event", "trigger": "release_event"})]
    assert not repo.completed


async def main() -> None:
    await test_import_completion_marks_matching_release_watch_complete()
    await test_stale_queued_watch_returns_to_retryable_when_download_vanishes()
    await test_stale_queued_watch_completes_when_matching_complete_download_exists()
    await test_tv_release_event_marks_auto_queue_as_queued_not_completed()
    print("round216 release-watch completion/recovery tests: PASS")


if __name__ == "__main__":
    asyncio.run(main())
