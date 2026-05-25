"""Round 48 regressions: dirty library names and queue-link fallback."""

from pathlib import Path

import pytest

from src.ai.tools.queue_download_support import QueueDownloadRequest, QueueDownloadService
from src.core.categories.movie import MovieCategory
from src.core.categories.tv import TvShowCategory


@pytest.mark.asyncio
async def test_tv_scan_cleans_dirty_release_folder_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    tv_root = tmp_path / "TV Shows"
    tv_root.mkdir()
    show_dir = tv_root / "Silicon.Valley.S01-06.ITA.DLMUX.x264-mkeagle3"
    show_dir.mkdir()
    episode = show_dir / "Silicon.Valley.S01E01.ITA.1080p.x264.mkv"
    episode.write_text("dummy video")

    category = TvShowCategory()
    monkeypatch.setattr(category, "detect_language", lambda *args, **kwargs: _async_value("Italian"))

    scanned = await category.scan(str(tv_root))

    assert len(scanned) == 1
    assert scanned[0].name == "Silicon Valley"
    assert scanned[0].episodes == {1: [1]}


@pytest.mark.asyncio
async def test_movie_scan_cleans_camelcase_folder_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    movie_root = tmp_path / "Movies"
    movie_root.mkdir()
    movie_dir = movie_root / "BrisbyAndSecretOfNimh"
    movie_dir.mkdir()
    movie_file = movie_dir / "BrisbyAndSecretOfNimh.mkv"
    movie_file.write_text("dummy video")

    category = MovieCategory()
    monkeypatch.setattr(category, "detect_language", lambda *args, **kwargs: _async_value("English"))

    scanned = await category.scan(str(movie_root))

    assert len(scanned) == 1
    assert scanned[0].name == "Brisby And Secret Of Nimh"
    assert scanned[0].file_count == 1


def test_metadata_merge_promotes_provider_title_and_artwork():
    category = MovieCategory()
    payload = {
        "item_id": "BrisbyAndSecretOfNimh",
        "display_name": "Brisby And Secret Of Nimh",
    }

    category.merge_display_metadata(payload, {
        "display_name": "The Secret of NIMH",
        "poster_path": "/poster.jpg",
    })

    assert payload["display_name"] == "The Secret of NIMH"
    assert payload["metadata_display_name"] == "The Secret of NIMH"
    assert payload["poster_url"] == "https://image.tmdb.org/t/p/w500/poster.jpg"


@pytest.mark.asyncio
async def test_queue_download_falls_back_to_same_episode_candidate():
    scheduler = _FallbackScheduler(failing_magnet="bad-link")
    service = QueueDownloadService(scheduler)
    request = _request()
    entries = [{
        "candidate_id": "bad-e04",
        "candidate": {
            "candidate_id": "bad-e04",
            "title": "Show.S05E04.bad",
            "magnet": "bad-link",
            "season": 5,
            "episode": 4,
            "source": "jackett",
        },
        "cache_data": {
            "name": "For All Mankind",
            "category_id": "tv",
            "candidates": [
                {
                    "candidate_id": "bad-e04",
                    "title": "Show.S05E04.bad",
                    "magnet": "bad-link",
                    "season": 5,
                    "episode": 4,
                    "source": "jackett",
                },
                {
                    "candidate_id": "good-e04",
                    "title": "Show.S05E04.good",
                    "magnet": "good-link",
                    "season": 5,
                    "episode": 4,
                    "source": "jackett",
                },
                {
                    "candidate_id": "good-e05",
                    "title": "Show.S05E05.good",
                    "magnet": "other-good-link",
                    "season": 5,
                    "episode": 5,
                    "source": "jackett",
                },
            ],
        },
    }]

    result = await service._queue_resolved_entries(request, entries)

    assert result["queued_count"] == 1
    assert result["error_count"] == 0
    assert result["fallback_count"] == 1
    assert result["queued"][0]["candidate_id"] == "good-e04"
    assert result["queued"][0]["fallback_for_candidate_id"] == "bad-e04"
    assert scheduler.calls == ["bad-link", "good-link"]


@pytest.mark.asyncio
async def test_queue_download_reports_failed_unit_when_no_fallback():
    scheduler = _FallbackScheduler(failing_magnet="bad-link")
    service = QueueDownloadService(scheduler)
    request = _request()
    entries = [{
        "candidate_id": "bad-e04",
        "candidate": {
            "candidate_id": "bad-e04",
            "title": "Show.S05E04.bad",
            "magnet": "bad-link",
            "season": 5,
            "episode": 4,
            "source": "jackett",
        },
        "cache_data": {
            "name": "For All Mankind",
            "category_id": "tv",
            "candidates": [],
        },
    }, {
        "candidate_id": "good-e06",
        "candidate": {
            "candidate_id": "good-e06",
            "title": "Show.S05E06.good",
            "magnet": "good-link",
            "season": 5,
            "episode": 6,
            "source": "jackett",
        },
        "cache_data": {"name": "For All Mankind", "category_id": "tv", "candidates": []},
    }]

    result = await service._queue_resolved_entries(request, entries)

    assert result["queued_count"] == 1
    assert result["error_count"] == 1
    assert result["partial_failure"] is True
    assert result["errors"][0]["season"] == 5
    assert result["errors"][0]["episode"] == 4
    assert "404" in result["errors"][0]["error"]


async def _async_value(value):
    return value


class _FallbackScheduler:
    def __init__(self, failing_magnet: str):
        self.failing_magnet = failing_magnet
        self.calls: list[str] = []

    async def queue_download(self, **kwargs):
        magnet = kwargs.get("magnet")
        self.calls.append(magnet)
        if magnet == self.failing_magnet:
            raise RuntimeError("HTTP 404 while resolving provider link")
        return {"status": "queued", "download_id": f"queued-{len(self.calls)}"}


def _request() -> QueueDownloadRequest:
    return QueueDownloadRequest(
        session_id="test-session",
        magnet=None,
        name=None,
        season=None,
        episode=None,
        option_index=None,
        candidate_ids=[],
        result_set_id=None,
        category_id="tv",
        estimated_size_bytes=None,
        selected_torrent_title="",
        selected_source_seeders=None,
        requested_priority="high",
        raw_arguments={},
    )
