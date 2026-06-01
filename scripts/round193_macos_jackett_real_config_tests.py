#!/usr/bin/env python3
"""Round 193 regression tests for real macOS Jackett configuration readiness."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.search.jackett import JackettSearch
from src.search.jackett_manager import JackettManager


def test_macos_managed_config_prefers_xdg_and_repairs_library_too() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        manager = JackettManager()
        manager._managed_state_dir = Path(tmp) / "jackett_state"  # type: ignore[attr-defined]
        with patch("platform.system", return_value="Darwin"):
            manager._prepare_managed_state_home()  # type: ignore[attr-defined]
            assert manager._config_dir() == manager._managed_state_dir / "config" / "Jackett"  # type: ignore[attr-defined]
            paths = manager._server_config_paths()  # type: ignore[attr-defined]
            rendered = "\n".join(str(path) for path in paths)
            assert "/config/Jackett/ServerConfig.json" in rendered
            assert "Library/Application Support/Jackett/ServerConfig.json" in rendered

            for cfg in paths[:2]:
                cfg.parent.mkdir(parents=True, exist_ok=True)
                cfg.write_text(json.dumps({"APIKey": "abc", "AdminPassword": "hash", "AllowExternal": True}))
            changed = manager._repair_managed_admin_auth_config()  # type: ignore[attr-defined]
            assert changed is True
            for cfg in paths[:2]:
                data = json.loads(cfg.read_text())
                assert data["AdminPassword"] is None, data
                assert data["AllowExternal"] is False, data


def test_managed_startup_config_created_before_first_start() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        manager = JackettManager()
        manager._managed_state_dir = Path(tmp) / "jackett_state"  # type: ignore[attr-defined]
        with patch("platform.system", return_value="Darwin"):
            manager._prepare_managed_state_home()  # type: ignore[attr-defined]
            assert not any(path.exists() for path in manager._server_config_paths())  # type: ignore[attr-defined]
            manager._ensure_managed_startup_config()  # type: ignore[attr-defined]
            cfg = manager._config_dir() / "ServerConfig.json"  # type: ignore[attr-defined]
            data = json.loads(cfg.read_text())
            assert data["AdminPassword"] is None, data
            assert data["AllowExternal"] is False, data
            assert data["APIKey"], data


async def test_jackett_search_does_not_use_filter_indexer_as_zero_config_workaround() -> None:
    provider = JackettSearch("http://127.0.0.1:9117", "abc", configured_indexers=0, allow_filter_indexers=True)
    called = False

    async def fake_filter(*args, **kwargs):
        nonlocal called
        called = True
        return []

    provider._search_filter_indexers = fake_filter  # type: ignore[method-assign]
    results = await provider.search("Project Hail Mary", category="movie")
    assert results == []
    assert called is False


def test_main_no_longer_registers_filter_indexer_mode_or_masks_unready_jackett() -> None:
    main_text = (ROOT / "main.py").read_text()
    jackett_text = (ROOT / "src/search/jackett.py").read_text()
    assert "Registering Jackett in official filter-indexer mode" not in main_text
    assert "filter selectors are not used as a substitute" in jackett_text
    assert "not activated because managed Jackett" in main_text


async def main() -> None:
    test_macos_managed_config_prefers_xdg_and_repairs_library_too()
    test_managed_startup_config_created_before_first_start()
    await test_jackett_search_does_not_use_filter_indexer_as_zero_config_workaround()
    test_main_no_longer_registers_filter_indexer_mode_or_masks_unready_jackett()
    print("round193 macOS Jackett real-config readiness tests passed")


if __name__ == "__main__":
    asyncio.run(main())
