"""Static regression tests for Trakt setup and OAuth wiring."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SYSTEM_ROUTER = (ROOT / "src/web/routers/system.py").read_text()
SETUP_ROUTER = (ROOT / "src/web/routers/setup.py").read_text()
TRAKT_JS = (ROOT / "src/web/static/js/components/traktAuth.js").read_text()
SETTINGS_PANEL = (ROOT / "src/web/static/js/components/settingsPanel.js").read_text()


def test_trakt_pkce_state_stores_client_id_with_verifier() -> None:
    """Callback must use the same client ID and redirect URI that generated the auth URL."""
    assert '"client_id": actual_client_id' in SYSTEM_ROUTER
    assert '"redirect_uri": redirect_uri' in SYSTEM_ROUTER
    assert 'client_id = resolve_trakt_client_id(settings, pkce_record.get("client_id"))' in SYSTEM_ROUTER
    assert 'pkce_record.get("redirect_uri") or trakt_redirect_uri_for_client' in SYSTEM_ROUTER


def test_setup_exposes_trakt_client_and_account_status_separately() -> None:
    """Setup requirements should distinguish public client ID from user account tokens."""
    assert 'trakt_client_available' in SETUP_ROUTER
    assert 'trakt_connected' in SETUP_ROUTER
    assert 'trakt_uses_builtin_client' in SETUP_ROUTER
    assert 'Trakt account linking needs a client ID' in SETUP_ROUTER
    assert 'linking it enables personalized recommendations' in SETUP_ROUTER


def test_trakt_js_supports_setup_static_settings_and_dynamic_settings_ids() -> None:
    """OAuth launcher should work from setup page, settings page, and dynamic panel."""
    for element_id in ('trakt_client_id', 'setup-trakt-id', 'pref-trakt-id'):
        assert element_id in TRAKT_JS
    for element_id in ('setup-trakt-status', 'settings-trakt-status', 'pref-trakt-status'):
        assert element_id in TRAKT_JS
    assert 'setup-trakt-id' not in SETTINGS_PANEL or 'pref-trakt-id' in SETTINGS_PANEL
