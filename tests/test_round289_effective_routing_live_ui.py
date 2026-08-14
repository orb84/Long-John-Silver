"""Round 289 regressions for effective model routing and live browser behavior."""

from __future__ import annotations

import asyncio
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.models import LLMConfig, TaskModelConfig
from src.llm_providers import LLMProviderManager
from src.llm_providers.activity import LLMActivityMonitor
from src.llm_providers.task_client import TaskLLMClient
from starlette.responses import HTMLResponse, JSONResponse

from src.web.static_assets import BrowserBundleCoherenceMiddleware, StaticAssetVersionResolver


class TestEffectiveRouteResolution:
    """Exercise the exact model-ownership failures seen in the live logs."""

    def test_per_task_generation_setting_does_not_erase_tier_model(self) -> None:
        config = LLMConfig(
            model="base-model",
            lightweight=TaskModelConfig(model="small-model", provider="nvidia_nim"),
            intent_routing=TaskModelConfig(max_tokens=512),
        )
        resolved = config.resolve_config("intent_routing")
        assert resolved.model == "small-model"
        assert resolved.provider == "nvidia_nim"
        assert resolved.max_tokens == 512
        assert config.route_source("intent_routing", "model") == "tier:lightweight"
        assert config.route_source("intent_routing", "max_tokens") == "task:intent_routing"

    def test_apply_base_route_clears_chat_route_identity_but_preserves_tuning_and_embedding(self) -> None:
        config = LLMConfig(
            model="new-model",
            lightweight=TaskModelConfig(model="old-model", temperature=0.25),
            intent_routing=TaskModelConfig(provider="old-provider", max_tokens=700),
            embedding=TaskModelConfig(model="embedding-model", provider="local"),
        )
        config.clear_route_overrides()
        assert config.get_model_for_task("intent_routing") == "new-model"
        assert config.lightweight.temperature == 0.25
        assert config.intent_routing.max_tokens == 700
        assert config.embedding.model == "embedding-model"
        assert config.embedding.provider == "local"


