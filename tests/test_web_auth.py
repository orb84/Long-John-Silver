"""
Tests for web authentication: page protection, WebSocket protection,
and JWT token verification.
"""

from unittest.mock import AsyncMock, MagicMock
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from src.web.app import create_app
from src.core.models import Settings, LLMConfig, ItemList
from src.utils.auth import AuthService, AuthConfig, load_auth_config


def _mock_settings(web_password_hash: str | None = None) -> MagicMock:
    """Create a mock SettingsManager with a given password hash."""
    settings = Settings(
        llm=LLMConfig(model="test", api_key="test"),
        tracked_items=ItemList(items=[]),
        download_dir="/tmp/test",
        web_password_hash=web_password_hash,
        setup_complete=True,
        trakt_client_id="",
    )
    mgr = MagicMock()
    mgr.settings = settings
    return mgr


def _make_app(settings_mgr, auth_service):
    """Create a bare-bones FastAPI app with mocked dependencies."""
    return create_app(
        settings_manager=settings_mgr,
        db=AsyncMock(),
        assistant=AsyncMock(),
        downloader=AsyncMock(),
        notifications=AsyncMock(),
        auth_service=auth_service,
        llm_manager=MagicMock(),
        scanner=AsyncMock(),
        conversation_manager=MagicMock(),
        behavior_tracker=MagicMock(),
        suggestion_compiler=AsyncMock(),
        recommender=MagicMock(),
        release_group_tracker=MagicMock(),
        comms_registry=MagicMock(),
        torrent_racer=MagicMock(),
        browser_runtime=MagicMock(),
        jackett_manager=MagicMock(),
        scheduler=AsyncMock(),
        supervisor=MagicMock(),
    )


class TestPageAuth:
    """Dashboard page routes (/, /library, /settings) should enforce auth."""

    def test_pages_allow_when_no_password_configured(self):
        """When no password is set, pages should be accessible."""
        auth = AuthService(secret_key="test-secret")
        settings_mgr = _mock_settings(web_password_hash=None)
        app = _make_app(settings_mgr, auth)
        client = TestClient(app)

        for path in ("/", "/library", "/settings"):
            resp = client.get(path)
            assert resp.status_code == 200, f"{path} should be 200 (open access), got {resp.status_code}"

    def test_pages_reject_when_password_configured_and_no_token(self):
        """When password is set, pages should return 401 without a token."""
        auth = AuthService(secret_key="test-secret")
        hashed = auth.hash_password("secret")
        settings_mgr = _mock_settings(web_password_hash=hashed)
        app = _make_app(settings_mgr, auth)
        client = TestClient(app)

        for path in ("/", "/library", "/settings"):
            resp = client.get(path)
            assert resp.status_code == 401, f"{path} should be 401 without token, got {resp.status_code}"

    def test_pages_allow_with_valid_token_cookie(self):
        """When a valid token cookie is present, pages should be accessible."""
        auth = AuthService(secret_key="test-secret")
        hashed = auth.hash_password("secret")
        settings_mgr = _mock_settings(web_password_hash=hashed)
        app = _make_app(settings_mgr, auth)
        client = TestClient(app)

        token = auth.create_token("admin")
        cookies = {"ljs_token": token}

        for path in ("/", "/library", "/settings"):
            resp = client.get(path, cookies=cookies)
            assert resp.status_code == 200, f"{path} should be 200 with valid token, got {resp.status_code}"

    def test_pages_allow_with_valid_token_header(self):
        """When a valid X-Auth-Token header is present, pages should be accessible."""
        auth = AuthService(secret_key="test-secret")
        hashed = auth.hash_password("secret")
        settings_mgr = _mock_settings(web_password_hash=hashed)
        app = _make_app(settings_mgr, auth)
        client = TestClient(app)

        token = auth.create_token("admin")
        headers = {"X-Auth-Token": token}

        for path in ("/", "/library", "/settings"):
            resp = client.get(path, headers=headers)
            assert resp.status_code == 200, f"{path} should be 200 with token header, got {resp.status_code}"


