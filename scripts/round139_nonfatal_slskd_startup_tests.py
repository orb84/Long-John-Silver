#!/usr/bin/env python3
"""Round 139 non-fatal managed slskd startup regression tests."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_main_does_not_start_slskd_before_web_ready() -> None:
    text = read("main.py")
    main_body = text.split("async def main()", 1)[1]
    pre_web, post_web = main_body.split("# --- Start the web server first", 1)
    assert "await slskd_manager.start" not in pre_web, "slskd startup must not block pre-web startup"
    assert "soulseek_managed_startup" in post_web
    assert "_start_managed_soulseek_after_ui" in text


def test_slskd_manager_converts_start_exceptions_to_state() -> None:
    text = read("src/integrations/slskd_manager.py")
    assert "async def _start_impl" in text
    assert "async def start" in text
    assert "logger.exception(self._last_error)" in text
    assert 'settings.soulseek.account_status = "error"' in text
    assert "async def _validate_account_impl" in text


def test_user_actions_do_not_let_slskd_start_exceptions_escape() -> None:
    settings_action = read("src/web/action_handlers/settings.py")
    system_action = read("src/web/action_handlers/system.py")
    assert "Soulseek start failed while saving settings" in settings_action
    assert "Soulseek start failed during login check" in system_action
    assert "Soulseek start failed:" in system_action


def main() -> None:
    test_main_does_not_start_slskd_before_web_ready()
    test_slskd_manager_converts_start_exceptions_to_state()
    test_user_actions_do_not_let_slskd_start_exceptions_escape()
    print("Round 139 non-fatal managed slskd startup tests passed")


if __name__ == "__main__":
    main()
