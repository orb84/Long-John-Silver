#!/usr/bin/env python3
"""Round 146 Soulseek candidate-id queueing regression tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ai.tools.soulseek import EnqueueSoulseekDownloadTool
from src.ai.tools.scheduling import _compact_soulseek_candidates
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


def test_public_candidates_have_stable_ids_and_folder_payloads() -> None:
    rows = [
        SoulseekCandidate(username="u", filename="Artist/Album/01 - One.mp3", size_bytes=1000, extension="mp3"),
        SoulseekCandidate(username="u", filename="Artist/Album/02 - Two.mp3", size_bytes=1000, extension="mp3"),
        SoulseekCandidate(username="u", filename="Artist/Album/cover.jpg", size_bytes=100, extension="jpg"),
    ]
    public = SlskdClient._public_candidates(rows, 10, query="Artist Album")
    folder = public[0]
    assert folder["candidate_type"] == "folder"
    assert folder["candidate_id"]
    assert folder["file_count"] == 3
    assert len(folder["filenames"]) == 3


def test_compact_candidates_keep_id_not_long_filename_array() -> None:
    compact = _compact_soulseek_candidates([
        {
            "candidate_id": "abc123",
            "candidate_type": "folder",
            "username": "u",
            "folder": "Artist/Album",
            "filename": "Artist/Album",
            "filenames": [f"Artist/Album/{i:02d}.mp3" for i in range(20)],
            "audio_filenames": [f"Artist/Album/{i:02d}.mp3" for i in range(20)],
            "audio_file_count": 20,
        }
    ], result_set_id="rs1")
    assert compact[0]["candidate_id"] == "abc123"
    assert compact[0]["enqueue_hint"] == {"tool": "enqueue_soulseek_download", "candidate_id": "abc123", "result_set_id": "rs1"}
    assert "filenames" not in compact[0]
    assert len(compact[0]["sample_filenames"]) <= 6


async def test_enqueue_resolves_candidate_id_from_cached_result_set() -> None:
    db = _Db()
    sm = _SettingsManager()
    candidate = {
        "candidate_id": "slskd-folder-1",
        "candidate_type": "folder",
        "username": "baitattack",
        "folder": "music/Albums/P/Persiana Jones - 1999 - Puerto Hurraco",
        "filename": "music/Albums/P/Persiana Jones - 1999 - Puerto Hurraco",
        "filenames": [
            "music/Albums/P/Persiana Jones - 1999 - Puerto Hurraco/01, Un Giorno Nuovo.mp3",
            "music/Albums/P/Persiana Jones - 1999 - Puerto Hurraco/02, Tremarella.mp3",
        ],
        "audio_file_count": 2,
        "folder_relevance": "strong",
    }
    await store_result_set(db, session_id="web-test", cache_data={
        "result_set_id": "rs-soulseek",
        "name": "Puerto Hurraco Persiana Jones",
        "category_id": "music",
        "candidates": [],
        "companion_soulseek": {"status": "ready", "candidate_count": 1, "candidates": [candidate]},
    })

    seen: dict[str, Any] = {}
    original = SlskdClient.enqueue_download

    async def fake_enqueue(self, *, username: str, filename: str = "", filenames: list[str] | None = None, file_requests: list[dict[str, Any]] | None = None):
        seen["username"] = username
        seen["filename"] = filename
        seen["filenames"] = filenames or []
        return {"ok": True, "source": "slskd", "username": username, "filename": filename, "filenames": filenames or []}

    SlskdClient.enqueue_download = fake_enqueue
    try:
        tool = EnqueueSoulseekDownloadTool(settings_manager=sm, database=db)
        assert tool.parameters().get("required") == []
        result = await tool.execute({"candidate_id": "slskd-folder-1", "result_set_id": "rs-soulseek"}, ToolExecutionContext(session_id="web-test"))
    finally:
        SlskdClient.enqueue_download = original

    assert result["ok"] is True
    assert seen["username"] == "baitattack"
    assert len(seen["filenames"]) == 2
    assert result["candidate_id"] == "slskd-folder-1"
    assert result["candidate_type"] == "folder"


async def test_enqueue_can_recover_from_username_only_when_cached() -> None:
    db = _Db()
    sm = _SettingsManager()
    candidate = {
        "candidate_id": "slskd-folder-2",
        "candidate_type": "folder",
        "username": "baitattack",
        "folder": "music/Albums/P/Persiana Jones - 1999 - Puerto Hurraco",
        "filename": "music/Albums/P/Persiana Jones - 1999 - Puerto Hurraco",
        "filenames": ["music/Albums/P/Persiana Jones - 1999 - Puerto Hurraco/01.mp3"],
        "audio_file_count": 1,
        "folder_relevance": "strong",
    }
    await store_result_set(db, session_id="web-test", cache_data={
        "result_set_id": "rs-latest",
        "name": "Puerto Hurraco Persiana Jones",
        "category_id": "music",
        "candidates": [],
        "companion_soulseek": {"status": "ready", "candidate_count": 1, "candidates": [candidate]},
    })
    original = SlskdClient.enqueue_download
    async def fake_enqueue(self, *, username: str, filename: str = "", filenames: list[str] | None = None, file_requests: list[dict[str, Any]] | None = None):
        return {"ok": True, "username": username, "filename": filename, "filenames": filenames or []}
    SlskdClient.enqueue_download = fake_enqueue
    try:
        tool = EnqueueSoulseekDownloadTool(settings_manager=sm, database=db)
        result = await tool.execute({"username": "baitattack"}, ToolExecutionContext(session_id="web-test"))
    finally:
        SlskdClient.enqueue_download = original
    assert result["ok"] is True
    assert result["candidate_id"] == "slskd-folder-2"


def main() -> None:
    test_public_candidates_have_stable_ids_and_folder_payloads()
    test_compact_candidates_keep_id_not_long_filename_array()
    asyncio.run(test_enqueue_resolves_candidate_id_from_cached_result_set())
    asyncio.run(test_enqueue_can_recover_from_username_only_when_cached())
    print("Round 146 Soulseek candidate-id queueing tests passed")


if __name__ == "__main__":
    main()
