"""Validated configuration for LJS's local MCP transport adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from src.core.models import InvocationCapability, MCPSettings


@dataclass(frozen=True, slots=True)
class MCPIntegrationSettings:
    """Immutable runtime settings derived from persisted application settings."""

    enabled: bool
    bearer_token: str
    principal_id: str
    user_id: str
    client_id: str
    capabilities: frozenset[InvocationCapability]
    mount_path: str = "/mcp"

    DEFAULT_CAPABILITIES = frozenset(
        {
            InvocationCapability.AGENT_DELEGATE,
            InvocationCapability.AGENT_READ,
            InvocationCapability.STATUS_READ,
            InvocationCapability.LIBRARY_READ,
            InvocationCapability.DOWNLOADS_READ,
            InvocationCapability.CONFIG_LLM_READ,
            InvocationCapability.DIAGNOSTICS_READ,
        }
    )

    @classmethod
    def from_application(cls, settings: MCPSettings) -> "MCPIntegrationSettings":
        """Build fail-closed runtime settings from the canonical Settings model."""
        configured = cls._parse_capabilities(settings.capabilities)
        capabilities = configured or cls.DEFAULT_CAPABILITIES
        token = str(settings.bearer_token or "").strip()
        if settings.enabled:
            if not token:
                raise ValueError("MCP bearer token is required when MCP is enabled")
            if len(token) < 32:
                raise ValueError("MCP bearer token must be at least 32 characters")
        return cls(
            enabled=bool(settings.enabled),
            bearer_token=token,
            principal_id=str(settings.principal_id or "mcp-local").strip() or "mcp-local",
            user_id=str(settings.user_id or "local").strip() or "local",
            client_id=str(settings.client_id or settings.principal_id or "mcp-local").strip() or "mcp-local",
            capabilities=capabilities,
        )

    @classmethod
    def _parse_capabilities(cls, values: Iterable[str]) -> frozenset[InvocationCapability]:
        resolved: set[InvocationCapability] = set()
        invalid: list[str] = []
        for raw in values or []:
            name = str(raw or "").strip()
            if not name:
                continue
            try:
                resolved.add(InvocationCapability(name))
            except ValueError:
                invalid.append(name)
        if invalid:
            raise ValueError(f"Unknown MCP capability values: {', '.join(sorted(invalid))}")
        return frozenset(resolved)
