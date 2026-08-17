"""Protocol-neutral delegation of external requests to the canonical LJS agent."""

from __future__ import annotations

import asyncio
import uuid

from src.ai.chat_session_runner import ChatSessionRunner, ChatTurnOutcome, ChatTurnRequest
from src.ai.chat_turn_registry import ActiveChatTurn, ChatTurnRegistry
from src.ai.agent_delegation_admission import AgentDelegationAdmissionGate
from src.ai.agent_turn_control import AgentTurnControlService
from src.core.conversation_handle import ConversationHandleService, ResolvedConversationHandle
from src.core.models import (
    AgentDelegationResult,
    AgentDelegationStatus,
    InvocationCapability,
    InvocationContext,
    InvocationPrincipal,
)


class AgentDelegationDenied(PermissionError):
    """Raised when a principal may not delegate work to the LJS agent."""


class AgentDelegationService:
    """Delegate external messages through LJS's existing chat authority."""

    def __init__(
        self,
        runner: ChatSessionRunner,
        turn_registry: ChatTurnRegistry,
        conversation_handles: ConversationHandleService,
        admission_gate: AgentDelegationAdmissionGate | None = None,
    ) -> None:
        self._runner = runner
        self._turn_registry = turn_registry
        self._conversation_handles = conversation_handles
        self._admission = admission_gate or AgentDelegationAdmissionGate()
        self._turn_control = AgentTurnControlService(turn_registry, conversation_handles)

    async def send_message(
        self,
        *,
        principal: InvocationPrincipal,
        message: str,
        conversation_id: str | None = None,
        allow_actions: bool = False,
    ) -> AgentDelegationResult:
        """Run one LJS-agent turn using an opaque principal-bound conversation."""
        self._require_delegation(principal)
        normalized_message = str(message or "")
        if not normalized_message.strip():
            raise ValueError("Delegated agent message cannot be empty")
        if len(normalized_message) > 65536:
            raise ValueError("Delegated agent message exceeds the 65536-character limit")
        if not await self._admission.try_acquire(principal):
            raise AgentDelegationDenied("This principal has reached the concurrent delegated-turn limit")
        try:
            resolved = await self._conversation_handles.resolve_or_mint(conversation_id, principal)
            request = self._request(resolved, principal, normalized_message, allow_actions)
            started, active = await self._turn_registry.start(
                resolved.internal_session_id,
                str(request.turn_id or ""),
                lambda: self._runner.collect_outcome(request),
                task_name=f"external-agent-turn-{request.turn_id}",
            )
            if not started:
                return self._busy_result(resolved, active)
            return await self._await_turn(resolved, active)
        finally:
            await self._admission.release(principal)

    async def cancel_turn(
        self,
        *,
        principal: InvocationPrincipal,
        conversation_id: str,
        turn_id: str | None = None,
    ) -> AgentDelegationResult:
        """Cancel the matching live agent turn without bypassing turn ownership."""
        self._require_delegation(principal)
        return await self._turn_control.cancel(
            principal=principal, conversation_id=conversation_id, turn_id=turn_id
        )

    async def close_conversation(
        self,
        *,
        principal: InvocationPrincipal,
        conversation_id: str,
    ) -> AgentDelegationResult:
        """Close one owned delegated conversation after any live turn has settled."""
        self._require_delegation(principal)
        return await self._turn_control.close(principal=principal, conversation_id=conversation_id)

    def _request(
        self,
        resolved: ResolvedConversationHandle,
        principal: InvocationPrincipal,
        message: str,
        allow_actions: bool,
    ) -> ChatTurnRequest:
        turn_id = uuid.uuid4().hex
        invocation = InvocationContext(
            principal=principal,
            conversation_id=resolved.handle_id,
            turn_id=turn_id,
            allow_actions=bool(allow_actions),
        )
        return ChatTurnRequest(
            prompt=str(message or ""),
            session_id=resolved.internal_session_id,
            user_id=resolved.user_id,
            turn_id=turn_id,
            invocation_context=invocation,
        )

    async def _await_turn(
        self,
        resolved: ResolvedConversationHandle,
        active: ActiveChatTurn,
    ) -> AgentDelegationResult:
        try:
            outcome = await active.task
            return self._outcome_result(resolved, active.turn_id, outcome)
        except asyncio.CancelledError:
            await self._turn_registry.cancel_and_wait(resolved.internal_session_id, active.turn_id)
            raise
        except Exception:
            return AgentDelegationResult(
                conversation_id=resolved.handle_id,
                turn_id=active.turn_id,
                status=AgentDelegationStatus.FAILED,
                message="The LJS agent turn failed. See diagnostics for details.",
            )
        finally:
            if active.task.done():
                await self._turn_registry.release(resolved.internal_session_id, active.turn_id)

    @staticmethod
    def _busy_result(
        resolved: ResolvedConversationHandle,
        active: ActiveChatTurn,
    ) -> AgentDelegationResult:
        return AgentDelegationResult(
            conversation_id=resolved.handle_id,
            turn_id=active.turn_id,
            status=AgentDelegationStatus.BUSY,
            message="This conversation already has an active LJS agent turn.",
        )

    @staticmethod
    def _outcome_result(
        resolved: ResolvedConversationHandle,
        turn_id: str,
        outcome: ChatTurnOutcome,
    ) -> AgentDelegationResult:
        return AgentDelegationResult(
            conversation_id=resolved.handle_id,
            turn_id=turn_id,
            status=AgentDelegationStatus(outcome.status),
            message=outcome.message,
            result_set_ids=list(outcome.result_set_ids),
            candidate_ids=list(outcome.candidate_ids),
            action_receipt_ids=list(outcome.action_receipt_ids),
        )

    @staticmethod
    def _require_delegation(principal: InvocationPrincipal) -> None:
        if not principal.allows(InvocationCapability.AGENT_DELEGATE):
            raise AgentDelegationDenied("This principal cannot delegate requests to the LJS agent")
