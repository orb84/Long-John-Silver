"""Audited settings mutation handler for the local MCP integration."""

from __future__ import annotations

from typing import Any


class MCPSettingsActionHandler:
    """Apply persisted MCP configuration through the runtime controller."""

    def __init__(self, controller: Any) -> None:
        self._controller = controller

    async def update(self, **kwargs: Any) -> dict[str, Any]:
        """Validate, activate/deactivate, and persist MCP settings atomically."""
        if self._controller is None:
            raise RuntimeError("MCP runtime controller is unavailable")
        return await self._controller.apply(dict(kwargs))
