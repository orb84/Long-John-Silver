#!/usr/bin/env python3
"""Round 120 regression tests for bundled Trakt OOB/PIN auth.

The category-config split must not make normal users provide a Trakt developer
Client ID.  LJS ships a public app Client ID and that bundled app uses Trakt's
out-of-band code/PIN redirect URI.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.integrations.trakt_defaults import (  # noqa: E402
    BUNDLED_TRAKT_CLIENT_ID,
    BUNDLED_TRAKT_REDIRECT_URI,
    is_bundled_trakt_client_id,
    resolve_trakt_client_id,
    trakt_redirect_uri_for_client,
)

SYSTEM_ROUTER = (ROOT / "src/web/routers/system.py").read_text()
SETUP_ROUTER = (ROOT / "src/web/routers/setup.py").read_text()
PAGES_ROUTER = (ROOT / "src/web/routers/pages.py").read_text()
MAIN = (ROOT / "main.py").read_text()
TRAKT_DEFAULTS = (ROOT / "src/integrations/trakt_defaults.py").read_text()
SETTINGS_HANDLER = (ROOT / "src/web/action_handlers/settings.py").read_text()
SETTINGS_TEMPLATE = (ROOT / "src/web/templates/settings.html").read_text()
SETUP_TEMPLATE = (ROOT / "src/web/templates/setup.html").read_text()
SETTINGS_PANEL = (ROOT / "src/web/static/js/components/settingsPanel.js").read_text()

EXPECTED_PUBLIC_ID = "42bc6ba1535878e40f4773d3e064809f8caf7347e4ba2b3f3ddc61b32f1ab2ac"


class _SettingsWithoutTrakt:
    def category_service_value(self, *_args):
        return ""


def test_bundled_public_client_id_is_shipped() -> None:
    assert BUNDLED_TRAKT_CLIENT_ID == EXPECTED_PUBLIC_ID
    assert resolve_trakt_client_id(_SettingsWithoutTrakt()) == EXPECTED_PUBLIC_ID
    assert is_bundled_trakt_client_id(EXPECTED_PUBLIC_ID)


def test_bundled_client_uses_oob_pin_redirect_and_custom_uses_callback() -> None:
    assert BUNDLED_TRAKT_REDIRECT_URI == "urn:ietf:wg:oauth:2.0:oob"
    assert trakt_redirect_uri_for_client(EXPECTED_PUBLIC_ID, "http://127.0.0.1:8088") == BUNDLED_TRAKT_REDIRECT_URI
    assert trakt_redirect_uri_for_client("custom-id", "http://127.0.0.1:8088") == "http://localhost:8088/api/trakt/callback"


def test_system_router_stores_redirect_uri_used_to_start_pkce_flow() -> None:
    assert '"redirect_uri": redirect_uri' in SYSTEM_ROUTER
    assert '"flow": "oob" if is_bundled_trakt_client_id(actual_client_id) else "callback"' in SYSTEM_ROUTER
    assert 'pkce_record.get("redirect_uri") or trakt_redirect_uri_for_client' in SYSTEM_ROUTER
    assert 'return {"auth_url": auth_url, "flow": deps.trakt_pkce_store[state]["flow"]}' in SYSTEM_ROUTER


def test_startup_and_setup_resolve_bundled_trakt_client_not_private_blank() -> None:
    assert 'from src.integrations.trakt_defaults import resolve_trakt_client_id' in MAIN
    assert 'trakt_client_id = resolve_trakt_client_id(settings)' in MAIN
    assert '"trakt_uses_builtin_client": has_bundled_trakt_client_id()' in SETUP_ROUTER
    assert 'if not resolve_trakt_client_id(settings):' in SETUP_ROUTER


def test_settings_saves_blank_as_bundled_without_clearing_tokens() -> None:
    assert 'previous_effective_trakt_id = resolve_trakt_client_id(settings)' in SETTINGS_HANDLER
    assert 'next_effective_trakt_id = resolve_trakt_client_id(settings)' in SETTINGS_HANDLER
    assert 'if next_effective_trakt_id != previous_effective_trakt_id:' in SETTINGS_HANDLER


def test_ui_exposes_custom_client_id_only_as_advanced_override() -> None:
    assert 'trakt_custom_client_id' in PAGES_ROUTER
    assert 'is_bundled_trakt_client_id(configured_text)' in PAGES_ROUTER
    assert 'value="{{ trakt_custom_client_id or \'\' }}"' in SETTINGS_TEMPLATE
    assert 'value="{{ trakt_custom_client_id or \'\' }}"' in SETUP_TEMPLATE
    assert 'Leave blank to use the bundled LJS Trakt app' in SETTINGS_PANEL
    assert 'Custom Trakt Client ID (optional)' in SETTINGS_PANEL


def test_docs_warn_not_to_replace_oob_with_localhost_for_bundled_app() -> None:
    assert 'Do not replace this with a localhost URL' in TRAKT_DEFAULTS


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("round120 Trakt bundled OOB regression tests passed")
