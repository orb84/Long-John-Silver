"""Durable command claims, immutable receipts, and append-only operational events."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import aiosqlite
from loguru import logger

from src.core.actions.payload_sanitizer import OperationalPayloadSanitizer
from src.core.models import (
    ActionCommand,
    ActionResult,
    ActionSource,
    CommandClaimDisposition,
    CommandClaimOutcome,
)


class ActionEventStore:
    """Persist command claims, immutable receipts, and compatibility audit rows."""

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db
        self._claim_lock = asyncio.Lock()
        self._sanitizer = OperationalPayloadSanitizer()

    async def claim_command(
        self,
        command: ActionCommand,
        request_fingerprint: str,
        lease_seconds: int = 300,
    ) -> CommandClaimOutcome:
        """Atomically reserve an idempotent command before any mutation executes."""
        if not command.idempotency_key:
            return CommandClaimOutcome(
                disposition=CommandClaimDisposition.ACQUIRED,
                owner_command_id=command.command_id,
                correlation_id=command.correlation_id,
                request_fingerprint=request_fingerprint,
            )
        now = datetime.now(timezone.utc)
        lease_expires = now + timedelta(seconds=max(30, lease_seconds))
        scope = self._claim_scope(command)
        async with self._claim_lock:
            await self._db.execute("BEGIN IMMEDIATE")
            try:
                row = await self._select_claim(scope)
                if row is None:
                    await self._db.execute(
                        """INSERT INTO action_command_claims
                           (action_name, source, user_scope, session_scope, idempotency_key,
                            request_fingerprint, owner_command_id, correlation_id, state,
                            lease_expires_at, receipt_command_id, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, NULL, ?, ?)""",
                        (*scope, request_fingerprint, command.command_id, command.correlation_id,
                         lease_expires.isoformat(), now.isoformat(), now.isoformat()),
                    )
                    await self._insert_operational_event(
                        command,
                        event_type="command_claimed",
                        subsystem="action_gateway",
                        details={"lease_expires_at": lease_expires.isoformat()},
                        request_fingerprint=request_fingerprint,
                    )
                    await self._db.commit()
                    return CommandClaimOutcome(
                        disposition=CommandClaimDisposition.ACQUIRED,
                        owner_command_id=command.command_id,
                        correlation_id=command.correlation_id,
                        request_fingerprint=request_fingerprint,
                    )

                outcome = await self._outcome_for_existing_claim(
                    command, request_fingerprint, dict(row), now,
                )
                await self._db.commit()
                return outcome
            except Exception:
                await self._db.rollback()
                raise

    async def _outcome_for_existing_claim(
        self,
        command: ActionCommand,
        request_fingerprint: str,
        claim: dict[str, Any],
        now: datetime,
    ) -> CommandClaimOutcome:
        owner_command_id = str(claim["owner_command_id"])
        correlation_id = str(claim["correlation_id"])
        stored_fingerprint = str(claim["request_fingerprint"])
        if stored_fingerprint != request_fingerprint:
            await self._insert_operational_event(
                command,
                event_type="command_idempotency_conflict",
                subsystem="action_gateway",
                details={"owner_command_id": owner_command_id},
                error_class="idempotency_conflict",
                request_fingerprint=request_fingerprint,
            )
            return CommandClaimOutcome(
                disposition=CommandClaimDisposition.CONFLICT,
                owner_command_id=owner_command_id,
                correlation_id=correlation_id,
                request_fingerprint=stored_fingerprint,
                message="The idempotency key is already bound to different command arguments.",
            )

        receipt_id = claim.get("receipt_command_id")
        if receipt_id:
            receipt = await self._load_receipt_unlocked(str(receipt_id))
            if receipt is not None:
                await self._insert_operational_event(
                    command,
                    event_type="command_replayed",
                    subsystem="action_gateway",
                    details={"original_command_id": receipt.command_id},
                    request_fingerprint=request_fingerprint,
                )
                receipt.replayed = True
                return CommandClaimOutcome(
                    disposition=CommandClaimDisposition.REPLAY,
                    owner_command_id=owner_command_id,
                    correlation_id=correlation_id,
                    request_fingerprint=stored_fingerprint,
                    receipt=receipt,
                )

        state = str(claim.get("state") or "running")
        if state in {"completed", "failed", "uncertain"}:
            return CommandClaimOutcome(
                disposition=CommandClaimDisposition.UNCERTAIN,
                owner_command_id=owner_command_id,
                correlation_id=correlation_id,
                request_fingerprint=stored_fingerprint,
                message="The command has a terminal claim but no readable durable receipt.",
            )

        lease_expires = self._parse_datetime(claim.get("lease_expires_at"))
        if lease_expires is None or lease_expires <= now:
            await self._db.execute(
                """UPDATE action_command_claims
                   SET state = 'uncertain', updated_at = ?
                   WHERE action_name = ? AND source = ? AND user_scope = ?
                     AND session_scope = ? AND idempotency_key = ?""",
                (now.isoformat(), *self._claim_scope(command)),
            )
            return CommandClaimOutcome(
                disposition=CommandClaimDisposition.UNCERTAIN,
                owner_command_id=owner_command_id,
                correlation_id=correlation_id,
                request_fingerprint=stored_fingerprint,
                message="The previous command lease expired without a durable receipt; automatic retry is blocked.",
            )
        return CommandClaimOutcome(
            disposition=CommandClaimDisposition.IN_PROGRESS,
            owner_command_id=owner_command_id,
            correlation_id=correlation_id,
            request_fingerprint=stored_fingerprint,
            message="An identical command is already executing.",
        )

    async def wait_for_claim_receipt(
        self,
        command: ActionCommand,
        request_fingerprint: str,
        timeout_seconds: float = 30.0,
        poll_seconds: float = 0.05,
    ) -> ActionResult | None:
        """Wait briefly for the owner of an in-progress claim to persist its receipt."""
        deadline = asyncio.get_running_loop().time() + max(0.0, timeout_seconds)
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(max(0.01, poll_seconds))
            async with self._claim_lock:
                row = await self._select_claim(self._claim_scope(command))
                if row is None:
                    return None
                claim = dict(row)
                if str(claim.get("request_fingerprint")) != request_fingerprint:
                    return None
                receipt_id = claim.get("receipt_command_id")
                if receipt_id:
                    receipt = await self._load_receipt_unlocked(str(receipt_id))
                    if receipt is not None:
                        receipt.replayed = True
                        return receipt
                if str(claim.get("state")) in {"failed", "completed", "uncertain"}:
                    return None
        return None

    async def record_started(
        self,
        command: ActionCommand,
        request_fingerprint: str = "",
        subsystem: str = "action_gateway",
    ) -> None:
        """Append a command-start event after any idempotency claim is acquired."""
        await self._record_operational_event(
            command=command,
            event_type="command_started",
            subsystem=subsystem,
            details={"arguments": command.arguments},
            request_fingerprint=request_fingerprint,
        )

    async def record_receipt(
        self,
        command: ActionCommand,
        result: ActionResult,
        request_fingerprint: str = "",
        subsystem: str = "action_gateway",
    ) -> ActionResult:
        """Persist an immutable final receipt and finalize its claim atomically."""
        started = result.started_at or command.created_at
        completed = result.completed_at or datetime.now(timezone.utc)
        arguments = self._sanitizer.sanitize(command.arguments)
        data = self._sanitizer.sanitize(result.data)
        error = self._sanitizer.sanitize_text(result.error)
        async with self._claim_lock:
            await self._db.execute("BEGIN IMMEDIATE")
            try:
                await self._db.execute(
                    """INSERT INTO action_command_receipts
                       (command_id, correlation_id, idempotency_key, action_name, source,
                        actor, user_id, session_id, status, ok, arguments_json, data_json,
                        error, started_at, completed_at, request_fingerprint,
                        receipt_persisted, persistence_error)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, NULL)""",
                    (
                        command.command_id, command.correlation_id, command.idempotency_key,
                        command.name, command.source.value, command.actor, command.user_id,
                        command.session_id, result.status, 1 if result.ok else 0,
                        json.dumps(arguments, default=str), json.dumps(data, default=str), error,
                        self._isoformat(started), self._isoformat(completed), request_fingerprint,
                    ),
                )
                if command.idempotency_key:
                    cursor = await self._db.execute(
                        """UPDATE action_command_claims
                           SET state = ?, receipt_command_id = ?, updated_at = ?
                           WHERE action_name = ? AND source = ? AND user_scope = ?
                             AND session_scope = ? AND idempotency_key = ?
                             AND owner_command_id = ? AND request_fingerprint = ?""",
                        (
                            "completed" if result.ok else "failed",
                            command.command_id,
                            datetime.now(timezone.utc).isoformat(),
                            *self._claim_scope(command),
                            command.command_id,
                            request_fingerprint,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise RuntimeError("Command claim ownership changed before receipt finalization")
                await self._insert_operational_event(
                    command=command,
                    event_type="command_completed" if result.ok else "command_failed",
                    subsystem=subsystem,
                    new_state={"status": result.status, "ok": result.ok},
                    details={"data": data},
                    error_class="action_error" if result.error else None,
                    request_fingerprint=request_fingerprint,
                )
                await self._db.commit()
                result.receipt_persisted = True
                result.persistence_error = None
                return result
            except Exception:
                await self._db.rollback()
                raise

    async def mark_claim_uncertain(
        self,
        command: ActionCommand,
        request_fingerprint: str,
        reason: str,
    ) -> None:
        """Fail closed when mutation outcome could not be durably recorded."""
        if not command.idempotency_key:
            return
        try:
            async with self._claim_lock:
                await self._db.execute(
                    """UPDATE action_command_claims
                       SET state = 'uncertain', updated_at = ?
                       WHERE action_name = ? AND source = ? AND user_scope = ?
                         AND session_scope = ? AND idempotency_key = ?
                         AND owner_command_id = ? AND request_fingerprint = ?""",
                    (
                        datetime.now(timezone.utc).isoformat(),
                        *self._claim_scope(command), command.command_id, request_fingerprint,
                    ),
                )
                await self._db.commit()
        except Exception as exc:
            logger.error("Could not mark failed receipt persistence as uncertain: {}", exc)

    async def find_successful_receipt(
        self,
        action_name: str,
        idempotency_key: str,
        max_age_seconds: int = 300,
    ) -> ActionResult | None:
        """Backward-compatible receipt lookup; new callers should use command claims."""
        cursor = await self._db.execute(
            """SELECT * FROM action_command_receipts
               WHERE action_name = ? AND idempotency_key = ? AND ok = 1
               ORDER BY completed_at DESC LIMIT 1""",
            (action_name, idempotency_key),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        receipt = self._receipt_from_row(dict(row))
        completed = receipt.completed_at
        if completed is None:
            return None
        age = (datetime.now(timezone.utc) - completed.astimezone(timezone.utc)).total_seconds()
        if age > max_age_seconds:
            return None
        receipt.replayed = True
        return receipt

    async def record(
        self,
        action_name: str,
        source: ActionSource,
        user_id: str | None = None,
        session_id: str | None = None,
        arguments: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        """Persist the legacy action-event projection independently of the ledger."""
        try:
            safe_arguments = self._sanitizer.sanitize(arguments or {})
            safe_result = self._sanitizer.sanitize(result or {})
            await self._db.execute(
                """INSERT INTO action_events
                   (action_name, source, user_id, session_id, arguments_json, result_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    action_name, source.value, user_id, session_id,
                    json.dumps(safe_arguments, default=str),
                    json.dumps(safe_result, default=str),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            await self._db.commit()
        except Exception as exc:
            logger.warning("Failed to persist legacy action event: {}", exc)

    async def get_recent(
        self,
        limit: int = 50,
        source: ActionSource | None = None,
        action_name: str | None = None,
    ) -> list[dict]:
        """Return recent legacy action-audit rows in newest-first order."""
        query = "SELECT * FROM action_events"
        conditions: list[str] = []
        params: list[Any] = []
        if source is not None:
            conditions.append("source = ?")
            params.append(source.value)
        if action_name is not None:
            conditions.append("action_name = ?")
            params.append(action_name)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        cursor = await self._db.execute(query, params)
        return [dict(row) for row in await cursor.fetchall()]

    async def get_recent_by_user(self, user_id: str, limit: int = 20) -> list[dict]:
        """Return recent legacy action-audit rows for one user."""
        cursor = await self._db.execute(
            "SELECT * FROM action_events WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def get_command_timeline(self, correlation_id: str) -> list[dict]:
        """Return the append-only operational timeline for one command."""
        cursor = await self._db.execute(
            "SELECT * FROM operational_events WHERE correlation_id = ? ORDER BY created_at, event_id",
            (correlation_id,),
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def count(self) -> int:
        """Return the number of stored legacy action-audit rows."""
        cursor = await self._db.execute("SELECT COUNT(*) FROM action_events")
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def _record_operational_event(
        self,
        command: ActionCommand,
        event_type: str,
        subsystem: str,
        previous_state: dict[str, Any] | None = None,
        new_state: dict[str, Any] | None = None,
        details: dict[str, Any] | None = None,
        error_class: str | None = None,
        request_fingerprint: str = "",
    ) -> None:
        async with self._claim_lock:
            await self._db.execute("BEGIN IMMEDIATE")
            try:
                await self._insert_operational_event(
                    command, event_type, subsystem, previous_state, new_state,
                    details, error_class, request_fingerprint,
                )
                await self._db.commit()
            except Exception:
                await self._db.rollback()
                raise

    async def _insert_operational_event(
        self,
        command: ActionCommand,
        event_type: str,
        subsystem: str,
        previous_state: dict[str, Any] | None = None,
        new_state: dict[str, Any] | None = None,
        details: dict[str, Any] | None = None,
        error_class: str | None = None,
        request_fingerprint: str = "",
    ) -> None:
        await self._db.execute(
            """INSERT INTO operational_events
               (event_id, command_id, correlation_id, action_name, event_type,
                source, actor, subsystem, entity_type, entity_id,
                previous_state_json, new_state_json, details_json, error_class,
                created_at, user_id, session_id, idempotency_key, request_fingerprint)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', '', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid4()), command.command_id, command.correlation_id, command.name,
                event_type, command.source.value, command.actor, subsystem,
                json.dumps(self._sanitizer.sanitize(previous_state or {}), default=str),
                json.dumps(self._sanitizer.sanitize(new_state or {}), default=str),
                json.dumps(self._sanitizer.sanitize(details or {}), default=str),
                error_class, datetime.now(timezone.utc).isoformat(), command.user_id,
                command.session_id, command.idempotency_key, request_fingerprint,
            ),
        )

    async def _select_claim(self, scope: tuple[str, str, str, str, str]) -> aiosqlite.Row | None:
        cursor = await self._db.execute(
            """SELECT * FROM action_command_claims
               WHERE action_name = ? AND source = ? AND user_scope = ?
                 AND session_scope = ? AND idempotency_key = ?""",
            scope,
        )
        return await cursor.fetchone()

    async def _load_receipt_unlocked(self, command_id: str) -> ActionResult | None:
        cursor = await self._db.execute(
            "SELECT * FROM action_command_receipts WHERE command_id = ?",
            (command_id,),
        )
        row = await cursor.fetchone()
        return self._receipt_from_row(dict(row)) if row else None

    def _receipt_from_row(self, values: dict[str, Any]) -> ActionResult:
        return ActionResult(
            ok=bool(values["ok"]),
            data=json.loads(values.get("data_json") or "{}"),
            error=values.get("error"),
            action_name=values.get("action_name") or "",
            status=values.get("status") or ("succeeded" if values.get("ok") else "failed"),
            command_id=values.get("command_id") or "",
            correlation_id=values.get("correlation_id") or "",
            idempotency_key=values.get("idempotency_key"),
            request_fingerprint=values.get("request_fingerprint") or "",
            receipt_persisted=bool(values.get("receipt_persisted", 1)),
            persistence_error=values.get("persistence_error"),
            started_at=self._parse_datetime(values.get("started_at")),
            completed_at=self._parse_datetime(values.get("completed_at")),
        )

    @staticmethod
    def _claim_scope(command: ActionCommand) -> tuple[str, str, str, str, str]:
        return (
            command.name,
            command.source.value,
            command.user_id or "",
            command.session_id or "",
            command.idempotency_key or "",
        )

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            parsed = value
        else:
            try:
                parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    @staticmethod
    def _isoformat(value: Any) -> str:
        return value.isoformat() if hasattr(value, "isoformat") else str(value)
