#!/usr/bin/env python3
"""Round 199 regression tests for Jackett runtime-config bootstrap targeting."""

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


class _StaticCatalogueConfigurer:
    def __init__(self, entries):
        self._entries = entries

    async def fetch_indexer_catalogue(self):
        return list(self._entries)

    @staticmethod
    def _is_public_like(entry: JackettIndexerInfo):
        return entry.type == "public"


async def test_zero_indexer_runtime_config_dir_is_added_as_bootstrap_target() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        managed = base / "managed" / "config" / "Jackett"
        runtime = base / "Library" / "Application Support" / "Jackett"
        _write_server_config(managed, "stale-key")
        _write_server_config(runtime, "runtime-key")

        manager = JackettManager()
        manager._managed_state_dir = base / "managed"  # type: ignore[attr-defined]
        manager._legacy_config_dirs = lambda: [runtime]  # type: ignore[method-assign]
        manager._running = True  # type: ignore[attr-defined]
        manager._attached_existing = True  # type: ignore[attr-defined]

        async def accept_only_runtime(key):
            return key == "runtime-key"

        manager._api_key_is_accepted = accept_only_runtime  # type: ignore[method-assign]
        with patch("platform.system", return_value="Darwin"):
            manager._select_state_mode()  # type: ignore[attr-defined]
            assert manager._state_mode == "managed"  # type: ignore[attr-defined]
            ok = await manager._adopt_valid_runtime_api_key()  # type: ignore[attr-defined]
            assert ok is True
            assert runtime in manager._candidate_config_dirs()  # type: ignore[attr-defined]
            targets = {path.parent for path in manager._managed_indexer_config_dirs()}  # type: ignore[attr-defined]
            assert runtime in targets


async def test_public_file_bootstrap_writes_to_runtime_dir_jackett_actually_reads() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        managed = base / "managed" / "config" / "Jackett"
        runtime = base / "Library" / "Application Support" / "Jackett"
        _write_server_config(managed, "runtime-key")
        _write_server_config(runtime, "runtime-key")

        manager = JackettManager()
        manager._managed_state_dir = base / "managed"  # type: ignore[attr-defined]
        manager._legacy_config_dirs = lambda: [runtime]  # type: ignore[method-assign]
        manager._running = True  # type: ignore[attr-defined]
        manager._attached_existing = True  # type: ignore[attr-defined]
        manager._api_key_is_accepted = lambda key: asyncio.sleep(0, result=(key == "runtime-key"))  # type: ignore[method-assign]

        entries = [
            JackettIndexerInfo(
                id="examplepublic",
                name="Example Public",
                configured=False,
                type="public",
                language="en-US",
                categories=(),
                tags=(),
                link="https://example.test/",
            )
        ]

        with patch("platform.system", return_value="Darwin"):
            manager._select_state_mode()  # type: ignore[attr-defined]
            await manager._adopt_valid_runtime_api_key()  # type: ignore[attr-defined]
            result = await manager.bootstrap_public_indexer_files("all_open_public", _StaticCatalogueConfigurer(entries))
            assert result["written"] == 1
            assert (runtime / "Indexers" / "examplepublic.json").exists()
            assert (managed / "Indexers" / "examplepublic.json").exists()


async def test_runtime_dir_with_existing_indexers_is_not_used_for_public_bootstrap() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        managed = base / "managed" / "config" / "Jackett"
        runtime = base / "Library" / "Application Support" / "Jackett"
        _write_server_config(managed, "runtime-key")
        _write_server_config(runtime, "runtime-key")
        _write_indexer(runtime, "private-or-user-indexer")

        manager = JackettManager()
        manager._managed_state_dir = base / "managed"  # type: ignore[attr-defined]
        manager._legacy_config_dirs = lambda: [runtime]  # type: ignore[method-assign]
        manager._running = True  # type: ignore[attr-defined]
        manager._attached_existing = True  # type: ignore[attr-defined]
        manager._api_key_is_accepted = lambda key: asyncio.sleep(0, result=(key == "runtime-key"))  # type: ignore[method-assign]

        entries = [
            JackettIndexerInfo(
                id="examplepublic",
                name="Example Public",
                configured=False,
                type="public",
                language="en-US",
                categories=(),
                tags=(),
                link="https://example.test/",
            )
        ]

        with patch("platform.system", return_value="Darwin"):
            manager._select_state_mode()  # type: ignore[attr-defined]
            # Because the legacy/runtime folder already has an indexer, state
            # selection must preserve it rather than force managed bootstrap.
            assert manager._state_mode == "legacy"  # type: ignore[attr-defined]
            await manager._adopt_valid_runtime_api_key()  # type: ignore[attr-defined]
            result = await manager.bootstrap_public_indexer_files("all_open_public", _StaticCatalogueConfigurer(entries))
            assert result["status"] == "skipped"
            assert not (runtime / "Indexers" / "examplepublic.json").exists()


async def main() -> None:
    await test_zero_indexer_runtime_config_dir_is_added_as_bootstrap_target()
    await test_public_file_bootstrap_writes_to_runtime_dir_jackett_actually_reads()
    await test_runtime_dir_with_existing_indexers_is_not_used_for_public_bootstrap()
    print("round199 Jackett runtime-config bootstrap tests passed")


if __name__ == "__main__":
    asyncio.run(main())
