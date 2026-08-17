"""Invocation-context construction shared by all assistant transports."""

from __future__ import annotations

from src.core.models import InvocationCapability, InvocationContext, InvocationPrincipal



class InvocationContextResolver:
    """Construct explicit trusted/local invocation context in one place."""

    @classmethod
    def trusted_for_session(
        cls,
        session_id: str | None,
        *,
        user_id: str | None = None,
        turn_id: str | None = None,
    ) -> InvocationContext:
        """Return the compatibility principal for existing first-party surfaces."""
        source = cls.source_for_session(session_id)
        return InvocationContext(
            principal=InvocationPrincipal(
                principal_id=f"local:{source}",
                user_id=user_id,
                client_id=source,
                source=source,
                capabilities={InvocationCapability.ADMIN},
                trusted=True,
            ),
            turn_id=turn_id,
            allow_actions=True,
        )

    @staticmethod
    def source_for_session(session_id: str | None) -> str:
        """Resolve legacy bridge source once rather than inside every agent loop."""
        value = str(session_id or "").strip()
        if not value:
            return "web"
        if ":" in value:
            return value.split(":", 1)[0] or "web"
        if "_" in value:
            return value.split("_", 1)[0] or "web"
        return "web"


class InvocationCapabilityDenied(PermissionError):
    """Raised when a public control-plane method lacks an application capability."""


class InvocationCapabilityGuard:
    """Central fail-closed authorization guard for public application services."""

    @staticmethod
    def require(principal: InvocationPrincipal, capability: InvocationCapability) -> None:
        """Require one application capability or raise a transport-neutral error."""
        if not principal.allows(capability):
            raise InvocationCapabilityDenied(
                f"Principal '{principal.principal_id}' lacks capability '{capability.value}'"
            )
