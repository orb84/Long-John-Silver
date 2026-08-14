"""Adversarial regression coverage for the corrected command/receipt foundation."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from src.ai.tool_outcome_guard import ToolOutcomeLedger
from src.ai.tools.downloads import QueueDownloadTool
from src.core.actions.audit import ActionEventStore
from src.core.actions.gateway import ActionGateway
from src.core.models import ActionCommand, ActionResult, ActionSource, ToolExecutionContext
from src.web.routers.actions import ActionsRouter


async def _ledger_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    for migration_name in (
        "112_operational_command_ledger.sql",
        "113_command_claims_and_receipt_integrity.sql",
    ):
        await db.executescript(Path("migrations", migration_name).read_text())
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
    return db


@pytest.mark.asyncio
async def test_concurrent_duplicate_commands_execute_handler_once() -> None:
    db = await _ledger_db()
    calls = 0
    entered = asyncio.Event()

    async def mutate(value: int) -> dict:
        nonlocal calls
        calls += 1
        entered.set()
        await asyncio.sleep(0.1)
        return {"status": "queued", "value": value}

    gateway = ActionGateway(ActionEventStore(db), claim_wait_seconds=2)
    gateway.register("queue_download", mutate)
    first = ActionCommand(
        name="queue_download", arguments={"value": 1}, source=ActionSource.CHAT,
        user_id="u", session_id="s", idempotency_key="same-operation",
    )
    second = first.model_copy(update={"command_id": "second", "correlation_id": "second-correlation"})
    task_one = asyncio.create_task(gateway.execute(first))
    await entered.wait()
    task_two = asyncio.create_task(gateway.execute(second))
    one, two = await asyncio.gather(task_one, task_two)

    assert calls == 1
    assert one.ok and two.ok
    assert {one.replayed, two.replayed} == {False, True}
    assert one.command_id == two.command_id
    await db.close()


@pytest.mark.asyncio
async def test_idempotency_scope_isolated_by_user_and_session() -> None:
    db = await _ledger_db()
    calls = 0

    async def mutate(value: int) -> dict:
        nonlocal calls
        calls += 1
        return {"status": "queued", "value": value}

    gateway = ActionGateway(ActionEventStore(db))
    gateway.register("queue_download", mutate)
    base = dict(
        name="queue_download", arguments={"value": 1}, source=ActionSource.CHAT,
        idempotency_key="same-client-key",
    )
    first = await gateway.execute(ActionCommand(**base, user_id="alice", session_id="one"))
    second = await gateway.execute(ActionCommand(**base, user_id="bob", session_id="one"))
    third = await gateway.execute(ActionCommand(**base, user_id="alice", session_id="two"))

    assert first.ok and second.ok and third.ok
    assert calls == 3
    await db.close()


@pytest.mark.asyncio
async def test_same_key_with_different_arguments_is_conflict() -> None:
    db = await _ledger_db()
    calls = 0

    async def mutate(value: int) -> dict:
        nonlocal calls
        calls += 1
        return {"status": "queued", "value": value}

    gateway = ActionGateway(ActionEventStore(db))
    gateway.register("queue_download", mutate)
    common = dict(
        name="queue_download", source=ActionSource.CHAT, user_id="u",
        session_id="s", idempotency_key="bound-key",
    )
    first = await gateway.execute(ActionCommand(**common, arguments={"value": 1}))
    conflict = await gateway.execute(ActionCommand(**common, arguments={"value": 2}))

    assert first.ok
    assert not conflict.ok
    assert conflict.status == "idempotency_conflict"
    assert calls == 1
    await db.close()


@pytest.mark.asyncio
async def test_failed_receipt_is_replayed_without_reexecution() -> None:
    db = await _ledger_db()
    calls = 0

    async def mutate() -> ActionResult:
        nonlocal calls
        calls += 1
        return ActionResult(ok=False, status="succeeded", error="queue refused")

    gateway = ActionGateway(ActionEventStore(db))
    gateway.register("queue_download", mutate)
    command = ActionCommand(
        name="queue_download", source=ActionSource.CHAT,
        user_id="u", session_id="s", idempotency_key="failed-once",
    )
    first = await gateway.execute(command)
    replay = await gateway.execute(command.model_copy(update={"command_id": "retry"}))

    assert not first.ok and first.status == "failed"
    assert not replay.ok and replay.replayed
    assert calls == 1
    await db.close()


@pytest.mark.asyncio
async def test_expired_running_claim_fails_closed() -> None:
    db = await _ledger_db()
    calls = 0

    async def mutate() -> dict:
        nonlocal calls
        calls += 1
        return {"status": "queued"}

    store = ActionEventStore(db)
    gateway = ActionGateway(store, claim_wait_seconds=0)
    gateway.register("queue_download", mutate)
    command = ActionCommand(
        name="queue_download", source=ActionSource.CHAT,
        user_id="u", session_id="s", idempotency_key="expired-running",
    )
    fingerprint = gateway._hasher.fingerprint(command)
    claim = await store.claim_command(command, fingerprint)
    assert claim.disposition.value == "acquired"
    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    await db.execute("UPDATE action_command_claims SET lease_expires_at = ?", (expired,))
    await db.commit()

    result = await gateway.execute(command.model_copy(update={"command_id": "retry"}))
    assert not result.ok and result.status == "uncertain"
    assert calls == 0
    await db.close()


@pytest.mark.asyncio
async def test_missing_claim_schema_blocks_idempotent_mutation() -> None:
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.execute("""CREATE TABLE action_events (
        id INTEGER PRIMARY KEY, action_name TEXT, source TEXT, user_id TEXT,
        session_id TEXT, arguments_json TEXT, result_json TEXT, created_at TEXT
    )""")
    calls = 0

    async def mutate() -> dict:
        nonlocal calls
        calls += 1
        return {"status": "queued"}

    gateway = ActionGateway(ActionEventStore(db))
    gateway.register("queue_download", mutate)
    result = await gateway.execute(ActionCommand(
        name="queue_download", source=ActionSource.CHAT, idempotency_key="must-be-durable",
    ))
    assert not result.ok and result.status == "idempotency_unavailable"
    assert calls == 0
    await db.close()


@pytest.mark.asyncio
async def test_receipt_persistence_failure_marks_outcome_uncertain() -> None:
    db = await _ledger_db()
    store = ActionEventStore(db)
    gateway = ActionGateway(store)
    calls = 0

    async def mutate() -> dict:
        nonlocal calls
        calls += 1
        return {"status": "queued", "download_id": "d1"}

    gateway.register("queue_download", mutate)
    original = store.record_receipt

    async def fail_receipt(*args, **kwargs):
        raise RuntimeError("disk full")

    store.record_receipt = fail_receipt  # type: ignore[method-assign]
    command = ActionCommand(
        name="queue_download", source=ActionSource.CHAT,
        user_id="u", session_id="s", idempotency_key="unrecorded",
    )
    result = await gateway.execute(command)
    store.record_receipt = original  # type: ignore[method-assign]
    retry = await gateway.execute(command.model_copy(update={"command_id": "retry"}))

    assert not result.ok and result.status == "uncertain"
    assert "may have executed" in str(result.error)
    assert result.receipt_persisted is False
    assert not retry.ok and retry.status == "uncertain"
    assert calls == 1
    await db.close()


@pytest.mark.asyncio
async def test_operational_payloads_redact_trackers_and_credentials() -> None:
    db = await _ledger_db()
    gateway = ActionGateway(ActionEventStore(db))
    gateway.register("queue_download", AsyncMock(return_value={"status": "queued", "url": "https://example.test/file?token=abc"}))
    magnet = "magnet:?xt=urn:btih:ABC123&tr=https://tracker.test/announce?passkey=SECRET"
    result = await gateway.execute(ActionCommand(
        name="queue_download",
        arguments={"magnet": magnet, "api_key": "TOPSECRET"},
        source=ActionSource.CHAT,
        user_id="u", session_id="s", idempotency_key="redaction",
    ))
    assert result.ok
    receipt = dict(await (await db.execute("SELECT * FROM action_command_receipts")).fetchone())
    events = [dict(row) for row in await (await db.execute("SELECT * FROM operational_events")).fetchall()]
    legacy = dict(await (await db.execute("SELECT * FROM action_events")).fetchone())
    durable_text = json.dumps({"receipt": receipt, "events": events, "legacy": legacy})
    assert "TOPSECRET" not in durable_text
    assert "SECRET" not in durable_text
    assert "tracker.test" not in durable_text
    assert "ABC123" in durable_text
    await db.close()


def test_queue_key_covers_operation_and_all_arguments() -> None:
    context = ToolExecutionContext(
        user_id="u", session_id="s", source="chat", operation_id="tool-call-1",
    )
    base = {"candidate_id": "c1", "priority": "normal", "episode": 10}
    same = QueueDownloadTool._queue_idempotency_key(dict(base), context)
    changed_priority = QueueDownloadTool._queue_idempotency_key({**base, "priority": "high"}, context)
    changed_episode = QueueDownloadTool._queue_idempotency_key({**base, "episode": 9}, context)
    new_invocation = QueueDownloadTool._queue_idempotency_key(
        dict(base), context.model_copy(update={"operation_id": "tool-call-2"}),
    )
    assert len({same, changed_priority, changed_episode, new_invocation}) == 4


@pytest.mark.asyncio
async def test_actions_router_owns_provenance_and_durable_ids() -> None:
    gateway = SimpleNamespace(
        execute=AsyncMock(return_value=ActionResult(ok=True, data={"done": True})),
        registered_actions=[],
    )
    router = ActionsRouter(SimpleNamespace(action_gateway=gateway))

    class FakeRequest:
        async def json(self):
            return {
                "name": "pause_download",
                "arguments": {"download_id": "d1"},
                "source": "system",
                "actor": "scheduler",
                "user_id": "admin",
                "session_id": "forged",
                "command_id": "forged-command",
                "correlation_id": "forged-correlation",
                "idempotency_key": "forged-key",
            }

    response = await router._execute_action(FakeRequest(), True)
    assert response.status_code == 200
    command = gateway.execute.await_args.args[0]
    assert command.source == ActionSource.UI
    assert command.actor == "authenticated_web_user"
    assert command.user_id == "web"
    assert command.session_id == "web_actions"
    assert command.command_id != "forged-command"
    assert command.correlation_id != "forged-correlation"
    assert command.idempotency_key is None


def test_queue_outcome_guard_rejects_unrecorded_success() -> None:
    """A successful mutation without a durable receipt must not authorize success prose."""
    ledger = ToolOutcomeLedger()
    ledger.record("queue_download", {
        "content": json.dumps({
            "status": "queued",
            "queued_count": 1,
            "download_id": "d1",
            "command_receipt": {
                "ok": True,
                "status": "succeeded_unrecorded",
                "receipt_persisted": False,
            },
        }),
    })

    failure = ledger.unresolved_queue_failure()
    assert failure is not None
    assert "durable command receipt" in failure
    assert ledger.partial_queue_failure() is None


def test_queue_outcome_guard_accepts_durable_verified_success() -> None:
    """A persisted command receipt plus a queue identifier authorizes success prose."""
    ledger = ToolOutcomeLedger()
    ledger.record("queue_download", {
        "content": json.dumps({
            "status": "queued",
            "queued_count": 1,
            "download_id": "d1",
            "command_receipt": {
                "ok": True,
                "status": "queued",
                "receipt_persisted": True,
            },
        }),
    })

    assert ledger.unresolved_queue_failure() is None
