#!/usr/bin/env python3
"""Round 142 Soulseek album-result normalization and missed-search retry tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import types
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Some tool imports transitively refer to optional AI manager/runtime modules.
if "src.ai.manager" not in sys.modules:
    mod = types.ModuleType("src.ai.manager")
    class AIManager:  # pragma: no cover - stub
        pass
    mod.AIManager = AIManager
    sys.modules["src.ai.manager"] = mod

from src.ai.tools.scheduling import SearchMediaTorrentsTool
from src.core.models import Settings, ToolExecutionContext
from src.core.scheduler_services import SchedulerTorrentSearchService
from src.integrations.slskd_client import SlskdClient


def test_nested_slskd_search_state_with_album_folder_normalizes() -> None:
    payload = {
        "id": "search-id",
        "responseCount": 1,
        "fileCount": 3,
        "responses": [
            {
                "username": "baitattack",
                "hasFreeUploadSlot": True,
                "queueLength": 0,
                "uploadSpeed": 2291200,
                "folder": "Persiana Jones - 1999 - Puerto Hurraco/P/Albums/music",
                "files": [
                    {"filename": "01,Un Giorno Nuovo.mp3", "size": 2948155, "extension": "mp3", "bitRate": 128, "length": 177},
                    {"filename": "02,Tremarella.mp3", "size": 2687495, "extension": "mp3", "bitRate": 128, "length": 161},
                    {"filename": "cover.jpg", "size": 106256, "extension": "jpg"},
                ],
            }
        ],
    }
    candidates, stats = SlskdClient.normalize_search_payload_detailed(payload)
    assert stats.total_file_rows == 3
    assert len(candidates) == 3
    assert candidates[0].username == "baitattack"
    assert candidates[0].filename.startswith("Persiana Jones - 1999 - Puerto Hurraco/")


def test_public_candidates_include_album_folder_before_tracks() -> None:
    payload = {
        "responses": [
            {
                "username": "baitattack",
                "hasFreeUploadSlot": True,
                "queueLength": 0,
                "folder": "Persiana Jones - 1999 - Puerto Hurraco/P/Albums/music",
                "files": [
                    {"filename": "01,Un Giorno Nuovo.mp3", "size": 2948155, "extension": "mp3"},
                    {"filename": "02,Tremarella.mp3", "size": 2687495, "extension": "mp3"},
                    {"filename": "03,Spacco Tutto.mp3", "size": 2744816, "extension": "mp3"},
                ],
            }
        ]
    }
    candidates, _ = SlskdClient.normalize_search_payload_detailed(payload)
    public = SlskdClient._public_candidates(candidates, limit=10)  # noqa: SLF001 - regression target
    assert public[0]["candidate_type"] == "folder"
    assert public[0]["file_count"] == 3
    assert len(public[0]["filenames"]) == 3
    assert public[0]["folder"].endswith("Albums/music")


def test_music_soulseek_queries_remove_album_and_try_artist_album_permutations() -> None:
    media = type("DummyMedia", (), {"key": "Puerto Hurraco Persiana Jones", "display_name": "Puerto Hurraco"})()
    queries = SchedulerTorrentSearchService._soulseek_query_variants("album Puerto Hurraco from Persiana Jones", media)
    folded = {q.casefold() for q in queries}
    assert "album" not in queries[0].casefold()
    assert "persiana jones puerto hurraco" in folded
    assert "puerto hurraco persiana jones" in folded
    assert "puerto hurraco" in folded


class _DummyPromptScheduler:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    async def list_tasks(self, user_id=None):
        return []

    async def create_task(self, **kwargs):
        self.created.append(kwargs)
        return type("Task", (), {"id": "retry123", "interval_minutes": kwargs["interval_minutes"], "max_runs": kwargs["max_runs"]})()


class _DummySettingsManager:
    def __init__(self) -> None:
        self.settings = Settings()
        self.settings.soulseek.enabled = True
        self.settings.soulseek.auto_retry_unmatched_searches = True
        self.settings.soulseek.retry_search_interval_minutes = 360
        self.settings.soulseek.retry_search_max_runs = 12


async def test_unmatched_search_schedules_recurring_retry() -> None:
    scheduler = type("DummyScheduler", (), {})()
    scheduler._prompt_scheduler = _DummyPromptScheduler()
    scheduler._settings_manager = _DummySettingsManager()
    tool = SearchMediaTorrentsTool(scheduler=scheduler)  # type: ignore[arg-type]
    res = {
        "candidate_count": 0,
        "companion_soulseek": {"enabled": True, "status": "ready", "candidate_count": 0},
    }
    await tool._maybe_schedule_unmatched_retry(  # noqa: SLF001 - targeted regression
        res=res,
        name="Puerto Hurraco Persiana Jones",
        category_id="music",
        search_scope="default",
        context=ToolExecutionContext(user_id="u", session_id="s", source="web"),
    )
    assert res["deferred_search_retry"]["scheduled"] is True
    assert scheduler._prompt_scheduler.created
    prompt = scheduler._prompt_scheduler.created[0]["prompt"]
    assert "without words like album" in prompt
    assert scheduler._prompt_scheduler.created[0]["interval_minutes"] == 360


def main() -> None:
    test_nested_slskd_search_state_with_album_folder_normalizes()
    test_public_candidates_include_album_folder_before_tracks()
    test_music_soulseek_queries_remove_album_and_try_artist_album_permutations()
    asyncio.run(test_unmatched_search_schedules_recurring_retry())
    print("Round 142 Soulseek album-result/retry tests passed")


if __name__ == "__main__":
    main()
