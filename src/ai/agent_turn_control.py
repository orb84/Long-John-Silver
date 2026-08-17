"""Cancellation and lifecycle control for externally delegated agent turns."""

from __future__ import annotations

from src.ai.chat_turn_registry import ActiveChatTurn, ChatTurnRegistry
from src.core.conversation_handle import ConversationHandleService, ResolvedConversationHandle
from src.core.models import AgentDelegationResult, AgentDelegationStatus, InvocationPrincipal


class AgentTurnControlService:
    """Preserve cancellation settlement truth and owned conversation lifecycle."""

    def __init__(self, turn_registry: ChatTurnRegistry, conversation_handles: ConversationHandleService) -> None:
        self._turn_registry = turn_registry
        self._conversation_handles = conversation_handles

    async def cancel(
        self,
        *,
        principal: InvocationPrincipal,
        conversation_id: str,
        turn_id: str | None = None,
    ) -> AgentDelegationResult:
        """Request cancellation and report only the settlement state actually observed."""
        resolved = await self._conversation_handles.resolve(conversation_id, principal)
        active, settled = await self._turn_registry.cancel_and_wait(
            resolved.internal_session_id,
            turn_id,
        )
        if active is None:
            return self._cancel_result(resolved, turn_id, True, active=None)
        if settled:
            await self._turn_registry.release(resolved.internal_session_id, active.turn_id)
        return self._cancel_result(resolved, active.turn_id, settled, active=active)

    async def close(
        self,
        *,
        principal: InvocationPrincipal,
        conversation_id: str,
    ) -> AgentDelegationResult:
        """Cancel any live turn, then revoke the durable handle only after settlement."""
        resolved = await self._conversation_handles.resolve(conversation_id, principal)
        active, settled = await self._turn_registry.cancel_and_wait(resolved.internal_session_id)
        if active is not None and not settled:
            return self._cancel_result(resolved, active.turn_id, False, active=active)
        if active is not None:
            await self._turn_registry.release(resolved.internal_session_id, active.turn_id)
        await self._conversation_handles.revoke(conversation_id, principal)
        return AgentDelegationResult(
            conversation_id=conversation_id,
            turn_id=active.turn_id if active is not None else None,
            status=AgentDelegationStatus.CLOSED,
            message="The delegated LJS conversation handle is closed.",
            matched=active is not None,
            cancellation_requested=active is not None,
            settled=True,
        )

    @staticmethod
    def _cancel_result(
        resolved: ResolvedConversationHandle,
        turn_id: str | None,
        settled: bool,
        *,
        active: ActiveChatTurn | None,
    ) -> AgentDelegationResult:
        if active is None:
            return AgentDelegationResult(
                conversation_id=resolved.handle_id,
                turn_id=turn_id,
                status=AgentDelegationStatus.NOT_RUNNING,
                message="No matching active LJS agent turn was running.",
                matched=False,
                cancellation_requested=False,
                settled=True,
            )
        if settled:
            return AgentDelegationResult(
                conversation_id=resolved.handle_id,
                turn_id=turn_id,
                status=AgentDelegationStatus.CANCELLED,
                message="The LJS agent turn was cancelled and has settled.",
                matched=True,
                cancellation_requested=True,
                settled=True,
            )
        return AgentDelegationResult(
            conversation_id=resolved.handle_id,
            turn_id=turn_id,
            status=AgentDelegationStatus.CANCELLING,
            message="Cancellation was requested; the LJS agent turn is still unwinding.",
            matched=True,
            cancellation_requested=True,
            settled=False,
        )
