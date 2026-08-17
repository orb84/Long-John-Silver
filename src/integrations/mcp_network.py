"""Network boundary for the initial local-only MCP transport."""

from __future__ import annotations

import ipaddress
from typing import Any


class LocalMCPNetworkBoundary:
    """Reject network MCP requests that did not originate on loopback."""

    def __init__(self, app: Any) -> None:
        self._app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or self._is_loopback(scope.get("client")):
            await self._app(scope, receive, send)
            return
        await send({"type": "http.response.start", "status": 403, "headers": [(b"content-type", b"text/plain; charset=utf-8")]})
        await send({"type": "http.response.body", "body": b"LJS MCP is local-only in this release."})

    @staticmethod
    def _is_loopback(client: object) -> bool:
        if client is None:
            return False
        if not isinstance(client, (tuple, list)) or not client:
            return False
        host = str(client[0] or "").strip()
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return host.casefold() == "localhost"
