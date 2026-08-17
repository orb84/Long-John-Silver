"""Protocol-neutral invocation identity and application capabilities."""

from __future__ import annotations

from enum import Enum
from typing import ClassVar

from pydantic import BaseModel, Field, field_validator


class InvocationCapability(str, Enum):
    """Application capabilities granted to one invocation principal."""

    AGENT_DELEGATE = "agent.delegate"
    AGENT_READ = "agent.read"
    STATUS_READ = "status.read"
    LIBRARY_READ = "library.read"
    LIBRARY_WRITE = "library.write"
    LIBRARY_FILES_DELETE = "library.files.delete"
    DOWNLOADS_READ = "downloads.read"
    DOWNLOADS_WRITE = "downloads.write"
    TRACKING_WRITE = "tracking.write"
    CONFIG_READ = "config.read"
    CONFIG_WRITE = "config.write"
    CONFIG_LLM_READ = "config.llm.read"
    CONFIG_LLM_PROBE = "config.llm.probe"
    CONFIG_LLM_WRITE = "config.llm.write"
    CONFIG_LLM_ENDPOINT_WRITE = "config.llm.endpoint.write"
    DIAGNOSTICS_READ = "diagnostics.read"
    ADMIN = "admin"


class InvocationPrincipal(BaseModel):
    """Authenticated application identity independent of transport protocol."""

    principal_id: str
    user_id: str | None = None
    client_id: str | None = None
    source: str = "internal"
    capabilities: set[InvocationCapability] = Field(default_factory=set)
    trusted: bool = False

    @field_validator("principal_id", "source")
    @classmethod
    def _require_identity(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("Invocation identity fields cannot be empty")
        if len(normalized) > 200:
            raise ValueError("Invocation identity fields cannot exceed 200 characters")
        return normalized

    def allows(self, capability: InvocationCapability | str) -> bool:
        """Return whether this principal grants ``capability``."""
        if self.trusted or InvocationCapability.ADMIN in self.capabilities:
            return True
        try:
            normalized = InvocationCapability(str(getattr(capability, "value", capability)))
        except ValueError:
            return False
        return normalized in self.capabilities

    def allows_all(self, capabilities: set[InvocationCapability]) -> bool:
        """Return whether every required capability is granted."""
        return all(self.allows(capability) for capability in capabilities)


class InvocationEvidence(BaseModel):
    """Stable non-secret handles produced during one delegated invocation."""

    result_set_ids: list[str] = Field(default_factory=list)
    candidate_ids: list[str] = Field(default_factory=list)
    action_receipt_ids: list[str] = Field(default_factory=list)
    needs_input: bool = False


class InvocationContext(BaseModel):
    """Execution context shared by chat, tools, and public control surfaces."""

    _WRITE_CAPABILITIES: ClassVar[set[InvocationCapability]] = {
        InvocationCapability.DOWNLOADS_WRITE,
        InvocationCapability.LIBRARY_WRITE,
        InvocationCapability.LIBRARY_FILES_DELETE,
        InvocationCapability.TRACKING_WRITE,
        InvocationCapability.CONFIG_WRITE,
        InvocationCapability.CONFIG_LLM_WRITE,
        InvocationCapability.CONFIG_LLM_ENDPOINT_WRITE,
    }

    principal: InvocationPrincipal
    conversation_id: str | None = None
    turn_id: str | None = None
    allow_actions: bool = True
    evidence: InvocationEvidence = Field(default_factory=InvocationEvidence)

    def effective_capabilities(self) -> set[InvocationCapability]:
        """Return capabilities effective for this particular delegation call."""
        if self.principal.trusted:
            return set(InvocationCapability)
        if InvocationCapability.ADMIN in self.principal.capabilities:
            capabilities = set(InvocationCapability)
        else:
            capabilities = set(self.principal.capabilities)
        if not self.allow_actions:
            capabilities.difference_update(self._WRITE_CAPABILITIES)
            capabilities.discard(InvocationCapability.ADMIN)
        return capabilities

    def allows(self, capability: InvocationCapability | str) -> bool:
        """Return whether the effective invocation grants ``capability``."""
        if self.principal.trusted:
            return True
        try:
            normalized = InvocationCapability(str(getattr(capability, "value", capability)))
        except ValueError:
            return False
        return normalized in self.effective_capabilities()


class AgentDelegationStatus(str, Enum):
    """Terminal/public states returned by protocol-neutral agent delegation."""

    COMPLETE = "complete"
    NEEDS_INPUT = "needs_input"
    BUSY = "busy"
    NOT_RUNNING = "not_running"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    CLOSED = "closed"
    FAILED = "failed"


class AgentDelegationResult(BaseModel):
    """Structured result returned to external agent adapters."""

    conversation_id: str
    turn_id: str | None = None
    status: AgentDelegationStatus
    message: str = ""
    result_set_ids: list[str] = Field(default_factory=list)
    candidate_ids: list[str] = Field(default_factory=list)
    action_receipt_ids: list[str] = Field(default_factory=list)
    matched: bool | None = None
    cancellation_requested: bool | None = None
    settled: bool | None = None
