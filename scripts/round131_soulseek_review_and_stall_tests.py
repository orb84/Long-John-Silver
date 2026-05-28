"""Round 131 Soulseek review/stall automation tests."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Lightweight stubs for optional modules imported transitively.
if "src.ai.manager" not in sys.modules:
    mod = types.ModuleType("src.ai.manager")
    class AIManager:  # pragma: no cover - simple stub
        pass
    mod.AIManager = AIManager
    sys.modules["src.ai.manager"] = mod

from src.ai.tools.soulseek import SearchSoulseekTool
from src.core.download_health import DownloadHealthSupervisor
from src.core.models import DownloadItem, DownloadPriority, DownloadStatus, Settings, SoulseekSettings
from src.integrations.slskd_client import SlskdClient


def test_slskd_normalization_filters_and_sorts() -> None:
    payload = {
        "responses": [
            {
                "username": "busy_user",
                "hasFreeUploadSlot": False,
                "queueLength": 4,
                "files": [{"filename": "Artist/Album/track.flac", "size": 1234, "bitrate": 900}],
                "lockedFiles": [{"filename": "Artist/Album/private.flac", "size": 999}],
            },
            {
                "username": "free_user",
                "hasFreeUploadSlot": True,
                "queueLength": 0,
                "files": [
                    {"filename": "Artist/Album/track.flac", "size": 1234, "bitrate": 800},  # duplicate path but different user -> allowed
                    {"filename": "Artist/Album/private2.flac", "isPrivate": True, "size": 777},
                ],
            },
            {
                "username": "free_user",
                "hasFreeUploadSlot": True,
                "queueLength": 0,
                "files": [{"filename": "Artist/Album/track.flac", "size": 1234, "bitrate": 800}],
            },
        ]
    }
    candidates, stats = SlskdClient.normalize_search_payload_detailed(payload)
    assert len(candidates) == 2, candidates
    assert candidates[0].username == "free_user", candidates
    assert stats.filtered_locked == 1, stats
    assert stats.filtered_private == 1, stats
    assert stats.filtered_duplicates == 1, stats


class _DummySettingsManager:
    def __init__(self) -> None:
        self.settings = Settings()
        self.settings.soulseek = SoulseekSettings(enabled=True, api_key="abc", soulseek_username="captain", soulseek_password="pw", account_status="ready")


async def test_search_tool_adds_filtering_note_async() -> None:
    sm = _DummySettingsManager()
    tool = SearchSoulseekTool(settings_manager=sm)
    original = SlskdClient.search

    async def fake_search(self, query, *, timeout_seconds=None, max_results=None):
        return {"ok": True, "candidates": [{"username": "u", "filename": "f.mp3"}]}

    SlskdClient.search = fake_search
    try:
        result = await tool.execute({"query": "kind of blue", "category_id": "music"}, context=None)  # type: ignore[arg-type]
    finally:
        SlskdClient.search = original
    assert result["ok"] is True
    assert any("filtered out" in note.lower() for note in result.get("search_notes", [])), result


class _DummyNotifications:
    def __init__(self) -> None:
        self.messages = []

    async def send_message(self, message: str, title: str = "", level: str = "info") -> None:
        self.messages.append({"title": title, "message": message, "level": level})


class _DummyDownloader:
    def __init__(self, item: DownloadItem) -> None:
        self.item = item

    async def reconcile_completed_downloads(self, limit: int = 100) -> int:
        return 0

    async def get_active_downloads(self):
        return [self.item]

    async def start_health_test(self, download_id: str, temporary_priority: DownloadPriority = DownloadPriority.HIGH):
        return None


class _DummyDB:
    pass


async def test_stalled_download_can_trigger_soulseek_companion_async() -> None:
    settings = Settings()
    settings.soulseek = SoulseekSettings(enabled=True, api_key="abc", soulseek_username="captain", soulseek_password="pw")
    settings.soulseek.search_enabled_categories = ["music"]

    class _SM:
        def __init__(self, settings):
            self.settings = settings

    item = DownloadItem(
        id="dl1",
        item_name="Kind of Blue",
        magnet="magnet:?xt=urn:btih:123",
        status=DownloadStatus.STALLED,
        priority=DownloadPriority.NORMAL,
        category_id="music",
        downloaded_bytes=0,
        progress=0.15,
        created_at=datetime.now(timezone.utc),
    )
    notifications = _DummyNotifications()
    supervisor = DownloadHealthSupervisor(
        settings_manager=_SM(settings),
        db=_DummyDB(),
        downloader=_DummyDownloader(item),
        pipeline=None,
        notifications=notifications,
    )

    original = SlskdClient.search
    async def fake_search(self, query, *, timeout_seconds=None, max_results=None):
        return {
            "ok": True,
            "candidates": [
                {"username": "free_user", "filename": "Miles Davis - Kind of Blue/01 - So What.flac", "size_bytes": 1000, "has_free_upload_slot": True, "queue_length": 0},
            ],
            "filtering": {"locked_or_private_filtered": 2},
        }
    SlskdClient.search = fake_search
    try:
        counters = await supervisor.run_once()
    finally:
        SlskdClient.search = original
    assert counters["soulseek_hits"] == 1, counters
    assert notifications.messages, "Expected a Soulseek notification"
    assert "Soulseek" in notifications.messages[0]["title"]
    assert "Filtered out 2 locked/private" in notifications.messages[0]["message"]


def test_prompt_builder_mentions_source_strategy() -> None:
    text = (ROOT / "src/ai/prompt_builder.py").read_text(encoding="utf-8")
    assert "prefer Soulseek first for single tracks" in text
    assert "prefer torrents first for full discographies" in text


def main() -> None:
    test_slskd_normalization_filters_and_sorts()
    asyncio.run(test_search_tool_adds_filtering_note_async())
    asyncio.run(test_stalled_download_can_trigger_soulseek_companion_async())
    test_prompt_builder_mentions_source_strategy()
    print("Round 131 Soulseek review/stall tests passed")


if __name__ == "__main__":
    main()
