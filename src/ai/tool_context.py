"""Shared ToolExecutionContext construction for every agent loop."""

from __future__ import annotations

from src.core.invocation import InvocationContextResolver
from src.core.models import InvocationContext, ToolExecutionContext


class ToolExecutionContextFactory:
    """Build immutable per-call tool context from explicit invocation identity."""

    @classmethod
    def create(
        cls,
        *,
        invocation: InvocationContext | None,
        session_id: str | None,
        user_id: str | None = None,
        category_id: str | None = None,
        user_prompt: str | None = None,
    ) -> ToolExecutionContext:
        """Return context for a tool call, preserving first-party compatibility."""
        resolved = invocation or InvocationContextResolver.trusted_for_session(
            session_id,
            user_id=user_id,
        )
        principal = resolved.principal
        return ToolExecutionContext(
            user_id=principal.user_id or user_id,
            session_id=session_id,
            source=principal.source,
            actor=(f"external:{principal.principal_id}" if not principal.trusted else "user"),
            principal_id=principal.principal_id,
            client_id=principal.client_id,
            capabilities={capability.value for capability in resolved.effective_capabilities()},
            trusted=principal.trusted,
            allow_actions=resolved.allow_actions,
            invocation_evidence=resolved.evidence,
            category_id=category_id,
            user_prompt=user_prompt,
        )
