#!/usr/bin/env python3
"""Round 145 Soulseek materialized-response and diagnostics tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from typing import Any
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.models import Settings
from src.integrations.slskd_client import SlskdClient


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


class MaterializedResponsesClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        if method == "POST" and url.endswith("/api/v0/searches"):
            payload = kwargs.get("json") or {}
            assert payload.get("filterResponses") is False
            assert "fileLimit" in payload
            return FakeResponse({"id": "rest-id", "token": 777, "searchText": payload.get("searchText")})
        if "/api/v0/searches/rest-id/responses" in url:
            return FakeResponse([
                {
                    "username": "baid.ttack",
                    "hasFreeUploadSlot": True,
                    "queueLength": 0,
                    "files": {
                        "Persiana Jones - 1999 - Puerto Hurraco/P Albums/music": [
                            {"filename": "01,Un Giorno Nuovo.mp3", "size": 2948155, "extension": "mp3", "bitRate": 128},
                            {"filename": "02,Tremarella.mp3", "size": 2687495, "extension": "mp3", "bitRate": 128},
                            {"filename": "cover.jpg", "size": 106256, "extension": "jpg"},
                        ]
                    },
                }
            ])
        if "/api/v0/searches/rest-id" in url:
            # This is the exact failure shape from the user's logs: raw counts
            # exist, but the state response carries an empty responses array.
            return FakeResponse({
                "id": "rest-id",
                "isComplete": True,
                "responseCount": 1,
                "fileCount": 3,
                "responses": [],
                "searchText": "Puerto Hurraco Persiana Jones",
            })
        return FakeResponse({})


def test_empty_state_responses_falls_back_to_materialized_responses() -> None:
    settings = Settings()
    settings.soulseek.enabled = True
    settings.soulseek.api_key = "abc"
    fake = MaterializedResponsesClient()
    result = asyncio.run(SlskdClient(settings.soulseek, client=fake).search("Puerto Hurraco Persiana Jones", timeout_seconds=2, max_results=10))
    assert result["ok"] is True, result
    assert result["raw_file_count"] >= 3, result
    assert result["candidates"], result
    folder = result["candidates"][0]
    assert folder["candidate_type"] == "folder", folder
    assert folder["audio_file_count"] == 2, folder
    assert any(name.endswith("cover.jpg") for name in folder["filenames"]), folder


class TokenOnlyResponsesClient:
    async def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        if method == "POST" and url.endswith("/api/v0/searches"):
            return FakeResponse({"id": "rest-id", "token": 888, "searchText": "Puerto Hurraco"})
        if "/api/v0/searches/rest-id/responses" in url:
            return FakeResponse([], 200)
        if "/api/v0/searches/888/responses" in url:
            return FakeResponse([{"username": "legacy", "files": [{"filename": "Artist/Album/01.mp3", "extension": "mp3", "size": 1}]}])
        if "/api/v0/searches/rest-id" in url:
            return FakeResponse({"id": "rest-id", "token": 888, "isComplete": True, "responseCount": 1, "fileCount": 1, "responses": []})
        return FakeResponse({})


def test_protocol_token_is_fallback_only_when_rest_responses_are_empty() -> None:
    settings = Settings()
    settings.soulseek.enabled = True
    settings.soulseek.api_key = "abc"
    result = asyncio.run(SlskdClient(settings.soulseek, client=TokenOnlyResponsesClient()).search("Puerto Hurraco", timeout_seconds=2, max_results=5))
    assert result["ok"] is True, result
    assert result["candidates"], result
    assert result["candidates"][0]["username"] == "legacy", result


class ImpossibleShapeClient:
    async def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        if method == "POST" and url.endswith("/api/v0/searches"):
            return FakeResponse({"id": "bad-id", "token": 999})
        if "/api/v0/searches/bad-id" in url:
            return FakeResponse({"id": "bad-id", "isComplete": True, "responseCount": 2, "fileCount": 17, "responses": []})
        return FakeResponse([])


def test_raw_counts_without_parse_write_diagnostic_dump() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings()
        settings.soulseek.enabled = True
        settings.soulseek.api_key = "abc"
        settings.soulseek.app_dir = tmp
        result = asyncio.run(SlskdClient(settings.soulseek, client=ImpossibleShapeClient()).search("Puerto Hurraco", timeout_seconds=1, max_results=5))
        assert result["ok"] is True, result
        assert result["raw_file_count"] == 17, result
        assert not result["candidates"], result
        dump = Path(result["diagnostic_dump"])
        assert dump.exists(), result
        text = dump.read_text(encoding="utf-8")
        assert "Puerto Hurraco" in text
        assert "responses_shape" in text


def main() -> None:
    test_empty_state_responses_falls_back_to_materialized_responses()
    test_protocol_token_is_fallback_only_when_rest_responses_are_empty()
    test_raw_counts_without_parse_write_diagnostic_dump()
    print("Round 145 Soulseek materialized-response tests passed")


if __name__ == "__main__":
    main()
