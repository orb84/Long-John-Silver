"""Round 221 deterministic checks for managed SearXNG hardening.

The checks avoid live internet and do not start SearXNG. They verify the managed
runtime roots, backup/rollback mechanics, and new system/UI action seams.
"""

from __future__ import annotations

import asyncio
import platform
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.models import Settings
from src.search.web import searxng_manager as manager_module
from src.search.web.searxng_manager import SearXNGManager


class Round221SearXNGHardeningTests:
    """Small deterministic test suite for upgrade/rollback hardening."""

    def __init__(self) -> None:
        self._failures: list[str] = []

    def run(self) -> None:
        self._test_managed_default_path_uses_project_data_root()
        asyncio.run(self._test_backup_and_restore_runtime())
        self._test_action_and_route_registration()
        self._test_compass_controls_present()
        if self._failures:
            raise AssertionError("\n".join(self._failures))
        print("Round 221 SearXNG upgrade/rollback hardening tests passed")

    def _test_managed_default_path_uses_project_data_root(self) -> None:
        expected = ROOT / "data" / "searxng"
        self._check(manager_module.SEARXNG_DATA_DIR == expected, "managed SearXNG data dir must be project-level data/searxng")
        self._check("/src/data/searxng" not in str(manager_module.SEARXNG_DATA_DIR).replace("\\", "/"), "managed SearXNG must not install under src/data")

    async def _test_backup_and_restore_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            manager = SearXNGManager(root / "svc", root / "state")
            settings = Settings()
            await manager.configure(settings)
            source_dir = manager._source_dir()
            source_dir.mkdir(parents=True)
            (source_dir / "searx").mkdir()
            (source_dir / "marker.txt").write_text("before", encoding="utf-8")
            python_path = manager._venv_python()
            python_path.parent.mkdir(parents=True, exist_ok=True)
            python_path.write_text("python", encoding="utf-8")
            backup = manager._backup_runtime(reason="test")
            (source_dir / "marker.txt").write_text("after", encoding="utf-8")
            await manager.stop()
            manager._restore_runtime_backup(backup)
            self._check((source_dir / "marker.txt").read_text(encoding="utf-8") == "before", "rollback must restore backed-up source checkout")
            self._check(manager.config_path().exists(), "rollback must restore generated settings.yml")
            self._check(manager._venv_python().exists(), "rollback must restore the managed venv")
            health = await manager.health_check(settings)
            self._check(health["rollback_available"] is True, "health should report rollback availability after backup")
            self._check(health["source_ref"], "health should expose the managed source ref")

    def _test_action_and_route_registration(self) -> None:
        registration = (ROOT / "src/core/actions/registration.py").read_text(encoding="utf-8")
        router = (ROOT / "src/web/routers/system.py").read_text(encoding="utf-8")
        handler = (ROOT / "src/web/action_handlers/system.py").read_text(encoding="utf-8")
        for name in ("system_upgrade_searxng", "system_rollback_searxng", "system_uninstall_searxng"):
            self._check(name in registration, f"{name} should be registered through ActionGateway")
        for path in ("/api/searxng/upgrade", "/api/searxng/rollback", "/api/searxng/uninstall"):
            self._check(path in router, f"{path} should be exposed as a system route")
        for method in ("upgrade_searxng", "rollback_searxng", "uninstall_searxng"):
            self._check(f"async def {method}" in handler, f"SystemActionHandler should implement {method}")

    def _test_compass_controls_present(self) -> None:
        js = (ROOT / "src/web/static/js/components/settingsPanel.js").read_text(encoding="utf-8")
        self._check("upgradeSearxng" in js, "Compass should expose managed SearXNG upgrade")
        self._check("rollbackSearxng" in js, "Compass should expose managed SearXNG rollback")
        self._check("pref-web-search-source-ref" in js, "Compass should expose managed source ref for clean-machine pinning")
        self._check("/api/searxng/upgrade" in js, "Compass upgrade should call the managed endpoint")
        self._check("/api/searxng/rollback" in js, "Compass rollback should call the managed endpoint")

    def _check(self, condition: Any, message: str) -> None:
        if not condition:
            self._failures.append(message)


if __name__ == "__main__":
    Round221SearXNGHardeningTests().run()
