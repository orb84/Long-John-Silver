"""Host-lifespan ownership for the MCP SDK session manager."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class MCPHostRuntime:
    """Let the top-level FastAPI app own any mounted MCP session manager."""

    def __init__(self) -> None:
        self._adapter: object | None = None

    def configure(self, adapter: object) -> None:
        """Bind the mounted adapter before the host application starts."""
        self._adapter = adapter

    @asynccontextmanager
    async def lifespan(self, app: object) -> AsyncIterator[None]:
        """Run the mounted MCP session manager for the host app lifetime."""
        adapter = self._adapter
        if adapter is None:
            yield
            return
        session_manager = getattr(adapter, "session_manager", None)
        if session_manager is None:
            raise RuntimeError("Configured MCP adapter has no session manager")
        async with session_manager.run():
            yield
