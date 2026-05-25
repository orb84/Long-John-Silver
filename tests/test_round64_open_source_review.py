"""Regression tests for the open-source cleanup and lifecycle review pass."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from src.core.category_lifecycle import CategoryLifecycleEngine, LifecycleFingerprints
from src.core.models import CategoryItem


class _LifecycleMediaRepo:
    """Minimal media repository: all fingerprints stay stable and empty."""

    async def get_category_metadata(self, category_id: str, item_id: str, provider: str | None = None) -> list[dict]:
        return []

    async def list_category_units(self, category_id: str, item_id: str, **kwargs) -> list[dict]:
        return []


class _LifecycleDownloadsRepo:
    """Minimal downloads repository used by lifecycle fingerprinting."""

    async def get_suggested_actions(self, **kwargs) -> list[dict]:
        return []


class _LifecycleSystemRepo:
    """Minimal system repository: no persisted taste snapshot in this test."""

    async def get_taste_profile_snapshot(self, user_id: str | None, category_id: str) -> None:
        return None


class _LifecycleDb:
    """Database facade exposing only the methods used by CategoryLifecycleEngine."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection
        self.media = _LifecycleMediaRepo()
        self.downloads = _LifecycleDownloadsRepo()
        self.system = _LifecycleSystemRepo()

    async def get_connection(self) -> aiosqlite.Connection:
        return self._connection


@pytest.mark.asyncio
async def test_successful_lifecycle_processing_consumes_pending_invalidations() -> None:
    """A resolved invalidation must not make the next scheduler pass run again.

    The events table keeps the audit trail, while the current state row is the
    source of truth for pending work.  If successful processing wrote the same
    invalidation back into state, unchanged items would be reprocessed forever.
    """
    connection = await aiosqlite.connect(":memory:")
    connection.row_factory = aiosqlite.Row
    try:
        with open("migrations/106_item_lifecycle_policy_ledger.sql", "r", encoding="utf-8") as handle:
            await connection.executescript(handle.read())
        await connection.commit()

        engine = CategoryLifecycleEngine(db=_LifecycleDb(connection))
        item = CategoryItem(key="example")
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()

        await engine.record_processing_result(
            item,
            purpose="suggestions",
            status="success",
            reason="library_changed",
            fingerprints=LifecycleFingerprints(),
            next_check_at=future,
            valid_until=future,
            invalidated_by=["library_changed"],
        )

        state = await engine.get_processing_state("base", "example")
        assert json.loads(state["invalidated_by"]) == []

        decision = await engine.should_process_item(item, purpose="suggestions")
        assert decision.should_process is False
        assert decision.reason == "ledger_valid"
    finally:
        await connection.close()
