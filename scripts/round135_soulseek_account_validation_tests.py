"""Round 135 Soulseek account validation tests."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ai.tools.soulseek import SearchSoulseekTool
from src.core.models import Settings, SoulseekSettings, ToolExecutionContext
from src.integrations.slskd_client import SlskdClient
from src.integrations.slskd_manager import SlskdManager


class DummySettingsManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings


def test_interpret_connection_payload_ready_and_auth_failed() -> None:
    ready = SlskdClient.interpret_connection_payload({"soulseek": {"connected": True}})
    assert ready["authenticated_to_soulseek"] is True
    assert ready["credentials_rejected"] is False

    failed = SlskdClient.interpret_connection_payload({}, log_text="Not connecting to the Soulseek server; username and/or password invalid")
    assert failed["authenticated_to_soulseek"] is False
    assert failed["credentials_rejected"] is True
    assert failed["connection_state"] == "auth_failed"


async def test_manager_validate_account_sets_status_from_api_async() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings()
        settings.soulseek = SoulseekSettings(
            enabled=True,
            api_key="abc",
            soulseek_username="captain",
            soulseek_password="secret",
            app_dir=str(Path(tmp) / "slskd"),
        )
        manager = SlskdManager()
        original = SlskdClient.connection_status

        async def fake_ready(self, *, log_text=""):
            return {"api_reachable": True, "authenticated_to_soulseek": True, "credentials_rejected": False, "connection_state": "connected"}

        SlskdClient.connection_status = fake_ready
        try:
            result = await manager.validate_account(settings, timeout_seconds=0)
        finally:
            SlskdClient.connection_status = original
        assert result["status"] == "ready"
        assert settings.soulseek.account_status == "ready"
        assert settings.soulseek.account_ready is True


async def test_manager_validate_account_sets_auth_failed_async() -> None:
    settings = Settings()
    settings.soulseek = SoulseekSettings(enabled=True, api_key="abc", soulseek_username="bad", soulseek_password="bad")
    manager = SlskdManager()
    original = SlskdClient.connection_status

    async def fake_failed(self, *, log_text=""):
        return {"api_reachable": True, "authenticated_to_soulseek": False, "credentials_rejected": True, "connection_state": "auth_failed", "error": "Soulseek rejected these credentials."}

    SlskdClient.connection_status = fake_failed
    try:
        result = await manager.validate_account(settings, timeout_seconds=0)
    finally:
        SlskdClient.connection_status = original
    assert result["status"] == "auth_failed"
    assert settings.soulseek.account_status == "auth_failed"
    assert "rejected" in settings.soulseek.account_status_message.lower()


async def test_search_tool_refuses_only_definitive_managed_account_failures_async() -> None:
    settings = Settings()
    settings.soulseek = SoulseekSettings(enabled=True, api_key="abc", account_status="needs_credentials")
    tool = SearchSoulseekTool(settings_manager=DummySettingsManager(settings))
    result = await tool.execute({"query": "rare album", "category_id": "music"}, ToolExecutionContext(session_id="test"))
    assert result["ok"] is False
    assert result["error_code"] == "SLSKD_NEEDS_CREDENTIALS"

    settings.soulseek = SoulseekSettings(enabled=True, api_key="abc", soulseek_username="u", soulseek_password="p", account_status="auth_failed", account_status_message="bad login")
    result = await tool.execute({"query": "rare album", "category_id": "music"}, ToolExecutionContext(session_id="test"))
    assert result["ok"] is False
    assert result["error_code"] == "SLSKD_AUTH_FAILED"


def test_ui_copy_mentions_new_username_validation() -> None:
    setup = (ROOT / "src/web/templates/setup.html").read_text(encoding="utf-8")
    settings_js = (ROOT / "src/web/static/js/components/settingsPanel.js").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "new unique username/password" in setup
    assert "network accepts it" in settings_js
    assert "auth_failed" in readme


def main() -> None:
    test_interpret_connection_payload_ready_and_auth_failed()
    asyncio.run(test_manager_validate_account_sets_status_from_api_async())
    asyncio.run(test_manager_validate_account_sets_auth_failed_async())
    asyncio.run(test_search_tool_refuses_only_definitive_managed_account_failures_async())
    test_ui_copy_mentions_new_username_validation()
    print("Round 135 Soulseek account validation tests passed")


if __name__ == "__main__":
    main()
