#!/usr/bin/env python3
"""Round 137 Soulseek login detection and no-false-negative tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Lightweight stubs for optional imports pulled in by scheduler services.
if "src.ai.manager" not in sys.modules:
    mod = types.ModuleType("src.ai.manager")
    class AIManager:  # pragma: no cover
        pass
    mod.AIManager = AIManager
    sys.modules["src.ai.manager"] = mod

from src.ai.tools.soulseek import SearchSoulseekTool
from src.core.models import Settings, SoulseekSettings, ToolExecutionContext
from src.core.scheduler_services import SchedulerTorrentSearchService
from src.integrations.slskd_client import SlskdClient


def test_server_is_logged_in_state_is_recognized() -> None:
    payload = {
        "application": {"version": {"current": "0.25.1"}},
        "server": {
            "state": "Connected",
            "isConnected": True,
            "isLoggedIn": True,
            "isLoggingIn": False,
        },
    }
    result = SlskdClient.interpret_connection_payload(payload)
    assert result["authenticated_to_soulseek"] is True, result
    assert result["credentials_rejected"] is False


def test_server_not_logged_in_is_not_treated_as_bad_credentials() -> None:
    payload = {
        "server": {
            "state": "Disconnected",
            "isConnected": False,
            "isLoggedIn": False,
            "isLoggingIn": False,
        },
    }
    result = SlskdClient.interpret_connection_payload(payload)
    assert result["authenticated_to_soulseek"] is False
    assert result["credentials_rejected"] is False, result


class DummySettingsManager:
    def __init__(self) -> None:
        self.settings = Settings()
        self.saved = False

    def save(self, settings: Settings) -> None:
        self.saved = True


async def test_search_tool_probes_when_account_status_is_only_checking() -> None:
    sm = DummySettingsManager()
    sm.settings.soulseek = SoulseekSettings(
        enabled=True,
        api_key="abc",
        soulseek_username="captain",
        soulseek_password="secret",
        account_status="checking",
        account_status_message="not confirmed yet",
    )
    called = {"count": 0}
    original = SlskdClient.search

    async def fake_search(self, query, *, timeout_seconds=None, max_results=None):
        called["count"] += 1
        return {"ok": True, "query": query, "source": "slskd", "candidates": []}

    SlskdClient.search = fake_search
    try:
        result = await SearchSoulseekTool(settings_manager=sm).execute(
            {"query": "Persiana Jones Puerto Hurraco", "category_id": "music"},
            ToolExecutionContext(session_id="test"),
        )
    finally:
        SlskdClient.search = original

    assert called["count"] >= 1
    assert result["ok"] is True
    assert sm.settings.soulseek.account_status == "ready"
    assert sm.saved is True


async def test_parallel_companion_search_does_not_short_circuit_on_checking_status() -> None:
    settings = Settings()
    settings.soulseek = SoulseekSettings(
        enabled=True,
        api_key="abc",
        soulseek_username="captain",
        soulseek_password="secret",
        account_status="checking",
        account_status_message="not confirmed yet",
    )

    class Context:
        settings_manager = type("SM", (), {"settings": settings})()
        db = None
        downloader = None
        pipeline = None
        aggregator = None
        categories = None
        tvmaze = None
        metadata_enricher = None

    service = SchedulerTorrentSearchService(Context())
    called = {"count": 0}
    original = SlskdClient.search

    async def fake_search(self, query, *, timeout_seconds=None, max_results=None):
        called["count"] += 1
        return {"ok": True, "query": query, "source": "slskd", "candidates": [{"username": "u", "filename": "f.flac"}]}

    SlskdClient.search = fake_search
    try:
        result = await service._soulseek_companion_search(
            query_summary="Puerto Hurraco Persiana Jones",
            media=type("Media", (), {"key": "Puerto Hurraco"})(),
            category_id="music",
            search_scope="default",
            settings=settings,
        )
    finally:
        SlskdClient.search = original

    assert called["count"] >= 1
    assert result["status"] == "ready", result
    assert result["candidate_count"] == 1
    assert settings.soulseek.account_status == "ready"


def main() -> None:
    test_server_is_logged_in_state_is_recognized()
    test_server_not_logged_in_is_not_treated_as_bad_credentials()
    asyncio.run(test_search_tool_probes_when_account_status_is_only_checking())
    asyncio.run(test_parallel_companion_search_does_not_short_circuit_on_checking_status())
    print("Round 137 Soulseek login detection tests passed")


if __name__ == "__main__":
    main()
