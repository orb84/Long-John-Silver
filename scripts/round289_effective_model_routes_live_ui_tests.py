"""Dependency-light executable checks for Round 289 live integration fixes."""

from __future__ import annotations

import asyncio
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.models import LLMConfig, TaskModelConfig
from src.llm_providers.activity import LLMActivityMonitor
from src.web.action_handlers.settings import SettingsActionHandler
from src.web.static_assets import StaticAssetVersionResolver


class Round289Checks:
    """Run the release-blocking effective-routing and browser contracts."""

    @classmethod
    async def run(cls) -> None:
        cls._check_fieldwise_route_ownership()
        await cls._check_atomic_base_route_save()
        cls._check_durable_problem_ledger()
        cls._check_static_bundle_identity()
        cls._check_legacy_settings_contract()
        cls._run_browser_harness()

    @staticmethod
    def _check_fieldwise_route_ownership() -> None:
        config = LLMConfig(
            model="base-model",
            active_provider="nvidia_nim",
            lightweight=TaskModelConfig(model="old-router", provider="nvidia_nim"),
            intent_routing=TaskModelConfig(max_tokens=700),
        )
        assert config.get_model_for_task("intent_routing") == "old-router"
        assert config.get_max_tokens_for_task("intent_routing") == 700
        config.clear_route_overrides()
        assert config.get_model_for_task("intent_routing") == "base-model"
        assert config.get_max_tokens_for_task("intent_routing") == 700
        assert config.route_source("intent_routing", "model") == "global"

    @staticmethod
    async def _check_atomic_base_route_save() -> None:
        config = LLMConfig(
            model="old-base",
            active_provider="nvidia_nim",
            lightweight=TaskModelConfig(model="old-router"),
        )

        class SettingsManager:
            def __init__(self) -> None:
                self.settings = SimpleNamespace(llm=config)
                self.saves = 0

            def save(self, settings) -> None:
                assert settings is self.settings
                self.saves += 1

        class Assistant:
            def __init__(self) -> None:
                self.reloads = 0

            def update_settings(self, _settings) -> None:
                self.reloads += 1

            def llm_route_summary(self) -> dict:
                return {
                    "config_revision": 2,
                    "routes": [{
                        "task": "intent_routing",
                        "model": config.get_model_for_task("intent_routing"),
                        "source": config.route_source("intent_routing", "model"),
                    }],
                    "cancelled_old_route_calls": 0,
                }

        manager = SettingsManager()
        assistant = Assistant()
        llm_manager = MagicMock()
        llm_manager.registry.get_preset.return_value = None
        llm_manager.keys.get_active_key.return_value = None
        handler = SettingsActionHandler(
            settings_manager=manager,
            assistant=assistant,
            downloader=MagicMock(),
            auth_service=MagicMock(),
            llm_manager=llm_manager,
        )
        result = await handler.update_llm(
            model="new-base",
            provider="nvidia_nim",
            tiers={"lightweight": {"model": "stale-router"}},
            apply_base_to_all=True,
        )
        assert manager.saves == 1
        assert assistant.reloads == 1
        assert result["routes"][0]["model"] == "new-base"
        assert result["routes"][0]["source"] == "global"

    @staticmethod
    def _check_durable_problem_ledger() -> None:
        monitor = LLMActivityMonitor()
        call_id = monitor.start_call(
            task="intent_routing",
            provider="nvidia_nim",
            model="model",
            messages=[{"role": "user", "content": "route"}],
            tools=None,
            stream=False,
        )
        monitor.record_attempt(call_id, attempt=1, max_attempts=2, status="started")
        monitor.record_attempt(
            call_id,
            attempt=1,
            max_attempts=2,
            status="failed",
            error="ReadTimeout",
        )
        snapshot = monitor.snapshot()
        assert monitor.status(call_id) == "running"
        assert any(event["event_type"] == "attempt_timeout" for event in snapshot["events"])

    @staticmethod
    def _check_static_bundle_identity() -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            asset = root / "app.js"
            asset.write_text("one", encoding="utf-8")
            first = StaticAssetVersionResolver(root)
            asset.write_text("two", encoding="utf-8")
            second = StaticAssetVersionResolver(root)
            assert first.version != second.version
            assert second.matches(second.version)
            assert not second.matches(first.version)
            assert "?v=" in second.url("/static/app.js")

    @staticmethod
    def _check_legacy_settings_contract() -> None:
        source = Path("src/web/static/js/components/settingsSavers.js").read_text(encoding="utf-8")
        assert "saveLLMRouting" in source
        assert "'/api/settings/llm'" in source
        assert "'/api/settings/tiers'" not in source
        assert "apply_base_to_all" in source

    @staticmethod
    def _run_browser_harness() -> None:
        project_root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            ["node", str(project_root / "scripts/round289_frontend_contract_harness.js"), str(project_root)],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stderr or result.stdout
        assert "ROUND289_FRONTEND_CONTRACT_PASS" in result.stdout


if __name__ == "__main__":
    asyncio.run(Round289Checks.run())
    print("Round 289 effective-model routing and live-UI checks passed")
