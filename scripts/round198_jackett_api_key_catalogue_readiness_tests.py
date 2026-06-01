#!/usr/bin/env python3
"""Round 198 regression tests for Jackett API-key/readiness/catalogue hardening."""

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

from src.search.jackett_manager import JackettManager
from src.search.jackett_indexer_config import JackettIndexerInfo


def _write_server_config(root: Path, api_key: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "ServerConfig.json").write_text(json.dumps({"APIKey": api_key, "AdminPassword": None, "AllowExternal": False}))


def _write_indexer(root: Path, indexer_id: str) -> None:
    folder = root / "Indexers"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{indexer_id}.json").write_text("[]\n")


def test_macos_case_insensitive_paths_do_not_double_count_indexers() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "managed" / "config" / "Jackett"
        _write_indexer(root, "example")
        manager = JackettManager()
        with patch("platform.system", return_value="Darwin"):
            # Simulate the same macOS directory being addressed with different case.
            manager._state_mode = "managed"  # type: ignore[attr-defined]
            manager._active_config_dirs = [root, base / "managed" / "config" / "jackett"]  # type: ignore[attr-defined]
            assert manager._configured_indexer_file_count(root) == 1  # type: ignore[attr-defined]
            assert manager._configured_indexer_file_count_across_active_dirs() == 1  # type: ignore[attr-defined]


async def test_rejected_api_key_prevents_file_count_from_registering_fake_readiness() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "managed" / "config" / "Jackett"
        _write_server_config(root, "bad-key")
        _write_indexer(root, "example")
        manager = JackettManager()
        manager._managed_state_dir = base / "managed"  # type: ignore[attr-defined]
        manager._legacy_config_dirs = lambda: []  # type: ignore[method-assign]
        manager._running = True  # type: ignore[attr-defined]
        manager._process = None  # type: ignore[attr-defined]
        manager._attached_existing = True  # type: ignore[attr-defined]

        async def reject(_key):
            return False

        manager._api_key_is_accepted = reject  # type: ignore[method-assign]
        with patch("platform.system", return_value="Darwin"):
            manager._select_state_mode()  # type: ignore[attr-defined]
            count = await manager.configured_indexer_count()
            assert count == 0


async def test_manager_adopts_the_api_key_the_running_jackett_accepts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        canonical = base / "managed" / "config" / "Jackett"
        unexpected = base / "managed" / "home" / ".config" / "Jackett"
        _write_server_config(canonical, "stale-key")
        _write_server_config(unexpected, "runtime-key")
        manager = JackettManager()
        manager._managed_state_dir = base / "managed"  # type: ignore[attr-defined]
        manager._legacy_config_dirs = lambda: []  # type: ignore[method-assign]
        manager._running = True  # type: ignore[attr-defined]
        manager._attached_existing = True  # type: ignore[attr-defined]

        async def accept_only_runtime(key):
            return key == "runtime-key"

        manager._api_key_is_accepted = accept_only_runtime  # type: ignore[method-assign]
        with patch("platform.system", return_value="Darwin"):
            manager._select_state_mode()  # type: ignore[attr-defined]
            ok = await manager._adopt_valid_runtime_api_key()  # type: ignore[attr-defined]
            assert ok is True
            assert manager.api_key == "runtime-key"
            for path in manager._server_config_paths():  # type: ignore[attr-defined]
                if path.exists():
                    assert json.loads(path.read_text())["APIKey"] == "runtime-key"


def test_local_definitions_catalogue_exposes_all_indexers_but_public_wrapper_filters() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        defs = base / "jackett" / "Definitions"
        defs.mkdir(parents=True)
        (defs / "public.yml").write_text("id: public\nname: Public\ntype: public\nlinks:\n  - https://public.example/\n")
        (defs / "private.yml").write_text("id: private\nname: Private\ntype: private\nlinks:\n  - https://private.example/\n")
        (defs / "semi.yml").write_text("id: semi\nname: Semi\ntype: semi-private\nlinks:\n  - https://semi.example/\n")
        fake_exe = defs.parent / "jackett"
        fake_exe.write_text("#!/bin/sh\n")
        manager = JackettManager()
        manager._executable_path = lambda: fake_exe  # type: ignore[method-assign]
        all_entries = manager._indexer_catalogue_from_local_definitions(public_only=False)  # type: ignore[attr-defined]
        public_entries = manager._public_indexer_catalogue_from_local_definitions()  # type: ignore[attr-defined]
        assert {entry.id for entry in all_entries} == {"public", "private", "semi"}
        assert [entry.id for entry in public_entries] == ["public"]


class _EmptyCatalogueConfigurer:
    async def fetch_indexer_catalogue(self):
        return []

    @staticmethod
    def _is_public_like(entry: JackettIndexerInfo):
        return entry.type == "public"


async def test_file_bootstrap_reports_full_local_catalogue_but_writes_only_public() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        defs = base / "jackett" / "Definitions"
        defs.mkdir(parents=True)
        (defs / "public.yml").write_text("id: public\nname: Public\ntype: public\nlinks:\n  - https://public.example/\n")
        (defs / "private.yml").write_text("id: private\nname: Private\ntype: private\nlinks:\n  - https://private.example/\n")
        fake_exe = defs.parent / "jackett"
        fake_exe.write_text("#!/bin/sh\n")
        manager = JackettManager()
        manager._managed_state_dir = base / "managed"  # type: ignore[attr-defined]
        manager._legacy_config_dirs = lambda: []  # type: ignore[method-assign]
        manager._executable_path = lambda: fake_exe  # type: ignore[method-assign]
        with patch("platform.system", return_value="Darwin"):
            manager._select_state_mode()  # type: ignore[attr-defined]
            manager._prepare_managed_state_home()  # type: ignore[attr-defined]
            manager._ensure_managed_startup_config()  # type: ignore[attr-defined]
            result = await manager.bootstrap_public_indexer_files("all_open_public", _EmptyCatalogueConfigurer())
            assert result["available_catalogue_count"] == 2
            assert result["requested_public_count"] == 1
            assert result["written"] == 1
            assert (Path(result["target"]) / "public.json").exists()
            assert not (Path(result["target"]) / "private.json").exists()


async def main() -> None:
    test_macos_case_insensitive_paths_do_not_double_count_indexers()
    await test_rejected_api_key_prevents_file_count_from_registering_fake_readiness()
    await test_manager_adopts_the_api_key_the_running_jackett_accepts()
    test_local_definitions_catalogue_exposes_all_indexers_but_public_wrapper_filters()
    await test_file_bootstrap_reports_full_local_catalogue_but_writes_only_public()
    print("round198 Jackett API-key/catalogue readiness tests passed")


if __name__ == "__main__":
    asyncio.run(main())
