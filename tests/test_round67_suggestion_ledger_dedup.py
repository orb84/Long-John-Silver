"""Regression coverage for lifecycle suggestion ledger idempotency."""

from __future__ import annotations

import asyncio
import sqlite3

from src.core.category_lifecycle import CategoryLifecycleEngine
from src.core.models import CategoryItem, SuggestedActionRecord


class _NoopDb:
    """Only used to instantiate the lifecycle engine for pure helper tests."""


def test_lifecycle_suggestion_dedup_keeps_one_row_per_key() -> None:
    """Duplicate user-facing suggestions must not violate the ledger primary key.

    The lifecycle ledger stores suggestions by ``suggestion_key``.  If a category
    workflow returns the same action twice in a single compile pass, the engine
    should collapse it before writing to both suggestion stores.
    """
    engine = CategoryLifecycleEngine(_NoopDb())
    first = SuggestedActionRecord(
        category_id="tv",
        item_id="show",
        action_type="related_media",
        title="Add related show",
        endpoint="/api/categories/tv/items",
        body_json='{"name":"Companion"}',
        priority=10,
    )
    duplicate_higher_priority = SuggestedActionRecord(
        category_id="tv",
        item_id="show",
        action_type="related_media",
        title="Add related show",
        endpoint="/api/categories/tv/items",
        body_json='{"name":"Companion"}',
        priority=40,
    )
    distinct = SuggestedActionRecord(
        category_id="tv",
        item_id="show",
        action_type="related_media",
        title="Add another related show",
        endpoint="/api/categories/tv/items",
        body_json='{"name":"Other"}',
        priority=20,
    )

    deduped = engine._dedupe_suggestions([first, duplicate_higher_priority, distinct])

    assert len(deduped) == 2
    assert deduped[0].priority == 40
    assert deduped[1].title == "Add another related show"


def test_lifecycle_suggestion_state_uses_conflict_safe_upsert() -> None:
    """The shadow ledger write path should remain defensive even after dedup."""
    source = __import__("pathlib").Path("src/core/category_lifecycle.py").read_text(encoding="utf-8")

    assert "ON CONFLICT(category_id, item_id, suggestion_key) DO UPDATE" in source


class _AsyncSqliteConnection:
    """Tiny async wrapper around sqlite3 for constraint-level regression tests."""

    def __init__(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row

    async def execute(self, sql: str, params: tuple = ()):
        return self.connection.execute(sql, params)

    async def commit(self) -> None:
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()


class _DbWithConnection:
    """Database facade exposing the lifecycle engine's expected connection seam."""

    def __init__(self, connection: _AsyncSqliteConnection) -> None:
        self._connection = connection

    async def get_connection(self) -> _AsyncSqliteConnection:
        return self._connection


def test_replace_suggestion_state_accepts_duplicate_keys_without_crashing() -> None:
    """A duplicate suggestion key in one compile pass should be an update, not a crash."""

    async def scenario() -> int:
        connection = _AsyncSqliteConnection()
        try:
            await connection.execute(
                """CREATE TABLE category_item_suggestion_state (
                    category_id TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    suggestion_key TEXT NOT NULL,
                    suggestion_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    title TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    suggestion_fingerprint TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    valid_until TEXT,
                    invalidated_by TEXT NOT NULL DEFAULT '[]',
                    policy_version INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (category_id, item_id, suggestion_key)
                )"""
            )
            engine = CategoryLifecycleEngine(_DbWithConnection(connection))
            first = SuggestedActionRecord(
                category_id="tv",
                item_id="show",
                action_type="related_media",
                title="Add related show",
                endpoint="/api/categories/tv/items",
                body_json='{"name":"Companion"}',
                priority=10,
            )
            duplicate = first.model_copy(update={"priority": 40})

            await engine._replace_suggestion_state(
                item=CategoryItem(key="show"),
                suggestions=[first, duplicate],
                valid_until=None,
                suggestion_fingerprint="fingerprint",
                policy_version=1,
            )
            cursor = await connection.execute("SELECT COUNT(*) AS count FROM category_item_suggestion_state")
            row = cursor.fetchone()
            return int(row["count"])
        finally:
            connection.close()

    assert asyncio.run(scenario()) == 1
