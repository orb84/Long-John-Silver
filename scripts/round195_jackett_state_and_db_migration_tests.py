#!/usr/bin/env python3
"""Round 195 regression tests for Jackett state preservation and DB startup hardening."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.database import Database
from src.search.jackett_manager import JackettManager


def _write_server_config(root: Path, api_key: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "ServerConfig.json").write_text(json.dumps({"APIKey": api_key, "AdminPassword": None}))


def test_jackett_preserves_legacy_state_when_configured_indexers_exist() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        legacy = base / "legacy" / "Jackett"
        managed = base / "managed" / "config" / "Jackett"
        _write_server_config(legacy, "legacy-key")
        _write_server_config(managed, "managed-key")
        (legacy / "Indexers").mkdir(parents=True)
        (legacy / "Indexers" / "eztv.json").write_text("{}")

        manager = JackettManager()
        manager._managed_state_dir = base / "managed"  # type: ignore[attr-defined]
        manager._legacy_config_dirs = lambda: [legacy]  # type: ignore[method-assign]

        with patch("platform.system", return_value="Linux"):
            manager._select_state_mode()  # type: ignore[attr-defined]
            assert manager._state_mode == "legacy"  # type: ignore[attr-defined]
            assert manager._candidate_config_dirs() == [legacy]  # type: ignore[attr-defined]
            assert manager._server_config_paths() == []  # type: ignore[attr-defined]
            assert manager._read_api_key() == "legacy-key"  # type: ignore[attr-defined]


def test_jackett_uses_managed_state_for_fresh_installs() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        manager = JackettManager()
        manager._managed_state_dir = base / "managed"  # type: ignore[attr-defined]
        manager._legacy_config_dirs = lambda: []  # type: ignore[method-assign]

        with patch("platform.system", return_value="Darwin"):
            manager._select_state_mode()  # type: ignore[attr-defined]
            assert manager._state_mode == "managed"  # type: ignore[attr-defined]
            manager._prepare_managed_state_home()  # type: ignore[attr-defined]
            manager._ensure_managed_startup_config()  # type: ignore[attr-defined]
            cfg = manager._config_dir() / "ServerConfig.json"  # type: ignore[attr-defined]
            data = json.loads(cfg.read_text())
            assert data["APIKey"]
            assert data["AdminPassword"] is None
            assert data["AllowExternal"] is False


async def test_database_repairs_legacy_category_columns_before_index_creation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "ljs.db"
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE downloads (
                id TEXT PRIMARY KEY,
                item_name TEXT NOT NULL,
                magnet TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                created_at TEXT NOT NULL
            );
            CREATE TABLE suggested_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_name TEXT NOT NULL,
                action_type TEXT NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE behavior_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                action TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
            INSERT INTO schema_version (version) VALUES (99);
            """
        )
        conn.commit()
        conn.close()

        db = Database(str(db_path))
        await db.initialize()
        raw = await db.get_connection()
        assert raw is not None
        for table in ("downloads", "suggested_actions", "behavior_log"):
            rows = await (await raw.execute(f"PRAGMA table_info({table})")).fetchall()
            columns = {row[1] for row in rows}
            assert "category_id" in columns, (table, columns)
            assert "item_id" in columns, (table, columns)
        version_row = await (await raw.execute("SELECT MAX(version) FROM schema_version")).fetchone()
        assert version_row[0] >= 108
        await db.close()


async def main() -> None:
    test_jackett_preserves_legacy_state_when_configured_indexers_exist()
    test_jackett_uses_managed_state_for_fresh_installs()
    await test_database_repairs_legacy_category_columns_before_index_creation()
    print("round195 Jackett state + DB migration hardening tests passed")


if __name__ == "__main__":
    asyncio.run(main())
