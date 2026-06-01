#!/usr/bin/env python3
"""Round 200 regression tests for Jackett runtime-dir authority.

These tests cover the macOS failure shown in the round-199 user log: stale
indexer files beside LJS' downloaded Jackett executable must not be selected as
legacy state when Jackett is actually using ~/Library/Application Support.
"""

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

import src.search.jackett_manager as jm
from src.search.jackett_manager import JackettManager
from src.search.jackett_indexer_config import JackettIndexerInfo


def _write_server_config(root: Path, api_key: str = "runtime-key") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "ServerConfig.json").write_text(json.dumps({"APIKey": api_key, "AdminPassword": None, "AllowExternal": False}) + "\n")


def _write_indexer(root: Path, indexer_id: str) -> None:
    folder = root / "Indexers"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{indexer_id}.json").write_text("[]\n")


class _StaticCatalogueConfigurer:
    def __init__(self, entries):
        self._entries = entries

    async def fetch_indexer_catalogue(self):
        return list(self._entries)

    @staticmethod
    def _is_public_like(entry: JackettIndexerInfo):
        return entry.type == "public"


async def test_macos_ignores_stale_executable_adjacent_indexer_files_for_state_selection() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        install_state = base / "data" / "jackett" / "Jackett"
        app_support = base / "Library" / "Application Support" / "Jackett"
        managed = base / "data" / "jackett_state"
        _write_server_config(install_state)
        _write_indexer(install_state, "stale-public-bootstrap")
        _write_server_config(app_support)

        manager = JackettManager()
        manager._managed_state_dir = managed  # type: ignore[attr-defined]
        manager._default_config_dirs = lambda: [app_support]  # type: ignore[method-assign]
        manager._executable_path = lambda: base / "data" / "jackett" / "jackett"  # type: ignore[method-assign]

        with patch("platform.system", return_value="Darwin"), patch.object(jm, "JACKETT_DIR", base / "data" / "jackett"):
            legacy_dirs = manager._legacy_config_dirs()  # type: ignore[attr-defined]
            assert install_state not in legacy_dirs
            assert app_support in legacy_dirs
            manager._select_state_mode()  # type: ignore[attr-defined]
            assert manager._state_mode == "managed"  # type: ignore[attr-defined]
            assert "zero-indexer legacy ServerConfig" in manager._state_reason  # type: ignore[attr-defined]


async def test_runtime_log_dir_overrides_same_api_key_mirrored_elsewhere() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        runtime = base / "Library" / "Application Support" / "Jackett"
        managed_root = base / "data" / "jackett_state"
        managed = managed_root / "config" / "Jackett"
        stale = base / "data" / "jackett" / "Jackett"
        for root in (runtime, managed, stale):
            _write_server_config(root, "same-key")
        _write_indexer(stale, "stale-public-bootstrap")
        (runtime / "log.txt").write_text(f"2026 Info App config/log directory: {runtime}\n")

        manager = JackettManager()
        manager._managed_state_dir = managed_root  # type: ignore[attr-defined]
        manager._default_config_dirs = lambda: [runtime]  # type: ignore[method-assign]
        manager._executable_path = lambda: base / "data" / "jackett" / "jackett"  # type: ignore[method-assign]
        manager._running = True  # type: ignore[attr-defined]
        manager._api_key = "same-key"  # type: ignore[attr-defined]
        manager._api_key_is_accepted = lambda key: asyncio.sleep(0, result=(key == "same-key"))  # type: ignore[method-assign]

        with patch("platform.system", return_value="Darwin"), patch.object(jm, "JACKETT_DIR", base / "data" / "jackett"):
            manager._select_state_mode()  # type: ignore[attr-defined]
            ok = await manager._adopt_valid_runtime_api_key()  # type: ignore[attr-defined]
            assert ok is True
            assert manager._runtime_config_dir == runtime  # type: ignore[attr-defined]
            assert manager._runtime_config_dir_bootstrap_safe is True  # type: ignore[attr-defined]

            entries = [JackettIndexerInfo(
                id="examplepublic",
                name="Example Public",
                configured=False,
                type="public",
                language="en-US",
                categories=(),
                tags=(),
                link="https://example.test/",
            )]
            result = await manager.bootstrap_public_indexer_files("all_open_public", _StaticCatalogueConfigurer(entries))
            assert result["written"] == 1
            assert (runtime / "Indexers" / "examplepublic.json").exists()
            assert not (stale / "Indexers" / "examplepublic.json").exists()


async def main() -> None:
    await test_macos_ignores_stale_executable_adjacent_indexer_files_for_state_selection()
    await test_runtime_log_dir_overrides_same_api_key_mirrored_elsewhere()
    print("round200 Jackett runtime-authority tests passed")


if __name__ == "__main__":
    asyncio.run(main())
