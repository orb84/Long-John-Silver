"""Configuration for LJS's local MCP transport adapter."""

from __future__ import annotations

import os
from dataclasses import dataclass

from src.core.models import InvocationCapability


@dataclass(frozen=True, slots=True)
class MCPIntegrationSettings:
    """Environment-backed settings for the opt-in local MCP endpoint."""

    enabled: bool
    bearer_token: str
    principal_id: str
    user_id: str
    client_id: str
    capabilities: frozenset[InvocationCapability]
    mount_path: str = "/mcp"

    _DEFAULT_CAPABILITIES = frozenset(
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
    def from_environment(cls) -> "MCPIntegrationSettings":
        """Load a fail-closed local MCP configuration from environment variables."""
        enabled = cls._truthy(os.getenv("LJS_MCP_ENABLED", "0"))
        token = str(os.getenv("LJS_MCP_TOKEN", "") or "").strip()
        principal_id = str(os.getenv("LJS_MCP_PRINCIPAL_ID", "mcp-local") or "mcp-local").strip()
        user_id = str(os.getenv("LJS_MCP_USER_ID", "local") or "local").strip() or "local"
        client_id = str(os.getenv("LJS_MCP_CLIENT_ID", principal_id) or principal_id).strip() or principal_id
        configured = str(os.getenv("LJS_MCP_CAPABILITIES", "") or "").strip()
        if not enabled:
            return cls(
                enabled=False,
                bearer_token=token,
                principal_id=principal_id,
                user_id=user_id,
                client_id=client_id,
                capabilities=cls._DEFAULT_CAPABILITIES,
            )
        capabilities = cls._parse_capabilities(configured) if configured else cls._DEFAULT_CAPABILITIES
        if not token:
            raise ValueError("LJS_MCP_TOKEN is required when LJS_MCP_ENABLED=1")
        if len(token) < 32:
            raise ValueError("LJS_MCP_TOKEN must be at least 32 characters when configured")
        return cls(
            enabled=enabled,
            bearer_token=token,
            principal_id=principal_id,
            user_id=user_id,
            client_id=client_id,
            capabilities=capabilities,
        )

    @staticmethod
    def _truthy(value: str) -> bool:
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _parse_capabilities(value: str) -> frozenset[InvocationCapability]:
        resolved: set[InvocationCapability] = set()
        invalid: list[str] = []
        for raw in value.split(","):
            name = raw.strip()
            if not name:
                continue
            try:
                resolved.add(InvocationCapability(name))
            except ValueError:
                invalid.append(name)
        if invalid:
            raise ValueError(f"Unknown LJS_MCP_CAPABILITIES values: {', '.join(sorted(invalid))}")
        return frozenset(resolved)
