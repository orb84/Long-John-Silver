"""Round 218 deterministic checks for managed SearXNG integration.

These tests avoid live internet and do not start SearXNG. They verify the
managed-install contract that matters before any system with an existing SearXNG
instance can mask installer defects.
"""

from __future__ import annotations

import asyncio
import socket
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.models import Settings
from src.search.web.searxng_manager import SearXNGManager
from src.search.web.searxng import SearXNGSearchProvider


class Round218SearXNGManagedInstallTests:
    """Small deterministic test runner for the managed SearXNG contract."""

    def __init__(self) -> None:
        self._failures: list[str] = []

    async def run(self) -> None:
        """Run all checks and raise on the first collected failure set."""
        await self._settings_yaml_contract()
        await self._port_collision_reassigns()
        await self._health_does_not_adopt_external_process()
        self._provider_parameter_and_dedupe_contract()
        if self._failures:
            raise AssertionError("\n".join(self._failures))
        print("round218_searxng_managed_install_tests: OK")

    async def _settings_yaml_contract(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            manager = SearXNGManager(Path(root) / "svc", Path(root) / "state")
            settings = Settings()
            ok = await manager.configure(settings)
            self._check(ok, "configure() should write settings.yml")
            text = manager.config_path().read_text(encoding="utf-8")
            self._check("- json" in text, "managed settings must enable JSON output")
            self._check('bind_address: "127.0.0.1"' in text, "managed settings must bind to localhost")
            self._check("public_instance: false" in text, "managed settings must not be public")
            self._check("limiter: false" in text, "managed settings must not require Valkey limiter by default")
            self._check("url: false" in text, "managed settings must disable Valkey by default")
            self._check(settings.web_search.provider == "searxng", "configure() should select SearXNG provider")
            self._check(settings.web_search.mode == "managed", "configure() should select managed mode")
            self._check(settings.web_search.api_base.startswith("http://127.0.0.1:"), "managed api_base should be localhost")

    async def _port_collision_reassigns(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            settings = Settings()
            settings.web_search.managed_port = 18888
            manager = SearXNGManager(Path(root) / "svc", Path(root) / "state")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.bind(("127.0.0.1", 18888))
                sock.listen(1)
                ok = await manager.configure(settings)
                self._check(ok, "configure() should succeed with occupied default port")
                self._check(settings.web_search.managed_port != 18888, "managed mode must not adopt occupied default port")
                self._check(settings.web_search.api_base != "http://127.0.0.1:18888", "managed URL should move off occupied port")
            finally:
                sock.close()

    async def _health_does_not_adopt_external_process(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            manager = SearXNGManager(Path(root) / "svc", Path(root) / "state")
            health = await manager.health_check(Settings())
            self._check(health["running"] is False, "health must report not running without an owned child process")
            self._check(health["ready"] is False, "health must not mark external/manual processes ready")

    def _provider_parameter_and_dedupe_contract(self) -> None:
        provider = SearXNGSearchProvider(
            "http://127.0.0.1:18888",
            categories=["general", "news"],
            language="it-IT",
            safe_search=2,
            timeout_seconds=3,
        )
        params = provider._params("test query", page=2)
        self._check(params["format"] == "json", "provider must always request JSON")
        self._check(params["categories"] == "general,news", "provider must pass configured categories")
        self._check(params["language"] == "it-IT", "provider must pass configured language")
        self._check(params["safesearch"] == 2, "provider must pass safe-search level")
        hits = provider._normalize_hits([
            {"title": "A", "url": "https://example.test/a", "content": "one", "engines": ["brave"]},
            {"title": "A duplicate", "url": "https://example.test/a", "content": "dupe"},
            {"title": "B", "url": "https://example.test/b", "content": "two", "engine": "duckduckgo"},
        ], max_results=5)
        self._check(len(hits) == 2, "provider should dedupe repeated URLs")
        self._check(hits[0].source == "brave", "provider should preserve engine source names")
        self._check(hits[1].engines == ["duckduckgo"], "provider should normalize scalar engine field")

    def _check(self, condition: bool, message: str) -> None:
        if not condition:
            self._failures.append(message)


if __name__ == "__main__":
    asyncio.run(Round218SearXNGManagedInstallTests().run())
