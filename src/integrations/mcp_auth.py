"""Validated application-principal resolution for the local MCP adapter."""

from __future__ import annotations

import secrets
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from src.core.models import InvocationPrincipal
from src.integrations.mcp_configuration import MCPIntegrationSettings


class MCPAuthenticationError(PermissionError):
    """Raised when an MCP request has no valid dedicated LJS MCP credential."""


class MCPRequestPrincipalContext:
    """Carry one already-authenticated MCP principal through protocol handling."""

    _principal: ContextVar[InvocationPrincipal | None] = ContextVar(
        "ljs_mcp_principal", default=None
    )

    @classmethod
    @contextmanager
    def bind(cls, principal: InvocationPrincipal) -> Iterator[None]:
        """Bind ``principal`` for the lifetime of one authenticated ASGI request."""
        token = cls._principal.set(principal)
        try:
            yield
        finally:
            cls._principal.reset(token)

    @classmethod
    def require(cls) -> InvocationPrincipal:
        """Return the already-validated request principal or fail closed."""
        principal = cls._principal.get()
        if principal is None:
            raise MCPAuthenticationError("MCP request principal is unavailable")
        return principal


class MCPPrincipalResolver:
    """Resolve the dedicated MCP bearer credential into an application principal.

    Generic LJS web JWTs are deliberately not accepted here. A browser/session
    credential has no MCP audience or capability grant and must never be widened
    into an external-control-plane administrator credential.
    """

    def __init__(
        self,
        *,
        settings: MCPIntegrationSettings,
        auth_service: object | None = None,
        database: object | None = None,
    ) -> None:
        # ``auth_service``/``database`` remain accepted for one compatibility
        # release so older composition code/test doubles do not break, but web
        # credentials are intentionally outside the MCP authentication contract.
        self._settings = settings

    async def resolve(self, headers: Mapping[str, str] | None) -> InvocationPrincipal:
        """Authenticate one HTTP MCP request using only ``LJS_MCP_TOKEN``."""
        token = self._bearer_token(headers)
        configured = self._settings.bearer_token
        if not token or not configured or not secrets.compare_digest(token, configured):
            raise MCPAuthenticationError("MCP bearer token is invalid or missing")
        return InvocationPrincipal(
            principal_id=self._settings.principal_id,
            user_id=self._settings.user_id,
            client_id=self._settings.client_id,
            source="mcp",
            capabilities=set(self._settings.capabilities),
            trusted=False,
        )

    @staticmethod
    def _bearer_token(headers: Mapping[str, str] | None) -> str:
        authorization = ""
        for key, value in (headers or {}).items():
            if str(key).casefold() == "authorization":
                authorization = str(value or "").strip()
                break
        if not authorization.lower().startswith("bearer "):
            return ""
        return authorization[7:].strip()


class MCPAuthenticationBoundary:
    """Authenticate once before MCP protocol handling and propagate the principal."""

    def __init__(self, app: Any, resolver: MCPPrincipalResolver) -> None:
        self._app = app
        self._resolver = resolver

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return
        headers = self._headers(scope.get("headers"))
        try:
            principal = await self._resolver.resolve(headers)
        except MCPAuthenticationError:
            await send({
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"text/plain; charset=utf-8"),
                    (b"www-authenticate", b"Bearer"),
                    (b"cache-control", b"no-store"),
                ],
            })
            await send({
                "type": "http.response.body",
                "body": b"LJS MCP authentication failed.",
            })
            return
        with MCPRequestPrincipalContext.bind(principal):
            await self._app(scope, receive, send)

    @staticmethod
    def _headers(raw_headers: object) -> dict[str, str]:
        if not isinstance(raw_headers, (list, tuple)):
            return {}
        headers: dict[str, str] = {}
        for raw_name, raw_value in raw_headers:
            name = raw_name.decode("latin-1") if isinstance(raw_name, bytes) else str(raw_name)
            value = raw_value.decode("latin-1") if isinstance(raw_value, bytes) else str(raw_value)
            headers[name] = value
        return headers
