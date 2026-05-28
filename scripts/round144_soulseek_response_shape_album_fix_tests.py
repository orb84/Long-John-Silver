#!/usr/bin/env python3
"""Round 144 Soulseek response-shape and album-folder regression tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ai.tools.soulseek import SearchSoulseekTool
from src.core.models import Settings, SoulseekSettings, ToolExecutionContext
from src.integrations.slskd_client import SlskdClient


def _album_payload() -> dict:
    return {
        "id": "search-id",
        "responseCount": 1,
        "fileCount": 16,
        "responses": {
            "baid.ttack": {
                "hasFreeUploadSlot": True,
                "queueLength": 0,
                "uploadSpeed": 2291,
                "files": {
                    "Persiana Jones - 1999 - Puerto Hurraco/P Albums/music": [
                        {"filename": "01,Un Giorno Nuovo.mp3", "size": 2948155, "extension": "mp3", "bitRate": 128},
                        {"filename": "02,Tremarella.mp3", "size": 2687495, "extension": "mp3", "bitRate": 128},
                        {"filename": "03,Spacco Tutto.mp3", "size": 2744816, "extension": "mp3", "bitRate": 128},
                        {"filename": "cover.jpg", "size": 106256, "extension": "jpg"},
                    ]
                },
            }
        },
    }


def test_slskd_username_folder_map_normalizes_album_candidate() -> None:
    candidates, stats = SlskdClient.normalize_search_payload_detailed(_album_payload())
    assert stats.total_file_rows == 4, stats
    assert len(candidates) == 4, candidates
    assert candidates[0].username == "baid.ttack"
    assert "Puerto Hurraco" in candidates[0].filename
    public = SlskdClient._public_candidates(candidates, 10, query="Puerto Hurraco Persiana Jones")
    folder = public[0]
    assert folder["candidate_type"] == "folder", public
    assert folder["folder_relevance"] == "strong", folder
    assert folder["audio_file_count"] == 3, folder
    assert any(name.endswith("cover.jpg") for name in folder["filenames"]), folder
    assert all("album" not in q.casefold().split() for q in ["Puerto Hurraco Persiana Jones"])


def test_slskd_compact_username_list_normalizes() -> None:
    payload = {
        "responses": {
            "salvadrea": [
                {"filename": "VA_LETS_SKA_COMPILATION_2002_D/Music/drop/05_PERSIANA_JONES_-_ORE_E_G.MP3", "size": 4480000, "extension": "mp3"}
            ]
        }
    }
    candidates, stats = SlskdClient.normalize_search_payload_detailed(payload)
    assert stats.total_file_rows == 1
    assert len(candidates) == 1
    assert candidates[0].username == "salvadrea"
    assert candidates[0].filename.endswith("ORE_E_G.MP3")


class _SM:
    def __init__(self) -> None:
        self.settings = Settings()
        self.settings.soulseek = SoulseekSettings(enabled=True, api_key="abc", soulseek_username="captain", soulseek_password="pw")
        self.settings.soulseek.account_status = "ready"

    def save(self, _settings):
        pass


async def test_direct_music_search_retries_clean_variants() -> None:
    sm = _SM()
    tool = SearchSoulseekTool(settings_manager=sm)
    calls: list[str] = []
    original = SlskdClient.search

    async def fake_search(self, query, *, timeout_seconds=None, max_results=None):
        calls.append(str(query))
        if str(query).casefold() == "persiana jones puerto hurraco":
            return {"ok": True, "candidates": [{"candidate_type": "folder", "username": "baid.ttack", "filename": "Persiana Jones - 1999 - Puerto Hurraco"}]}
        return {"ok": True, "candidates": [], "raw_response_count": 1, "raw_file_count": 16}

    SlskdClient.search = fake_search
    try:
        result = await tool.execute({"query": "album Puerto Hurraco from Persiana Jones", "category_id": "music"}, ToolExecutionContext(session_id="test"))
    finally:
        SlskdClient.search = original
    assert result["ok"] is True
    assert result["candidates"], result
    assert "Persiana Jones Puerto Hurraco" in calls, calls
    assert not any(call.casefold().startswith("album ") for call in calls), calls


def main() -> None:
    test_slskd_username_folder_map_normalizes_album_candidate()
    test_slskd_compact_username_list_normalizes()
    asyncio.run(test_direct_music_search_retries_clean_variants())
    print("Round 144 Soulseek response-shape/album tests passed")


if __name__ == "__main__":
    main()
