"""Application-owned configuration and host lifecycle for local MCP."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from src.core.models import MCPSettings
from src.integrations.mcp_configuration import MCPIntegrationSettings
from src.integrations.mcp_runtime_worker import MCPDynamicMount, MCPRuntimeWorker


class MCPRuntimeController:
    """Coordinate persisted MCP Settings with the lifespan-owned runtime worker."""

    def __init__(
        self,
        *,
        settings_manager: Any,
        control_plane: Any,
        database: Any,
        auth_service: Any | None = None,
    ) -> None:
        self._settings_manager = settings_manager
        self._database = database
        self._worker = MCPRuntimeWorker(
            control_plane=control_plane,
            database=database,
            auth_service=auth_service,
        )
        self._settings_lock = asyncio.Lock()
        self._last_error = ""

    @property
    def asgi_app(self) -> MCPDynamicMount:
        """Return the stable ASGI dispatcher mounted by the host app."""
        return self._worker.asgi_app

    @property
    def running(self) -> bool:
        """Return whether the MCP SDK runtime is observably active."""
        return self._worker.running

    @property
    def last_error(self) -> str:
        """Return the most recent bounded startup/transition failure."""
        return self._last_error

    async def start(self) -> None:
        """Start the worker and best-effort apply persisted MCP configuration."""
        runtime: MCPIntegrationSettings | None = None
        try:
            settings = self._settings_manager.settings.mcp
            if settings.enabled:
                self._ensure_persisted_token(settings)
                await self._validate_user_binding(settings)
                runtime = MCPIntegrationSettings.from_application(settings)
        except Exception as exc:
            self._last_error = self._safe_error(exc)

        startup_error = await self._worker.start(runtime)
        if startup_error is not None:
            self._last_error = self._safe_error(startup_error)
        elif runtime is not None:
            self._last_error = ""

    async def start_configured(self) -> None:
        """Compatibility alias for tests/host code that request configured startup."""
        await self.start()

    async def apply(self, values: dict[str, Any]) -> dict[str, Any]:
        """Validate, transition, and persist one MCP Settings mutation atomically."""
        async with self._settings_lock:
            current = self._settings_manager.settings.mcp
            previous = current.model_copy(deep=True)
            candidate = self._build_candidate(current, values)
            await self._validate_user_binding(candidate)
            runtime = MCPIntegrationSettings.from_application(candidate)
            try:
                await self._worker.apply(runtime)
            except asyncio.CancelledError:
                restore_error = await self._restore(previous, persist=False)
                if restore_error:
                    self._last_error = restore_error
                raise
            except Exception as exc:
                restore_error = await self._restore(previous, persist=False)
                self._last_error = self._failure_message(exc, restore_error)
                raise RuntimeError(self._last_error) from exc

            try:
                self._persist(candidate)
            except Exception as exc:
                restore_error = await self._restore(previous, persist=True)
                self._last_error = self._failure_message(exc, restore_error)
                raise RuntimeError(self._last_error) from exc

            self._last_error = ""
            return self.snapshot(include_token=True)

    async def shutdown(self) -> None:
        """Stop the host-owned runtime worker."""
        await self._worker.shutdown()

    async def stop(self) -> None:
        """Compatibility alias for host shutdown."""
        await self.shutdown()

    def snapshot(self, *, include_token: bool = False) -> dict[str, Any]:
        """Return bounded persisted/runtime state for Settings and status APIs."""
        settings = self._settings_manager.settings.mcp
        payload = {
            "enabled": bool(settings.enabled),
            "running": self.running,
            "principal_id": settings.principal_id,
            "user_id": settings.user_id,
            "client_id": settings.client_id,
            "capabilities": list(settings.capabilities),
            "last_error": self._last_error,
            "transport": "streamable_http",
            "local_only": True,
        }
        if include_token:
            payload["bearer_token"] = settings.bearer_token
        else:
            payload["token_configured"] = bool(settings.bearer_token)
        return payload

    def _ensure_persisted_token(self, settings: MCPSettings) -> None:
        if settings.bearer_token:
            return
        token = self.generate_token()
        settings.bearer_token = token
        try:
            self._settings_manager.save(self._settings_manager.settings)
        except Exception:
            settings.bearer_token = ""
            raise

    def _persist(self, candidate: MCPSettings) -> None:
        self._settings_manager.settings.mcp = candidate
        self._settings_manager.save(self._settings_manager.settings)

    async def _restore(self, previous: MCPSettings, *, persist: bool) -> str:
        """Restore canonical Settings/runtime and return bounded rollback errors."""
        self._settings_manager.settings.mcp = previous
        errors: list[str] = []
        if persist:
            try:
                self._settings_manager.save(self._settings_manager.settings)
            except Exception as exc:
                errors.append(f"settings rollback failed: {self._safe_error(exc)}")
        try:
            await self._worker.apply(MCPIntegrationSettings.from_application(previous))
        except Exception as exc:
            errors.append(f"runtime rollback failed: {self._safe_error(exc)}")
        return "; ".join(errors)

    async def _validate_user_binding(self, settings: MCPSettings) -> None:
        user_id = str(settings.user_id or "local").strip() or "local"
        if user_id == "local":
            return
        users = getattr(self._database, "users", None)
        get_user = getattr(users, "get_user_by_id", None) if users is not None else None
        if not callable(get_user) or await get_user(user_id) is None:
            raise ValueError(f"Configured MCP user '{user_id}' does not exist")

    @staticmethod
    def _build_candidate(current: MCPSettings, values: dict[str, Any]) -> MCPSettings:
        payload = current.model_dump()
        for key in {"enabled", "principal_id", "user_id", "client_id", "capabilities"}:
            if key in values:
                payload[key] = values[key]
        if values.get("regenerate_token") or (bool(payload.get("enabled")) and not payload.get("bearer_token")):
            payload["bearer_token"] = MCPRuntimeController.generate_token()
        return MCPSettings(**payload)

    @staticmethod
    def generate_token() -> str:
        """Generate a strong URL-safe dedicated MCP bearer token."""
        return secrets.token_urlsafe(32)

    @classmethod
    def _failure_message(cls, exc: Exception, restore_error: str) -> str:
        base = cls._safe_error(exc)
        if not restore_error:
            return base
        return f"{base} ({restore_error})"[:300]

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        text = str(exc or "MCP runtime failed").strip() or exc.__class__.__name__
        return text[:300]


class MCPHostRuntime:
    """Top-level FastAPI lifespan owner for the dynamically mounted MCP runtime."""

    def __init__(self) -> None:
        self._controller: MCPRuntimeController | None = None

    def configure(self, controller: MCPRuntimeController) -> None:
        """Bind the controller before the host application starts."""
        self._controller = controller

    @asynccontextmanager
    async def lifespan(self, app: object) -> AsyncIterator[None]:
        """Own the MCP worker for the complete FastAPI lifespan."""
        del app
        controller = self._controller
        if controller is None:
            yield
            return
        await controller.start()
        try:
            yield
        finally:
            await controller.shutdown()
