"""Round 283 deterministic checks for command receipts and authority foundations."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import tempfile

import aiosqlite

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.actions.audit import ActionEventStore
from src.core.actions.gateway import ActionGateway
from src.core.database import Database
from src.core.models import ActionCommand, ActionSource
from src.core.state_authority import FactType, StateAuthorityRegistry


class Round283Checks:
    """Exercise durable receipts, idempotency, and authority boundaries."""

    @staticmethod
    async def run() -> None:
        await Round283Checks._gateway_receipt_is_durable_and_idempotent()
        Round283Checks._authority_matrix_rejects_wrong_sources()
        await Round283Checks._migration_is_applied_by_database()
        print("ROUND283_COMMAND_LEDGER_AND_RECONCILIATION_PASS")

    @staticmethod
    async def _gateway_receipt_is_durable_and_idempotent() -> None:
        db = await aiosqlite.connect(":memory:")
        db.row_factory = aiosqlite.Row
        for migration_name in (
            "112_operational_command_ledger.sql",
            "113_command_claims_and_receipt_integrity.sql",
        ):
            await db.executescript((ROOT / "migrations" / migration_name).read_text())
        await db.executescript("""
            CREATE TABLE action_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action_name TEXT NOT NULL,
                source TEXT NOT NULL,
                user_id TEXT,
                session_id TEXT,
                arguments_json TEXT NOT NULL DEFAULT '{}',
                result_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
        """)
        calls = {"count": 0}

        async def mutate(value: int) -> dict:
            calls["count"] += 1
            return {"status": "queued", "download_id": f"dl-{value}"}

        store = ActionEventStore(db)
        gateway = ActionGateway(audit_store=store)
        gateway.register("queue_download", mutate)
        command = ActionCommand(
            name="queue_download",
            arguments={"value": 7},
            source=ActionSource.CHAT,
            session_id="session-1",
            actor="user",
            idempotency_key="queue:candidate-7",
        )
        first = await gateway.execute(command)
        replay = await gateway.execute(command.model_copy(update={"command_id": "second-command"}))
        assert first.ok and first.status == "queued"
        assert replay.ok and replay.replayed
        assert replay.command_id == first.command_id
        assert calls["count"] == 1
        timeline = await store.get_command_timeline(first.correlation_id)
        assert [event["event_type"] for event in timeline] == [
            "command_claimed", "command_started", "command_completed", "command_replayed",
        ]
        await db.close()

    @staticmethod
    def _authority_matrix_rejects_wrong_sources() -> None:
        registry = StateAuthorityRegistry()
        assert registry.accepts(FactType.LOCAL_PRESENCE, "canonical_library_object")
        assert not registry.accepts(FactType.LOCAL_PRESENCE, "download_history")
        assert registry.accepts(FactType.ACTION_OUTCOME, "command_receipt")
        assert not registry.accepts(FactType.DOWNLOAD_ACTIVITY, "conversation_memory")

    @staticmethod
    async def _migration_is_applied_by_database() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Database(str(Path(tmp) / "round283.db"))
            await database.initialize()
            connection = database.raw_connection
            assert connection is not None
            cursor = await connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('action_command_receipts', 'operational_events', 'action_command_claims')"
            )
            names = {row[0] for row in await cursor.fetchall()}
            assert names == {"action_command_receipts", "operational_events", "action_command_claims"}
            await database.close()


if __name__ == "__main__":
    asyncio.run(Round283Checks.run())
