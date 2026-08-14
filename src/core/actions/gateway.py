"""Unified mutation gateway with durable idempotency claims and truthful receipts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Coroutine

from loguru import logger

from src.core.actions.audit import ActionEventStore
from src.core.actions.request_hasher import ActionRequestHasher
from src.core.actions.result_normalizer import ActionResultNormalizer
from src.core.models import (
    ActionCommand,
    ActionResult,
    CommandClaimDisposition,
)


ActionHandler = Callable[..., Coroutine[Any, Any, Any]]

_PREFERENCE_ACTIONS: dict[str, tuple[str, Callable[[dict], str | None]]] = {
    "suggestion_deny": ("reject", lambda a: a.get("item_name") or a.get("item_id") or a.get("name")),
    "suggestion_approve": ("download", lambda a: a.get("item_name") or a.get("item_id") or a.get("name")),
    "category_item_pause": ("category_item_pause", lambda a: a.get("item_name") or a.get("item_id") or a.get("name")),
    "category_item_resume": ("category_item_resume", lambda a: a.get("item_name") or a.get("item_id") or a.get("name")),
    "settings_update_quality": ("quality_change", lambda a: None),
    "upgrade_deny": ("reject", lambda a: a.get("item_name") or a.get("item_id") or a.get("name")),
    "download_cancel": ("cancel", lambda a: a.get("item_name") or a.get("item_id") or a.get("name")),
}


class ActionGateway:
    """Single entry point for deterministic mutations from every application surface."""

    def __init__(
        self,
        audit_store: ActionEventStore | None = None,
        event_bus: Any = None,
        tool_registry: Any = None,
        behavior_recorder: Any = None,
        claim_wait_seconds: float = 30.0,
    ) -> None:
        self._registry = tool_registry
        self._legacy_handlers: dict[str, ActionHandler] = {}
        self._audit_store = audit_store
        self._event_bus = event_bus
        self._behavior_recorder = behavior_recorder
        self._claim_wait_seconds = max(0.0, claim_wait_seconds)
        self._hasher = ActionRequestHasher()
        self._normalizer = ActionResultNormalizer()

    def register(
        self,
        name: str,
        handler: ActionHandler,
        description: str = "",
        parameters: dict | None = None,
        intents: Any = None,
        requires_confirmation: bool = False,
        destructive: bool = False,
    ) -> None:
        """Register a direct handler or a shared ToolRegistry action."""
        if self._registry:
            self._registry.register(
                name=name,
                description=description or f"Action: {name}",
                parameters=parameters or {"type": "object", "properties": {}, "required": []},
                handler=handler,
                allow_direct=True,
                intents=intents,
                requires_confirmation=requires_confirmation,
                destructive=destructive,
            )
            logger.debug("Registered action as tool: {}", name)
            return
        if name in self._legacy_handlers:
            logger.warning("Action {!r} is being re-registered", name)
        self._legacy_handlers[name] = handler
        logger.debug("Registered action handler: {}", name)

    async def execute(self, command: ActionCommand) -> ActionResult:
        """Claim, execute, and durably record one mutation command."""
        request_fingerprint = self._hasher.fingerprint(command)
        claim_result = await self._prepare_claim(command, request_fingerprint)
        if claim_result is not None:
            return claim_result

        started_at = datetime.now(timezone.utc)
        await self._record_started(command, request_fingerprint)
        result = await self._execute_once(command)
        self._attach_identity(result, command, request_fingerprint, started_at)
        await self._persist_receipt(command, result, request_fingerprint)
        await self._record_legacy_audit(command, result)
        await self._record_behavior(command, result)
        self._emit_execution_event(command, result)
        return result

    async def _prepare_claim(
        self,
        command: ActionCommand,
        request_fingerprint: str,
    ) -> ActionResult | None:
        if not command.idempotency_key:
            return None
        if not self._audit_store:
            return self._claim_failure(
                command,
                request_fingerprint,
                "idempotency_unavailable",
                "This mutation requires a durable idempotency store, but none is configured.",
            )
        try:
            claim = await self._audit_store.claim_command(command, request_fingerprint)
        except Exception as exc:
            logger.error("Could not acquire durable command claim for {}: {}", command.name, exc)
            return self._claim_failure(
                command,
                request_fingerprint,
                "idempotency_unavailable",
                "The command could not be reserved safely; no mutation was executed.",
                persistence_error=str(exc),
            )

        if claim.disposition == CommandClaimDisposition.ACQUIRED:
            return None
        if claim.disposition == CommandClaimDisposition.REPLAY and claim.receipt is not None:
            return claim.receipt
        if claim.disposition == CommandClaimDisposition.IN_PROGRESS:
            receipt = await self._audit_store.wait_for_claim_receipt(
                command,
                request_fingerprint,
                timeout_seconds=self._claim_wait_seconds,
            )
            if receipt is not None:
                return receipt
            return self._claim_failure(
                command,
                request_fingerprint,
                "in_progress",
                claim.message or "An identical command is already executing.",
            )
        if claim.disposition == CommandClaimDisposition.CONFLICT:
            return self._claim_failure(
                command,
                request_fingerprint,
                "idempotency_conflict",
                claim.message or "The idempotency key is already bound to a different request.",
            )
        return self._claim_failure(
            command,
            request_fingerprint,
            "uncertain",
            claim.message or "A previous command may have executed without a readable receipt; automatic retry is blocked.",
        )

    async def _execute_once(self, command: ActionCommand) -> ActionResult:
        if self._registry:
            raw = await self._registry.execute(command.name, command.arguments)
            return self._normalizer.normalize(raw, command.name)
        handler = self._legacy_handlers.get(command.name)
        if not handler:
            message = f"Unknown action: {command.name!r}"
            logger.warning(message)
            return ActionResult(ok=False, error=message, action_name=command.name, status="rejected")
        try:
            raw = await handler(**command.arguments)
        except Exception as exc:
            logger.error("Action {!r} failed: {}", command.name, exc)
            return ActionResult(ok=False, error=str(exc), action_name=command.name, status="failed")
        return self._normalizer.normalize(raw, command.name)

    def _attach_identity(
        self,
        result: ActionResult,
        command: ActionCommand,
        request_fingerprint: str,
        started_at: datetime,
    ) -> None:
        result.command_id = command.command_id
        result.correlation_id = command.correlation_id
        result.idempotency_key = command.idempotency_key
        result.request_fingerprint = request_fingerprint
        result.started_at = started_at
        result.completed_at = datetime.now(timezone.utc)

    async def _record_started(self, command: ActionCommand, request_fingerprint: str) -> None:
        if not self._audit_store:
            return
        try:
            await self._audit_store.record_started(command, request_fingerprint)
        except Exception as exc:
            logger.warning("Failed to record command start: {}", exc)

    async def _persist_receipt(
        self,
        command: ActionCommand,
        result: ActionResult,
        request_fingerprint: str,
    ) -> None:
        if not self._audit_store:
            result.receipt_persisted = None
            return
        try:
            await self._audit_store.record_receipt(command, result, request_fingerprint)
        except Exception as exc:
            logger.error("Mutation outcome could not be durably recorded for {}: {}", command.name, exc)
            may_have_executed = result.ok
            result.receipt_persisted = False
            result.persistence_error = str(exc)
            result.ok = False
            result.status = "uncertain"
            result.error = (
                "The mutation may have executed, but its durable receipt could not be recorded. "
                "Verify current state before retrying."
                if may_have_executed
                else (result.error or "The failed mutation outcome could not be durably recorded.")
            )
            await self._audit_store.mark_claim_uncertain(command, request_fingerprint, str(exc))

    async def _record_legacy_audit(self, command: ActionCommand, result: ActionResult) -> None:
        if not self._audit_store:
            return
        try:
            await self._audit_store.record(
                action_name=command.name,
                source=command.source,
                user_id=command.user_id,
                session_id=command.session_id,
                arguments=command.arguments,
                result={
                    "ok": result.ok,
                    "status": result.status,
                    "error": result.error,
                    "data": result.data,
                    "command_id": result.command_id,
                    "correlation_id": result.correlation_id,
                    "receipt_persisted": result.receipt_persisted,
                },
            )
        except Exception as exc:
            logger.warning("Failed to record compatibility action audit: {}", exc)

    async def _record_behavior(self, command: ActionCommand, result: ActionResult) -> None:
        if not self._behavior_recorder or not result.ok or result.replayed:
            return
        mapping = _PREFERENCE_ACTIONS.get(command.name)
        if not mapping:
            return
        behavior_action, extract_item = mapping
        try:
            await self._behavior_recorder.record_action(
                user_id=command.user_id or "system",
                action=behavior_action,
                item_name=extract_item(command.arguments),
                action_name=command.name,
            )
        except Exception as exc:
            logger.warning("Failed to record behavior for {!r}: {}", command.name, exc)

    def _emit_execution_event(self, command: ActionCommand, result: ActionResult) -> None:
        if not self._event_bus:
            return
        self._event_bus.emit("action_executed", {
            "action": command.name,
            "source": command.source.value,
            "ok": result.ok,
            "user_id": command.user_id,
            "command_id": command.command_id,
            "correlation_id": command.correlation_id,
            "replayed": result.replayed,
            "receipt_persisted": result.receipt_persisted,
        })

    @staticmethod
    def _claim_failure(
        command: ActionCommand,
        request_fingerprint: str,
        status: str,
        error: str,
        persistence_error: str | None = None,
    ) -> ActionResult:
        now = datetime.now(timezone.utc)
        return ActionResult(
            ok=False,
            error=error,
            action_name=command.name,
            status=status,
            command_id=command.command_id,
            correlation_id=command.correlation_id,
            idempotency_key=command.idempotency_key,
            request_fingerprint=request_fingerprint,
            receipt_persisted=False if persistence_error else None,
            persistence_error=persistence_error,
            started_at=now,
            completed_at=now,
        )

    @property
    def registered_actions(self) -> list[str]:
        """Return the currently registered mutation action names."""
        if self._registry:
            return self._registry.get_tool_names()
        return list(self._legacy_handlers.keys())
