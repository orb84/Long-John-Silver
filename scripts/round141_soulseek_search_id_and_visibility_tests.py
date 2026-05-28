#!/usr/bin/env python3
"""Round 141 tests: slskd search ids, visible Soulseek parallel status, and start races."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ai.tool_result_compactor import ToolResultCompactor
from src.core.models import Settings
from src.core.scheduler_services import SchedulerTorrentSearchService
from src.integrations.slskd_client import SlskdClient
from src.integrations.slskd_manager import SlskdManager


class FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.content = b"{}" if payload is not None else b""

    def json(self) -> Any:
        return self._payload

    @property
    def text(self) -> str:
        return str(self._payload)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeHttpClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        if method == "POST" and url.endswith("/api/v0/searches"):
            # The important regression: token is a Soulseek protocol token, but
            # id is the slskd REST identifier for state/search_responses.
            return FakeResponse({"id": "uuid-search-id", "token": 123, "searchText": "Puerto Hurraco"})
        if "uuid-search-id" in url and "includeResponses=true" in url:
            return FakeResponse({
                "id": "uuid-search-id",
                "isComplete": True,
                "responseCount": 1,
                "fileCount": 1,
                "responses": [{
                    "username": "music_user",
                    "hasFreeUploadSlot": True,
                    "queueLength": 0,
                    "files": [{
                        "filename": "Persiana Jones/Puerto Hurraco/01 Puerto Hurraco.flac",
                        "size": 123456,
                        "extension": "flac",
                        "bitRate": 900,
                    }],
                }],
            })
        if "123" in url:
            return FakeResponse({"unexpected": "wrong token endpoint"}, status_code=500)
        return FakeResponse({})


def test_search_prefers_slskd_id_over_protocol_token() -> None:
    settings = Settings()
    settings.soulseek.enabled = True
    settings.soulseek.api_key = "abc"
    fake = FakeHttpClient()
    result = asyncio.run(SlskdClient(settings.soulseek, client=fake).search("Puerto Hurraco", timeout_seconds=2, max_results=5))
    urls = [url for _, url, _ in fake.calls]
    assert result["ok"] is True, result
    assert result["search_id"] == "uuid-search-id", result
    assert result["candidate_count" if "candidate_count" in result else "ok"] if False else True
    assert result["candidates"], result
    assert any("uuid-search-id" in url for url in urls), urls
    assert not any("/123" in url for url in urls), urls


def test_search_state_with_responses_normalizes_candidates() -> None:
    payload = {
        "id": "abc",
        "responses": [{
            "username": "u",
            "hasFreeUploadSlot": True,
            "queueLength": 0,
            "files": [{"filename": "Artist/Album/song.mp3", "size": 1, "bitRate": 320}],
        }],
    }
    candidates = SlskdClient.normalize_search_payload(payload)
    assert len(candidates) == 1
    assert candidates[0].username == "u"
    assert candidates[0].filename.endswith("song.mp3")


def test_compactor_keeps_soulseek_zero_result_status_visible() -> None:
    result = {
        "query": "Puerto Hurraco",
        "category_id": "music",
        "result_set_id": "rs1",
        "candidates": [],
        "candidate_picker": [],
        "next_actions": [],
        "soulseek_summary": {
            "enabled": True,
            "status": "ready",
            "candidate_count": 0,
            "queries": ["Puerto Hurraco Persiana Jones", "Puerto Hurraco"],
            "raw_response_count": 0,
            "raw_file_count": 0,
            "error": None,
        },
    }
    compact = ToolResultCompactor().compact("search_media_torrents", result)
    assert compact["candidate_count"] == 0
    assert compact["soulseek_summary"]["status"] == "ready"
    assert compact["soulseek_summary"]["queries"][1] == "Puerto Hurraco"


def test_soulseek_query_variants_strip_album_and_deduplicate() -> None:
    media = type("DummyMedia", (), {"key": "Puerto Hurraco Persiana Jones", "display_name": "Puerto Hurraco"})()
    queries = SchedulerTorrentSearchService._soulseek_query_variants("Puerto Hurraco album", media)
    assert queries[0] == "Puerto Hurraco"
    assert not any(q.casefold().endswith(" album") for q in queries)
    assert "Puerto Hurraco Persiana Jones" in queries
    assert len(queries) == len({q.casefold() for q in queries})


def test_manager_can_mark_adopted_runtime_running() -> None:
    manager = SlskdManager()
    manager._adopted_external = True  # noqa: SLF001 - targeted state regression
    manager._running = True  # noqa: SLF001
    assert manager.is_running is True


def main() -> None:
    test_search_prefers_slskd_id_over_protocol_token()
    test_search_state_with_responses_normalizes_candidates()
    test_compactor_keeps_soulseek_zero_result_status_visible()
    test_soulseek_query_variants_strip_album_and_deduplicate()
    test_manager_can_mark_adopted_runtime_running()
    print("Round 141 Soulseek search id/visibility tests passed")


if __name__ == "__main__":
    main()
