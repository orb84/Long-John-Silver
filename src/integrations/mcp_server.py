"""Thin MCP adapter over LJS's protocol-neutral public control plane."""

from __future__ import annotations

import json
from typing import Any

from src.core.models import InvocationCapability, InvocationPrincipal
from src.core.public_control_plane_facade import PublicControlPlane
from src.integrations.mcp_auth import (
    MCPAuthenticationBoundary,
    MCPPrincipalResolver,
    MCPRequestPrincipalContext,
)
from src.integrations.mcp_network import LocalMCPNetworkBoundary

try:  # Optional until the operator enables MCP and installs requirements.
    from mcp.server import MCPServer
    from mcp.server.mcpserver import Context
except ImportError:  # pragma: no cover - exercised only in dependency-poor bootstrap environments.
    MCPServer = None  # type: ignore[assignment]

    class Context:  # type: ignore[no-redef]
        """Import-time placeholder; never instantiated when MCP is enabled."""


class MCPServerAdapter:
    """Register a deliberately small public MCP surface over LJS services."""

    def __init__(self, *, control_plane: PublicControlPlane, principal_resolver: MCPPrincipalResolver) -> None:
        if MCPServer is None:
            raise RuntimeError("MCP is enabled but the 'mcp>=2,<3' package is not installed")
        self._control = control_plane
        self._principal_resolver = principal_resolver
        self._server = MCPServer("LJS")
        self._register_tools()
        self._register_resources()
        protocol_app = self._server.streamable_http_app(streamable_http_path="/")
        authenticated_app = MCPAuthenticationBoundary(protocol_app, self._principal_resolver)
        self._asgi_app = LocalMCPNetworkBoundary(authenticated_app)

    @property
    def asgi_app(self) -> object:
        """Return the mounted Streamable-HTTP ASGI application."""
        return self._asgi_app

    @property
    def session_manager(self) -> object:
        """Return the SDK session manager after the ASGI app has initialized it."""
        return self._server.session_manager

    async def agent_message(
        self,
        message: str,
        ctx: Context,
        conversation_id: str | None = None,
        allow_actions: bool = False,
    ) -> dict[str, Any]:
        """Delegate a semantic request to the canonical LJS domain agent."""
        principal = await self._principal(ctx)
        result = await self._control.agent.send_message(
            principal=principal,
            message=message,
            conversation_id=conversation_id,
            allow_actions=allow_actions,
        )
        return result.model_dump(mode="json")

    async def agent_cancel(
        self,
        conversation_id: str,
        ctx: Context,
        turn_id: str | None = None,
    ) -> dict[str, Any]:
        """Cancel one live delegated LJS agent turn using shared turn ownership."""
        principal = await self._principal(ctx)
        result = await self._control.agent.cancel_turn(
            principal=principal,
            conversation_id=conversation_id,
            turn_id=turn_id,
        )
        return result.model_dump(mode="json")

    async def agent_close(
        self,
        conversation_id: str,
        ctx: Context,
    ) -> dict[str, Any]:
        """Close one owned delegated conversation after cancellation settles."""
        principal = await self._principal(ctx)
        result = await self._control.agent.close_conversation(
            principal=principal,
            conversation_id=conversation_id,
        )
        return result.model_dump(mode="json")

    async def status(self, ctx: Context) -> dict[str, Any]:
        """Return bounded LJS health/status information."""
        principal = await self._principal(ctx)
        return self._control.status.get(principal)

    async def capabilities(self, ctx: Context) -> dict[str, Any]:
        """Return application capabilities attached to the validated caller."""
        principal = await self._principal(ctx)
        return self._capability_payload(principal)

    async def library_list(self, ctx: Context, offset: int = 0, limit: int = 25) -> dict[str, Any]:
        """Return one bounded page of canonical tracked-library summaries."""
        principal = await self._principal(ctx)
        return await self._control.library.list_items(principal, offset=offset, limit=limit)

    async def library_get(self, category_id: str, item_id: str, ctx: Context) -> dict[str, Any] | None:
        """Return one canonical library item by stable category/item identity."""
        principal = await self._principal(ctx)
        return await self._control.library.get_item(
            principal,
            category_id=category_id,
            item_id=item_id,
        )

    async def downloads_list(self, ctx: Context, limit: int = 100) -> dict[str, Any]:
        """Return bounded active-download state without search orchestration."""
        principal = await self._principal(ctx)
        return await self._control.downloads.list_active(principal, limit=limit)

    async def llm_get(self, ctx: Context) -> dict[str, Any]:
        """Return configured and effective LLM routing without secrets."""
        principal = await self._principal(ctx)
        return self._control.llm.get(principal)

    async def llm_test(self, ctx: Context) -> dict[str, Any]:
        """Probe the configured LLM provider/model route."""
        principal = await self._principal(ctx)
        return await self._control.llm.test(principal)

    async def llm_set(
        self,
        values: dict[str, Any],
        ctx: Context,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Mutate LLM routing through ActionGateway and return its durable receipt."""
        principal = await self._principal(ctx)
        return await self._control.llm.set(
            principal,
            values=values,
            idempotency_key=idempotency_key,
        )

    async def diagnostics_recent(self, ctx: Context, limit: int = 20) -> dict[str, Any]:
        """Return bounded secret-redacted recent LLM diagnostics."""
        principal = await self._principal(ctx)
        return self._control.diagnostics.recent(principal, limit=limit)

    async def status_resource(self, ctx: Context) -> str:
        """Render the bounded status resource as JSON."""
        return self._json(await self.status(ctx))

    async def capabilities_resource(self, ctx: Context) -> str:
        """Render validated caller capabilities as JSON."""
        return self._json(await self.capabilities(ctx))

    async def library_summary_resource(self, ctx: Context) -> str:
        """Render a deliberately bounded first page of library summaries."""
        return self._json(await self.library_list(ctx, offset=0, limit=25))

    async def downloads_resource(self, ctx: Context) -> str:
        """Render bounded active-download state as JSON."""
        return self._json(await self.downloads_list(ctx, limit=100))

    async def llm_resource(self, ctx: Context) -> str:
        """Render configured/effective LLM routing as JSON."""
        return self._json(await self.llm_get(ctx))

    async def _principal(self, ctx: Context) -> InvocationPrincipal:
        """Return the principal authenticated once at the outer ASGI boundary."""
        del ctx
        return MCPRequestPrincipalContext.require()

    def _register_tools(self) -> None:
        self._server.tool(name="ljs.agent_message")(self.agent_message)
        self._server.tool(name="ljs.agent_cancel")(self.agent_cancel)
        self._server.tool(name="ljs.agent_close")(self.agent_close)
        self._server.tool(name="ljs.status")(self.status)
        self._server.tool(name="ljs.capabilities")(self.capabilities)
        self._server.tool(name="ljs.library_list")(self.library_list)
        self._server.tool(name="ljs.library_get")(self.library_get)
        self._server.tool(name="ljs.downloads_list")(self.downloads_list)
        self._server.tool(name="ljs.llm_get")(self.llm_get)
        self._server.tool(name="ljs.llm_test")(self.llm_test)
        self._server.tool(name="ljs.llm_set")(self.llm_set)
        self._server.tool(name="ljs.diagnostics_recent")(self.diagnostics_recent)

    def _register_resources(self) -> None:
        self._server.resource("ljs://status", mime_type="application/json")(self.status_resource)
        self._server.resource("ljs://capabilities", mime_type="application/json")(self.capabilities_resource)
        self._server.resource("ljs://library/summary", mime_type="application/json")(self.library_summary_resource)
        self._server.resource("ljs://downloads/active", mime_type="application/json")(self.downloads_resource)
        self._server.resource("ljs://configuration/llm", mime_type="application/json")(self.llm_resource)

    @staticmethod
    def _capability_payload(principal: InvocationPrincipal) -> dict[str, Any]:
        capabilities = set(InvocationCapability) if principal.allows(InvocationCapability.ADMIN) else principal.capabilities
        return {
            "principal_id": principal.principal_id,
            "source": principal.source,
            "capabilities": sorted(capability.value for capability in capabilities),
            "agent_actions_default": False,
        }

    @staticmethod
    def _json(payload: object) -> str:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
