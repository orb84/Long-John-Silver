#!/usr/bin/env python3
"""Round 197 regression tests for macOS Jackett zero-state recovery."""

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

from src.search.jackett_indexer_config import JackettIndexerConfigurer, JackettIndexerInfo
from src.search.jackett_manager import JackettManager


def _write_server_config(root: Path, api_key: str = "test-api-key") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "ServerConfig.json").write_text(json.dumps({"APIKey": api_key, "AdminPassword": None, "AllowExternal": False}))


def test_zero_indexer_legacy_server_config_is_not_preserved_as_runtime_state() -> None:
    """macOS fresh Jackett configs with no Indexers must not poison LJS startup."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        legacy = base / "Library" / "Application Support" / "Jackett"
        _write_server_config(legacy, "legacy-zero-key")

        manager = JackettManager()
        manager._managed_state_dir = base / "managed"  # type: ignore[attr-defined]
        manager._legacy_config_dirs = lambda: [legacy]  # type: ignore[method-assign]

        with patch("platform.system", return_value="Darwin"):
            manager._select_state_mode()  # type: ignore[attr-defined]
            assert manager._state_mode == "managed"  # type: ignore[attr-defined]
            assert "zero-indexer legacy" in manager._state_reason  # type: ignore[attr-defined]
            assert legacy not in manager._candidate_config_dirs()  # type: ignore[attr-defined]


def test_torznab_indexer_parser_preserves_link_for_file_bootstrap() -> None:
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<indexers>
  <indexer id="examplepublic" configured="false">
    <title>Example Public</title>
    <description>Example</description>
    <link>https://example.test/</link>
    <language>en-US</language>
    <type>public</type>
  </indexer>
</indexers>
"""
    cfg = JackettIndexerConfigurer("http://127.0.0.1:9117", "api")
    parsed = cfg._parse_torznab_indexers(xml, configured=False)  # type: ignore[attr-defined]
    assert len(parsed) == 1
    assert parsed[0].id == "examplepublic"
    assert parsed[0].type == "public"
    assert parsed[0].link == "https://example.test/"


