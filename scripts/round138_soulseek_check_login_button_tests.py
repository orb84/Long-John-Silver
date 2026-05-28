"""Round 138 Soulseek explicit login-check button tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import types

# Optional runtime dependencies are not installed in the lightweight regression sandbox.
if "aiosqlite" not in sys.modules:
    aiosqlite_stub = types.ModuleType("aiosqlite")
    class _Connection: pass
    class _Cursor: pass
    aiosqlite_stub.Connection = _Connection
    aiosqlite_stub.Cursor = _Cursor
    sys.modules["aiosqlite"] = aiosqlite_stub

if "bcrypt" not in sys.modules:
    bcrypt_stub = types.ModuleType("bcrypt")
    bcrypt_stub.gensalt = lambda *a, **k: b"salt"
    bcrypt_stub.hashpw = lambda password, salt: b"hash"
    bcrypt_stub.checkpw = lambda password, hashed: True
    sys.modules["bcrypt"] = bcrypt_stub

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.models import Settings
from src.web.action_handlers.system import SystemActionHandler
from src.integrations.slskd_client import SlskdClient


class DummySettingsManager:
    def __init__(self) -> None:
        self.settings = Settings()
        self.saved = 0

    def save(self, settings: Settings) -> None:
        self.settings = settings
        self.saved += 1


class DummySlskdManager:
    url = "http://127.0.0.1:5030"
    last_error = ""

    def __init__(self) -> None:
        self.is_running = False
        self.is_installed = True
        self.start_calls = []

    async def start(self, settings: Settings, *, login_timeout_seconds: float = 90.0) -> bool:
        self.start_calls.append(login_timeout_seconds)
        self.is_running = True
        settings.soulseek.host = self.url
        settings.soulseek.api_key = settings.soulseek.api_key or "abc"
        settings.soulseek.account_status = "ready"
        settings.soulseek.account_status_message = "Soulseek account authenticated."
        settings.soulseek.account_checked_at = "2026-01-01T00:00:00+00:00"
        return True

    async def validate_account(self, settings: Settings, *, timeout_seconds: float = 0.0) -> dict:
        return {"api_reachable": True, "authenticated_to_soulseek": True, "status": "ready"}

    def save_to_settings(self, settings: Settings) -> None:
        settings.soulseek.host = self.url
        settings.soulseek.managed = True


async def test_check_login_action_verifies_search_probe() -> None:
    sm = DummySettingsManager()
    slskd = DummySlskdManager()
    handler = SystemActionHandler(sm, None, None, slskd, None, None, None)  # type: ignore[arg-type]
    original = SlskdClient.search

    async def fake_search(self, query, *, timeout_seconds=None, max_results=None):
        return {"ok": True, "query": query, "candidates": []}

    SlskdClient.search = fake_search
    try:
        result = await handler.check_soulseek_login({
            "enabled": True,
            "soulseek_username": "new_unique_user",
            "soulseek_password": "secret",
            "share_mode": "full_library",
        }, timeout_seconds=17)
    finally:
        SlskdClient.search = original

    assert result["status"] == "ready", result
    assert result["ready"] is True, result
    assert result["search_probe_ok"] is True, result
    assert sm.settings.soulseek.account_status == "ready"
    assert slskd.start_calls == [17], slskd.start_calls
    assert sm.saved >= 1


def test_check_login_ui_is_present() -> None:
    settings_panel = (ROOT / "src/web/static/js/components/settingsPanel.js").read_text(encoding="utf-8")
    setup_js = (ROOT / "src/web/static/js/pages/setup.js").read_text(encoding="utf-8")
    setup_html = (ROOT / "src/web/templates/setup.html").read_text(encoding="utf-8")
    router = (ROOT / "src/web/routers/system.py").read_text(encoding="utf-8")
    registration = (ROOT / "src/core/actions/registration.py").read_text(encoding="utf-8")

    assert "pref-soulseek-check-login" in settings_panel
    assert "checkSoulseekLogin" in settings_panel
    assert "/api/soulseek/check-login" in settings_panel
    assert "setup-soulseek-check-login" in setup_html
    assert "checkSetupSoulseekLogin" in setup_js
    assert "/api/soulseek/check-login" in setup_js
    assert "system_check_soulseek_login" in router
    assert "system_check_soulseek_login" in registration


def main() -> None:
    asyncio.run(test_check_login_action_verifies_search_probe())
    test_check_login_ui_is_present()
    print("Round 138 Soulseek check-login button tests passed")


if __name__ == "__main__":
    main()
