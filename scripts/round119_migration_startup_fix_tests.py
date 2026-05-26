#!/usr/bin/env python3
"""Round 119 checks for robust SQL migration startup parsing."""

from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def require(condition: bool, message: str) -> None:
    """Raise a clear assertion for the Round 119 review pass."""
    if not condition:
        raise AssertionError(message)


def read(rel: str) -> str:
    """Read a repository file as UTF-8 text."""
    return (ROOT / rel).read_text(encoding="utf-8")


def import_database_with_aiosqlite_stub():
    """Import the database module in lightweight sandboxes without aiosqlite."""
    if "aiosqlite" not in sys.modules:
        stub = types.ModuleType("aiosqlite")
        stub.OperationalError = RuntimeError
        stub.Connection = object
        stub.Cursor = object
        sys.modules["aiosqlite"] = stub
    from src.core.database import Database

    return Database


def test_migration_splitter_ignores_comment_semicolons() -> None:
    """A semicolon in a migration comment must not become executable SQL."""
    Database = import_database_with_aiosqlite_stub()
    migration = read("migrations/107_scheduled_task_timing.sql")
    sample = """-- comment with a semicolon; this is prose only
ALTER TABLE demo ADD COLUMN name TEXT DEFAULT 'a;b';
/* block comment; ignored */
CREATE INDEX IF NOT EXISTS idx_demo_name ON demo(name);
"""
    statements = Database._split_sql_migration_statements(sample)
    require(statements == [
        "ALTER TABLE demo ADD COLUMN name TEXT DEFAULT 'a;b'",
        "CREATE INDEX IF NOT EXISTS idx_demo_name ON demo(name)",
    ], f"unexpected split result: {statements!r}")

    round_107 = Database._split_sql_migration_statements(migration)
    require(len(round_107) == 10, f"migration 107 should split into 10 executable statements, got {len(round_107)}")
    require(all(not stmt.startswith("--") for stmt in round_107), "comments should not be sent to sqlite.execute")
    require(all("optional/defaulted" not in stmt for stmt in round_107), "comment prose must not be executable SQL")
    require(round_107[0].startswith("ALTER TABLE scheduled_tasks ADD COLUMN task_type"), "first 107 statement should add task_type")
    require(round_107[-1].startswith("CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_due"), "last 107 statement should create due index")


def test_database_runner_no_longer_uses_plain_semicolon_split() -> None:
    """The runner should route every migration through the safe splitter."""
    database = read("src/core/database.py")
    require("def _split_sql_migration_statements" in database, "database should expose a safe migration splitter")
    require("self._split_sql_migration_statements(sql)" in database, "migration runner should use the safe splitter")
    require('sql.split(";")' not in database, "plain semicolon splitting should not be used for migrations")


def test_migration_docs_record_comment_semicolon_rule() -> None:
    """Docs should explain why comments/prose must not break startup migrations."""
    docs = read("migrations/README.md")
    history = read("docs/project-history/ROUND119_MIGRATION_STARTUP_FIX.md")
    for source, label in ((docs, "migrations README"), (history, "Round 119 history")):
        require("comment" in source.lower(), f"{label} should mention migration comments")
        require("semicolon" in source.lower(), f"{label} should mention semicolons")
        require("Round 119" in source or "round 119" in source.lower(), f"{label} should mention the round")


if __name__ == "__main__":
    for name in sorted(k for k in globals() if k.startswith("test_")):
        globals()[name]()
        print(f"PASS {name}")