def test_managed_startup_config_synchronizes_api_keys_across_macos_candidate_paths() -> None:
    """Managed macOS must not have upper/lower/app-support API-key split-brain."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        manager = JackettManager()
        manager._managed_state_dir = base / "managed"  # type: ignore[attr-defined]
        manager._legacy_config_dirs = lambda: []  # type: ignore[method-assign]

        with patch("platform.system", return_value="Darwin"):
            manager._select_state_mode()  # type: ignore[attr-defined]
            # Simulate residue from an earlier failed run: two plausible config
            # paths exist with different API keys.  LJS used to read one while
            # Jackett used another, making Torznab/admin calls appear broken.
            paths = manager._server_config_paths()[:3]  # type: ignore[attr-defined]
            _write_server_config(paths[0].parent, "canonical-key")
            _write_server_config(paths[1].parent, "stale-lowercase-key")
            manager._ensure_managed_startup_config()  # type: ignore[attr-defined]
            keys = []
            for path in manager._server_config_paths():  # type: ignore[attr-defined]
                data = json.loads(path.read_text())
                keys.append(data["APIKey"])
                assert data["AdminPassword"] is None
                assert data["AllowExternal"] is False
            assert set(keys) == {"canonical-key"}
            assert manager.api_key == "canonical-key"


def test_local_definition_catalogue_uses_only_real_public_jackett_definitions() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        defs = base / "jackett" / "Definitions"
        defs.mkdir(parents=True)
        (defs / "goodpublic.yml").write_text(
            "id: goodpublic\nname: Good Public\ntype: public\nlanguage: en-US\nlinks:\n  - https://good.example/\n"
        )
        (defs / "privateone.yml").write_text(
            "id: privateone\nname: Private One\ntype: private\nlinks:\n  - https://private.example/\n"
        )
        (defs / "nolink.yml").write_text("id: nolink\nname: No Link\ntype: public\n")

        manager = JackettManager()
        fake_exe = defs.parent / "jackett"
        fake_exe.write_text("#!/bin/sh\n")
        manager._executable_path = lambda: fake_exe  # type: ignore[method-assign]

        entries = manager._public_indexer_catalogue_from_local_definitions()  # type: ignore[attr-defined]
        assert [entry.id for entry in entries] == ["goodpublic"]
        assert entries[0].link == "https://good.example/"


class _EmptyCatalogueConfigurer:
    async def fetch_indexer_catalogue(self):
        return []

    @staticmethod
    def _is_public_like(entry):
        return entry.type == "public"


async def test_file_bootstrap_falls_back_to_local_definitions_when_jackett_catalogue_is_empty() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        defs = base / "jackett" / "Definitions"
        defs.mkdir(parents=True)
        (defs / "goodpublic.yml").write_text(
            "id: goodpublic\nname: Good Public\ntype: public\nlinks:\n  - https://good.example/\n"
        )
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
            result = await manager.bootstrap_public_indexer_files(
                profile="all_open_public",
                catalogue_configurer=_EmptyCatalogueConfigurer(),
            )
            assert result["catalogue_source"] == "local_definitions"
            assert result["written"] == 1
            written = Path(result["target"]) / "goodpublic.json"
            assert written.exists()
            payload = json.loads(written.read_text())
            assert payload[0]["value"] == "https://good.example/"


class _FakeCatalogueConfigurer:
    async def fetch_indexer_catalogue(self):
        return [
            JackettIndexerInfo(
                id="examplepublic",
                name="Example Public",
                configured=False,
                type="public",
                link="https://example.test/",
            ),
            JackettIndexerInfo(
                id="privateone",
                name="Private One",
                configured=False,
                type="private",
                link="https://private.test/",
            ),
            JackettIndexerInfo(
                id="nolinkpublic",
                name="No Link Public",
                configured=False,
                type="public",
                link="",
            ),
        ]

    @staticmethod
    def _is_public_like(entry):
        text = " ".join([entry.id, entry.name, entry.type, *entry.tags]).lower()
        if any(token in text for token in ("private", "semi-private", "invite", "cookie")):
            return False
        return entry.type in {"public", "unknown", ""} or "public" in text


async def test_managed_public_indexer_file_bootstrap_writes_only_safe_public_configs() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        manager = JackettManager()
        manager._managed_state_dir = base / "managed"  # type: ignore[attr-defined]
        manager._legacy_config_dirs = lambda: []  # type: ignore[method-assign]

        with patch("platform.system", return_value="Darwin"):
            manager._select_state_mode()  # type: ignore[attr-defined]
            manager._prepare_managed_state_home()  # type: ignore[attr-defined]
            manager._ensure_managed_startup_config()  # type: ignore[attr-defined]
            result = await manager.bootstrap_public_indexer_files(
                profile="all_open_public",
                catalogue_configurer=_FakeCatalogueConfigurer(),
            )
            assert result["written"] == 1
            assert result["skipped_no_link"] == 1
            target = Path(result["target"])
            written = target / "examplepublic.json"
            assert written.exists()
            payload = json.loads(written.read_text())
            assert payload[0]["id"] == "sitelink"
            assert payload[0]["value"] == "https://example.test/"
            assert not (target / "privateone.json").exists()
            assert not (target / "nolinkpublic.json").exists()


async def main() -> None:
    test_zero_indexer_legacy_server_config_is_not_preserved_as_runtime_state()
    test_torznab_indexer_parser_preserves_link_for_file_bootstrap()
    test_managed_startup_config_synchronizes_api_keys_across_macos_candidate_paths()
    test_local_definition_catalogue_uses_only_real_public_jackett_definitions()
    await test_managed_public_indexer_file_bootstrap_writes_only_safe_public_configs()
    await test_file_bootstrap_falls_back_to_local_definitions_when_jackett_catalogue_is_empty()
    print("round197 macOS Jackett managed-state bootstrap tests passed")


if __name__ == "__main__":
    asyncio.run(main())