class TestRuntimeRouteReload:
    """Prove a settings save cannot leave a retry running on the old model."""

    @pytest.mark.asyncio
    async def test_config_reload_cancels_active_old_route_and_next_call_uses_new_model(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as handle:
            key_path = handle.name
        manager = LLMProviderManager(key_store_path=key_path)
        monitor = LLMActivityMonitor()
        client = TaskLLMClient(
            manager=manager,
            llm_config=LLMConfig(model="old-model", active_provider="openrouter"),
            activity_monitor=monitor,
        )
        started = asyncio.Event()

        async def blocked_completion(**_kwargs):
            started.set()
            await asyncio.Event().wait()

        with patch("src.llm_providers.task_client.litellm.acompletion", new=AsyncMock(side_effect=blocked_completion)):
            provider_task = asyncio.create_task(client.completion("chat", [{"role": "user", "content": "hello"}]))
            await asyncio.wait_for(started.wait(), timeout=2)
            client.update_config(LLMConfig(model="new-model", active_provider="openrouter"))
            with pytest.raises(asyncio.CancelledError):
                await provider_task

        assert client.resolve_task("chat").model == "new-model"
        assert client.last_reload_cancelled_calls == 1
        events = monitor.snapshot()["events"]
        assert any(event["event_type"] == "route_configuration_changed" for event in events)
        call = monitor.snapshot()["last_call"]
        assert call["status"] == "cancelled"
        assert "route configuration changed" in str(call["error"]).casefold()
        assert monitor.status(call["call_id"]) == "cancelled"
        Path(key_path).unlink(missing_ok=True)

    def test_effective_routes_report_the_winning_source(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as handle:
            key_path = handle.name
        manager = LLMProviderManager(key_store_path=key_path)
        client = TaskLLMClient(
            manager=manager,
            llm_config=LLMConfig(
                model="base",
                active_provider="openrouter",
                lightweight=TaskModelConfig(model="router"),
            ),
        )
        route = next(row for row in client.effective_routes() if row["task"] == "intent_routing")
        assert route["model"] == "router"
        assert route["source"] == "tier:lightweight"
        Path(key_path).unlink(missing_ok=True)


class TestDurableDiagnosticsAndAssets:
    """Cover the browser integration paths previous source-only tests missed."""

    def test_problem_events_are_present_in_the_first_snapshot(self) -> None:
        monitor = LLMActivityMonitor()
        call_id = monitor.start_call(
            task="intent_routing", provider="nvidia_nim", model="model",
            messages=[{"role": "user", "content": "route"}], tools=None, stream=False,
        )
        monitor.record_attempt(call_id, attempt=1, max_attempts=2, status="started")
        monitor.record_attempt(call_id, attempt=1, max_attempts=2, status="failed", error="ReadTimeout")
        snapshot = monitor.snapshot()
        assert any(event["event_type"] == "attempt_timeout" for event in snapshot["events"])

    def test_static_asset_version_changes_with_browser_bundle(self, tmp_path: Path) -> None:
        root = tmp_path / "static"
        root.mkdir()
        asset = root / "app.js"
        asset.write_text("one", encoding="utf-8")
        first = StaticAssetVersionResolver(root)
        asset.write_text("two", encoding="utf-8")
        second = StaticAssetVersionResolver(root)
        assert first.version != second.version
        assert first.url("/static/app.js").startswith("/static/app.js?v=")

    @pytest.mark.asyncio
    async def test_html_cannot_pin_an_obsolete_browser_bundle(self) -> None:
        middleware = BrowserBundleCoherenceMiddleware(MagicMock(), asset_version="abc123")

        async def html_response(_request):
            return HTMLResponse("<html></html>")

        async def json_response(_request):
            return JSONResponse({"ok": True})

        html = await middleware.dispatch(MagicMock(), html_response)
        payload = await middleware.dispatch(MagicMock(), json_response)
        assert html.headers["cache-control"] == "no-store, max-age=0"
        assert html.headers["x-ljs-asset-version"] == "abc123"
        assert "cache-control" not in payload.headers
        assert payload.headers["x-ljs-asset-version"] == "abc123"

    def test_frontend_contract_harness_executes_cards_and_chat_state(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            ["node", str(project_root / "scripts/round289_frontend_contract_harness.js"), str(project_root)],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        assert result.returncode == 0, result.stderr or result.stdout
        assert "ROUND289_FRONTEND_CONTRACT_PASS" in result.stdout

class TestAtomicSettingsMutation:
    """Verify one save updates base/tier ownership and returns effective routes."""

    @pytest.mark.asyncio
    async def test_llm_settings_save_is_atomic_and_apply_all_is_authoritative(self) -> None:
        from types import SimpleNamespace
        from src.web.action_handlers.settings import SettingsActionHandler

        config = LLMConfig(
            model="old-base",
            active_provider="nvidia_nim",
            lightweight=TaskModelConfig(model="old-router"),
            intent_routing=TaskModelConfig(provider="old-provider", max_tokens=500),
        )

        class _SettingsManager:
            def __init__(self):
                self.settings = SimpleNamespace(llm=config)
                self.saved = 0

            def save(self, settings):
                assert settings is self.settings
                self.saved += 1

        class _Assistant:
            def __init__(self):
                self.updated = 0

            def update_settings(self, settings):
                self.updated += 1

            def llm_route_summary(self):
                return {
                    "config_revision": 9,
                    "routes": [{
                        "task": "intent_routing", "model": config.get_model_for_task("intent_routing"),
                        "provider": config.active_provider, "source": config.route_source("intent_routing", "model"),
                    }],
                    "cancelled_old_route_calls": 0,
                }

        settings_manager = _SettingsManager()
        assistant = _Assistant()
        llm_manager = MagicMock()
        llm_manager.registry.get_preset.return_value = None
        llm_manager.keys.get_active_key.return_value = None
        handler = SettingsActionHandler(
            settings_manager=settings_manager,
            assistant=assistant,
            downloader=MagicMock(),
            auth_service=MagicMock(),
            llm_manager=llm_manager,
        )

        result = await handler.update_llm(
            model="new-base",
            provider="nvidia_nim",
            apply_base_to_all=True,
            tiers={"lightweight": {"model": "stale-tier"}},
        )

        assert settings_manager.saved == 1
        assert assistant.updated == 1
        assert config.get_model_for_task("intent_routing") == "new-base"
        assert config.intent_routing.max_tokens == 500
        assert result["routes"][0]["model"] == "new-base"
        assert result["routes"][0]["source"] == "global"

    @pytest.mark.asyncio
    async def test_setup_base_model_clears_stale_task_route_identity(self) -> None:
        from types import SimpleNamespace
        from src.web.action_handlers.setup import SetupActionHandler

        config = LLMConfig(
            model="old-base",
            active_provider="nvidia_nim",
            lightweight=TaskModelConfig(model="old-router", provider="nvidia_nim"),
            intent_routing=TaskModelConfig(max_tokens=700),
        )

        class _SettingsManager:
            def __init__(self):
                self.settings = SimpleNamespace(llm=config, web_search=SimpleNamespace(model_dump=lambda: {}))
                self.saved = 0

            def save(self, settings):
                assert settings is self.settings
                self.saved += 1

        class _Assistant:
            def update_settings(self, _settings):
                return None

            def llm_route_summary(self):
                return {
                    "config_revision": 2,
                    "routes": [{
                        "task": "intent_routing",
                        "model": config.get_model_for_task("intent_routing"),
                        "source": config.route_source("intent_routing", "model"),
                    }],
                }

        llm_manager = MagicMock()
        llm_manager.registry.get_preset.return_value = None
        handler = SetupActionHandler(
            settings_manager=_SettingsManager(),
            auth_service=MagicMock(),
            llm_manager=llm_manager,
            assistant=_Assistant(),
        )
        result = await handler.setup_llm(provider="nvidia_nim", model="new-base")
        assert config.get_model_for_task("intent_routing") == "new-base"
        assert config.intent_routing.max_tokens == 700
        assert result["routes"][0]["source"] == "global"

class TestLegacyAndStaleBrowserContracts:
    """Execute the live integration seams that failed on the user's machine."""

    @staticmethod
    def _json_request(path: str, payload: dict) -> "Request":
        import json
        from fastapi import Request

        body = json.dumps(payload).encode("utf-8")
        delivered = False

        async def receive():
            nonlocal delivered
            if delivered:
                return {"type": "http.disconnect"}
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}

        return Request({
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }, receive)

    def test_asset_resolver_rejects_missing_or_old_browser_bundle(self, tmp_path: Path) -> None:
        root = tmp_path / "static"
        root.mkdir()
        (root / "app.js").write_text("current", encoding="utf-8")
        resolver = StaticAssetVersionResolver(root)
        assert resolver.matches(resolver.version) is True
        assert resolver.matches(None) is False
        assert resolver.matches("") is False
        assert resolver.matches("old-version") is False

    @pytest.mark.asyncio
    async def test_legacy_base_model_save_defaults_to_authoritative_route(self) -> None:
        from src.web.routers.settings import SettingsRouter

        router = SettingsRouter(MagicMock())
        router._execute_action = AsyncMock(side_effect=lambda _name, arguments: arguments)
        result = await router._update_llm(
            self._json_request("/api/settings/llm", {"provider": "nvidia_nim", "model": "new-model"}),
            _auth=True,
        )
        assert result["model"] == "new-model"
        assert result["apply_base_to_all"] is True

    @pytest.mark.asyncio
    async def test_retired_two_step_tier_endpoint_fails_with_reload_instruction(self) -> None:
        from fastapi import HTTPException
        from src.web.routers.settings import SettingsRouter

        router = SettingsRouter(MagicMock())
        with pytest.raises(HTTPException) as caught:
            await router._update_tiers(
                self._json_request("/api/settings/tiers", {"lightweight": {"model": "stale"}}),
                _auth=True,
            )
        assert caught.value.status_code == 409
        assert "reload" in str(caught.value.detail).casefold()

    def test_stale_browser_is_rejected_before_llm_and_current_bundle_runs(self) -> None:
        from fastapi.testclient import TestClient
        from src.core.models import Intent
        from src.core.models import Settings
        from src.utils.auth import AuthService
        from src.web.app import create_app

        class _Assistant:
            def __init__(self) -> None:
                self.calls = 0

            async def preflight_intent_for_chat_status(self, _prompt, **_kwargs):
                return Intent.CHAT

            async def run_stream(self, _prompt, **_kwargs):
                self.calls += 1
                yield "ok"

            def format_chat_error(self, _operation, exc):
                return f"error: {exc}"

        assistant = _Assistant()
        settings = Settings(
            llm=LLMConfig(model="test", api_key="test"), tracked_items=[],
            download_dir="/tmp/test", web_password_hash=None, setup_complete=True,
            trakt_client_id="",
        )
        manager = MagicMock()
        manager.settings = settings
        downloader = MagicMock()
        downloader.set_stats_callback.return_value = None
        app = create_app(
            settings_manager=manager, db=AsyncMock(), assistant=assistant,
            downloader=downloader, notifications=MagicMock(),
            auth_service=AuthService(secret_key="test-secret"), llm_manager=MagicMock(),
            scanner=AsyncMock(), conversation_manager=MagicMock(), behavior_tracker=MagicMock(),
            suggestion_compiler=AsyncMock(), recommender=MagicMock(),
            release_group_tracker=MagicMock(), comms_registry=MagicMock(),
            torrent_racer=MagicMock(), browser_runtime=MagicMock(), jackett_manager=MagicMock(),
            scheduler=AsyncMock(), supervisor=MagicMock(),
        )
        origin_headers = {"origin": "http://testserver"}
        with TestClient(app) as client:
            with client.websocket_connect("/ws/chat", headers=origin_headers) as ws:
                ws.send_json({"type": "message", "message": "hello", "session_id": "s", "turn_id": "stale"})
                stale = ws.receive_json()
                assert stale["type"] == "error"
                assert "reload" in stale["content"].casefold()
                assert assistant.calls == 0

                ws.send_json({
                    "type": "message", "message": "hello", "session_id": "s", "turn_id": "current",
                    "client_asset_version": app.state.static_asset_version,
                })
                assert ws.receive_json()["type"] == "started"
                token = ws.receive_json()
                assert token["type"] == "token"
                assert token["content"] == "ok"
        assert assistant.calls == 1

    def test_stale_rest_browser_is_rejected_before_llm(self) -> None:
        from fastapi.testclient import TestClient
        from src.core.models import Settings
        from src.utils.auth import AuthService
        from src.web.app import create_app

        assistant = MagicMock()
        settings = Settings(
            llm=LLMConfig(model="test", api_key="test"), tracked_items=[],
            download_dir="/tmp/test", web_password_hash=None, setup_complete=True,
            trakt_client_id="",
        )
        manager = MagicMock()
        manager.settings = settings
        downloader = MagicMock()
        downloader.set_stats_callback.return_value = None
        app = create_app(
            settings_manager=manager, db=AsyncMock(), assistant=assistant,
            downloader=downloader, notifications=MagicMock(),
            auth_service=AuthService(secret_key="test-secret"), llm_manager=MagicMock(),
            scanner=AsyncMock(), conversation_manager=MagicMock(), behavior_tracker=MagicMock(),
            suggestion_compiler=AsyncMock(), recommender=MagicMock(),
            release_group_tracker=MagicMock(), comms_registry=MagicMock(),
            torrent_racer=MagicMock(), browser_runtime=MagicMock(), jackett_manager=MagicMock(),
            scheduler=AsyncMock(), supervisor=MagicMock(),
        )
        with TestClient(app) as client:
            response = client.post(
                "/api/chat", headers={"origin": "http://testserver"},
                json={"message": "hello", "session_id": "s"},
            )
        assert response.status_code == 200
        assert response.json()["reload_required"] is True
        assistant.run_stream.assert_not_called()
