"""Static regressions for first-run setup completion."""

from pathlib import Path


def test_setup_complete_response_must_be_checked_by_frontend() -> None:
    """The wizard must not show success when the API returns a blocked result."""
    js = Path("src/web/static/js/pages/setup.js").read_text()
    assert "result.status === 'blocked'" in js
    assert "result.setup_complete === false" in js
    assert "Setup is missing required items" in js


def test_setup_time_settings_endpoints_are_not_redirected() -> None:
    """Setup uses settings endpoints before setup_complete is true."""
    app = Path("src/web/app.py").read_text()
    assert '"/api/settings"' in app


def test_open_access_password_skip_is_consistent_with_validation() -> None:
    """Skipping the password should warn, not block setup completion."""
    router = Path("src/web/routers/setup.py").read_text()
    assert '"id": "web_password"' in router
    assert 'warnings.append({' in router
    assert 'missing.append({"id": "web_password"' not in router