class TestWebSocketAuth:
    """WebSocket routes should enforce auth before accepting connections."""

    def test_ws_rejects_without_token(self):
        """WebSocket connections should be rejected without a token."""
        auth = AuthService(secret_key="test-secret")
        hashed = auth.hash_password("secret")
        settings_mgr = _mock_settings(web_password_hash=hashed)
        app = _make_app(settings_mgr, auth)
        client = TestClient(app)

        for path in ("/ws/chat", "/ws/downloads", "/ws/events"):
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with client.websocket_connect(path):
                    pass
            assert exc_info.value.code == 4001, f"{path} should close with 4001, got {exc_info.value.code}"

    def test_ws_accepts_with_token_cookie(self):
        """WebSocket connections should be accepted with a valid token cookie."""
        auth = AuthService(secret_key="test-secret")
        hashed = auth.hash_password("secret")
        settings_mgr = _mock_settings(web_password_hash=hashed)
        app = _make_app(settings_mgr, auth)
        client = TestClient(app)

        token = auth.create_token("admin")
        cookies = {"ljs_token": token}

        for path in ("/ws/chat", "/ws/downloads", "/ws/events"):
            with client.websocket_connect(path, cookies=cookies) as ws:
                # Successfully connected — close manually
                ws.close()

    def test_ws_accepts_with_token_query_param(self):
        """WebSocket connections should be accepted with a token query parameter."""
        auth = AuthService(secret_key="test-secret")
        hashed = auth.hash_password("secret")
        settings_mgr = _mock_settings(web_password_hash=hashed)
        app = _make_app(settings_mgr, auth)
        client = TestClient(app)

        token = auth.create_token("admin")

        for path in ("/ws/chat", "/ws/downloads", "/ws/events"):
            url = f"{path}?token={token}"
            with client.websocket_connect(url) as ws:
                ws.close()

    def test_ws_allows_when_no_password_configured(self):
        """WebSocket connections should be allowed when no password is set."""
        auth = AuthService(secret_key="test-secret")
        settings_mgr = _mock_settings(web_password_hash=None)
        app = _make_app(settings_mgr, auth)
        client = TestClient(app)

        for path in ("/ws/chat", "/ws/downloads", "/ws/events"):
            with client.websocket_connect(path) as ws:
                ws.close()


class TestTokenSecurity:
    """JWT token security: different secrets should not verify."""

    def test_tokens_signed_with_wrong_secret_fail(self):
        """Tokens signed with one secret should fail with another."""
        auth1 = AuthService(secret_key="correct-secret")
        auth2 = AuthService(secret_key="wrong-secret")

        token = auth1.create_token("admin")
        username = auth2.verify_token(token)
        assert username is None, "Token from different secret should not verify"

    def test_expired_token_is_rejected(self):
        """An expired token should return None from verify_token."""
        from datetime import datetime, timedelta, timezone
        from src.utils.auth import jwt_encode

        auth = AuthService(secret_key="test-secret")
        # Manually create a token that expired 1 hour ago
        expired_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).timestamp()
        expired_payload = {"sub": "admin", "exp": expired_ts}
        # Use jwt_encode from auth module to create token with same secret
        expired_token = jwt_encode(expired_payload, "test-secret")

        username = auth.verify_token(expired_token)
        assert username is None, "Expired token should not verify"

    def test_tampered_token_is_rejected(self):
        """A tampered token should return None from verify_token."""
        auth = AuthService(secret_key="test-secret")
        token = auth.create_token("admin")

        # Corrupt the payload
        parts = token.split(".")
        tampered = f"{parts[0]}.{parts[1]}x.{parts[2]}"

        username = auth.verify_token(tampered)
        assert username is None, "Tampered token should not verify"


class TestLoadAuthConfig:
    """Tests for load_auth_config() environment-based configuration."""

    def test_load_auth_config_with_env_secret(self, monkeypatch):
        monkeypatch.setenv("LJS_WEB_SECRET", "my-secret-key")
        config = load_auth_config()
        assert config.secret_key == "my-secret-key"
        assert config.allow_insecure_dev_secret is False

    def test_load_auth_config_without_secret_without_insecure_raises(self, monkeypatch):
        monkeypatch.delenv("LJS_WEB_SECRET", raising=False)
        monkeypatch.delenv("LJS_ALLOW_INSECURE_DEV", raising=False)
        with pytest.raises(ValueError):
            load_auth_config()

    def test_load_auth_config_without_secret_with_insecure_dev(self, monkeypatch):
        monkeypatch.delenv("LJS_WEB_SECRET", raising=False)
        monkeypatch.setenv("LJS_ALLOW_INSECURE_DEV", "1")
        config = load_auth_config()
        assert config.allow_insecure_dev_secret is True
        assert len(config.secret_key) > 0
