"""Single-task owner for the live MCP SDK transport lifecycle."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal

from src.integrations.mcp_auth import MCPPrincipalResolver
from src.integrations.mcp_configuration import MCPIntegrationSettings
from src.integrations.mcp_server import MCPServerAdapter


class MCPDynamicMount:
    """Stable ASGI mount that forwards to the currently active MCP adapter."""

    def __init__(self) -> None:
        self._app: Any | None = None

    def bind(self, app: Any | None) -> None:
        """Atomically replace the active MCP ASGI application."""
        self._app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        app = self._app
        if app is not None:
            await app(scope, receive, send)
            return
        await send({
            "type": "http.response.start",
            "status": 503,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"cache-control", b"no-store"),
            ],
        })
        await send({
            "type": "http.response.body",
            "body": b"LJS MCP is disabled or not running. Enable it in Compass settings.",
        })


@dataclass(slots=True)
class _MCPRuntimeCommand:
    """One transition executed by the lifespan-owned worker task."""

    kind: Literal["apply", "shutdown"]
    settings: MCPIntegrationSettings | None
    completion: asyncio.Future[None]


class MCPRuntimeWorker:
    """Own every MCP SDK session-manager context on one long-lived task."""

    def __init__(self, *, control_plane: Any, database: Any, auth_service: Any | None = None) -> None:
        self._control_plane = control_plane
        self._database = database
        self._auth_service = auth_service
        self._mount = MCPDynamicMount()
        self._adapter: MCPServerAdapter | None = None
        self._session_context: Any | None = None
        self._commands: asyncio.Queue[_MCPRuntimeCommand] | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def asgi_app(self) -> MCPDynamicMount:
        """Return the dispatcher that remains mounted for the host lifetime."""
        return self._mount

    @property
    def running(self) -> bool:
        """Return whether a protocol adapter and SDK session manager are active."""
        return self._adapter is not None and self._session_context is not None

    async def start(self, initial: MCPIntegrationSettings | None = None) -> Exception | None:
        """Start the worker and return a bounded startup activation failure, if any."""
        if self._task is not None and not self._task.done():
            return None
        loop = asyncio.get_running_loop()
        ready: asyncio.Future[Exception | None] = loop.create_future()
        self._commands = asyncio.Queue()
        self._task = asyncio.create_task(self._run(initial, ready), name="ljs-mcp-runtime")
        return await ready

    async def apply(self, settings: MCPIntegrationSettings) -> None:
        """Apply one runtime transition and wait until the owner task observes it."""
        self._require_running_worker()
        completion = asyncio.get_running_loop().create_future()
        await self._queue().put(_MCPRuntimeCommand("apply", settings, completion))
        try:
            await asyncio.shield(completion)
        except asyncio.CancelledError:
            # Do not abandon an already-enqueued SDK transition midway. The
            # controller can now restore its previous runtime deterministically.
            await asyncio.shield(completion)
            raise

    async def shutdown(self) -> None:
        """Stop the owner task after detaching/closing its current SDK runtime."""
        task = self._task
        if task is None:
            self._mount.bind(None)
            return
        if not task.done():
            completion = asyncio.get_running_loop().create_future()
            await self._queue().put(_MCPRuntimeCommand("shutdown", None, completion))
            await asyncio.shield(completion)
        await task
        self._task = None
        self._commands = None

    async def _run(
        self,
        initial: MCPIntegrationSettings | None,
        ready: asyncio.Future[Exception | None],
    ) -> None:
        startup_error = await self._activate_initial(initial)
        if not ready.done():
            ready.set_result(startup_error)
        while True:
            command = await self._queue().get()
            try:
                if command.kind == "shutdown":
                    await self._deactivate(suppress_errors=True)
                    self._complete(command, None)
                    return
                assert command.settings is not None
                await self._transition(command.settings)
                self._complete(command, None)
            except Exception as exc:
                self._complete(command, exc)

    async def _activate_initial(self, settings: MCPIntegrationSettings | None) -> Exception | None:
        if settings is None or not settings.enabled:
            self._mount.bind(None)
            return None
        try:
            await self._activate(settings)
            return None
        except Exception as exc:
            self._mount.bind(None)
            return exc

    async def _transition(self, settings: MCPIntegrationSettings) -> None:
        await self._deactivate(suppress_errors=False)
        if settings.enabled:
            await self._activate(settings)

    async def _activate(self, settings: MCPIntegrationSettings) -> None:
        adapter = MCPServerAdapter(
            control_plane=self._control_plane,
            principal_resolver=MCPPrincipalResolver(
                settings=settings,
                auth_service=self._auth_service,
                database=self._database,
            ),
        )
        context = adapter.session_manager.run()
        await context.__aenter__()
        self._adapter = adapter
        self._session_context = context
        self._mount.bind(adapter.asgi_app)

    async def _deactivate(self, *, suppress_errors: bool) -> None:
        context = self._session_context
        self._mount.bind(None)
        self._adapter = None
        self._session_context = None
        if context is None:
            return
        if suppress_errors:
            try:
                await context.__aexit__(None, None, None)
            except Exception:
                pass
            return
        await context.__aexit__(None, None, None)

    def _require_running_worker(self) -> None:
        if self._task is None or self._task.done():
            raise RuntimeError("MCP runtime worker is not running")

    def _queue(self) -> asyncio.Queue[_MCPRuntimeCommand]:
        if self._commands is None:
            raise RuntimeError("MCP runtime worker is not running")
        return self._commands

    @staticmethod
    def _complete(command: _MCPRuntimeCommand, exc: Exception | None) -> None:
        if command.completion.done():
            return
        if exc is None:
            command.completion.set_result(None)
        else:
            command.completion.set_exception(exc)
