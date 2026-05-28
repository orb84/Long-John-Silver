#!/usr/bin/env python3
"""Round 147 Soulseek enqueue payload-shape regression tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ai.tools.soulseek import EnqueueSoulseekDownloadTool
from src.core.models import Settings, SoulseekSettings, ToolExecutionContext
from src.integrations.slskd_client import SlskdClient, SoulseekCandidate
from src.utils.candidate_ids import store_result_set


class _SystemStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set_preference(self, key: str, value: str) -> None:
        self.values[key] = value

    async def get_preference(self, key: str) -> str | None:
        return self.values.get(key)


class _Db:
    def __init__(self) -> None:
        self.system = _SystemStore()


class _SettingsManager:
    def __init__(self) -> None:
        self.settings = Settings()
        self.settings.soulseek = SoulseekSettings(
            enabled=True,
            api_key="abc",
            soulseek_username="ljs_test",
            soulseek_password="pw",
            account_status="ready",
            account_status_message="ready",
        )


def test_slskd_enqueue_posts_array_of_queue_download_requests() -> None:
    settings = SoulseekSettings(enabled=True, api_key="abc")
    client = SlskdClient(settings)
    seen: dict[str, Any] = {}

    async def fake_request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        seen["method"] = method
        seen["path"] = path
        seen["json"] = kwargs.get("json")
        return {"accepted": True}

    client._request = fake_request  # type: ignore[method-assign]
    result = asyncio.run(client.enqueue_download(
        username="baitattack",
        file_requests=[
            {"filename": "music/Albums/P/Persiana Jones - 1999 - Puerto Hurraco/01.mp3", "size": 123},
            {"filename": "music/Albums/P/Persiana Jones - 1999 - Puerto Hurraco/02.mp3", "size": 456},
        ],
    ))

    assert result["ok"] is True
    assert seen["method"] == "POST"
    assert seen["path"] == "/api/v0/transfers/downloads/baitattack"
    assert isinstance(seen["json"], list), seen["json"]
    assert seen["json"][0] == {"filename": "music/Albums/P/Persiana Jones - 1999 - Puerto Hurraco/01.mp3", "size": 123}
    assert result["file_count"] == 2


def test_public_folder_candidates_keep_file_requests_for_cache() -> None:
    rows = [
        SoulseekCandidate(username="u", filename="Artist/Album/01 - One.mp3", size_bytes=1000, extension="mp3"),
        SoulseekCandidate(username="u", filename="Artist/Album/02 - Two.mp3", size_bytes=2000, extension="mp3"),
    ]
    public = SlskdClient._public_candidates(rows, 5, query="Artist Album")
    folder = public[0]
    assert folder["candidate_type"] == "folder"
    assert "file_requests" in folder
    assert folder["file_requests"][0] == {"filename": "Artist/Album/01 - One.mp3", "size": 1000}


async def test_enqueue_tool_uses_cached_file_requests_and_no_success_note_on_failure() -> None:
    db = _Db()
    sm = _SettingsManager()
    candidate = {
        "candidate_id": "slskd-folder-147",
        "candidate_type": "folder",
        "username": "baitattack",
        "folder": "music/Albums/P/Persiana Jones - 1999 - Puerto Hurraco",
        "filename": "music/Albums/P/Persiana Jones - 1999 - Puerto Hurraco",
        "filenames": [
            "music/Albums/P/Persiana Jones - 1999 - Puerto Hurraco/01.mp3",
            "music/Albums/P/Persiana Jones - 1999 - Puerto Hurraco/02.mp3",
        ],
        "file_requests": [
            {"filename": "music/Albums/P/Persiana Jones - 1999 - Puerto Hurraco/01.mp3", "size": 111},
            {"filename": "music/Albums/P/Persiana Jones - 1999 - Puerto Hurraco/02.mp3", "size": 222},
        ],
        "audio_file_count": 2,
        "folder_relevance": "strong",
    }
    await store_result_set(db, session_id="web-test", cache_data={
        "result_set_id": "rs-soulseek-147",
        "name": "Puerto Hurraco Persiana Jones",
        "category_id": "music",
        "candidates": [],
        "companion_soulseek": {"status": "ready", "candidate_count": 1, "candidates": [candidate]},
    })

    seen: dict[str, Any] = {}
    original = SlskdClient.enqueue_download

    async def fake_enqueue(self, *, username: str, filename: str = "", filenames: list[str] | None = None, file_requests: list[dict[str, Any]] | None = None):
        seen["username"] = username
        seen["file_requests"] = file_requests or []
        return {"ok": False, "recoverable": True, "error_code": "SLSKD_HTTP_ERROR", "error": "bad"}

    SlskdClient.enqueue_download = fake_enqueue
    try:
        tool = EnqueueSoulseekDownloadTool(settings_manager=sm, database=db)
        result = await tool.execute({"candidate_id": "slskd-folder-147", "result_set_id": "rs-soulseek-147"}, ToolExecutionContext(session_id="web-test"))
    finally:
        SlskdClient.enqueue_download = original

    assert seen["username"] == "baitattack"
    assert seen["file_requests"][0]["size"] == 111
    assert result["ok"] is False
    assert "import_note" not in result
    assert "Do not retry" in result["next_actions"][0]


def main() -> None:
    test_slskd_enqueue_posts_array_of_queue_download_requests()
    test_public_folder_candidates_keep_file_requests_for_cache()
    asyncio.run(test_enqueue_tool_uses_cached_file_requests_and_no_success_note_on_failure())
    print("Round 147 Soulseek enqueue payload-shape tests passed")


if __name__ == "__main__":
    main()
