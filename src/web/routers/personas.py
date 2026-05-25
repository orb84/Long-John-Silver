"""Persona package API endpoints.

The frontend uses these endpoints to render assistant chrome — active persona
name, avatar, and bounded theme hints — while the assistant backend uses the
same registry for prompts.  Keeping both paths behind this router prevents the
UI from reaching directly into arbitrary user-configured file paths.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from src.ai.persona_registry import PersonaRegistry
from src.core.models import ActionCommand, ActionSource
from src.web.dependencies import WebDependencies, verify_auth


class PersonasRouter:
    """Class-based router for persona package discovery and activation."""

    def __init__(self, deps: WebDependencies) -> None:
        self._deps = deps

    def get_router(self) -> APIRouter:
        """Build and return persona package endpoints."""
        router = APIRouter()
        router.add_api_route("/api/personas", self._list_personas, methods=["GET"])
        router.add_api_route("/api/personas/active", self._active_persona, methods=["GET"])
        router.add_api_route("/api/personas/active", self._set_active_persona, methods=["POST"])
        router.add_api_route("/api/personas/{persona_id}/avatar", self._avatar, methods=["GET"])
        return router

    @property
    def _registry(self) -> PersonaRegistry:
        """Create a registry rooted at the current project working directory."""
        return PersonaRegistry(Path.cwd())

    async def _list_personas(self, _auth: bool = Depends(verify_auth)) -> dict:
        """List valid persona packages and include the active package summary."""
        active_id = self._deps.settings_manager.settings.active_persona or "default"
        packages = self._registry.list_packages()
        active = self._registry.load(active_id)
        return {
            "active_persona": active.id,
            "personas": [package.api_summary(active=package.id == active.id) for package in packages],
            "active": active.api_summary(active=True),
            "personas_dir": str(self._registry.personas_dir),
        }

    async def _active_persona(self, _auth: bool = Depends(verify_auth)) -> dict:
        """Return the active persona metadata for lightweight UI bootstrapping."""
        active_id = self._deps.settings_manager.settings.active_persona or "default"
        active = self._registry.load(active_id)
        return {
            "active_persona": active.id,
            "active": active.api_summary(active=True),
            "personas_dir": str(self._registry.personas_dir),
        }

    async def _set_active_persona(self, request: Request, _auth: bool = Depends(verify_auth)) -> dict:
        """Switch the active persona through the shared ActionGateway."""
        body = await request.json()
        persona_id = str(body.get("persona_id") or body.get("active_persona") or "").strip()
        if not persona_id:
            raise HTTPException(status_code=400, detail="persona_id is required")
        result = await self._deps.action_gateway.execute(ActionCommand(
            name="settings_update_persona",
            arguments={"active_persona": persona_id},
            source=ActionSource.UI,
        ))
        if not result.ok:
            raise HTTPException(status_code=400, detail=result.error or "Persona update failed")
        return result.data

    async def _avatar(self, persona_id: str, _auth: bool = Depends(verify_auth)) -> FileResponse:
        """Serve a resolved local avatar file for a persona package.

        The registry already validates that the file is inside the package
        directory and has an allowed image extension.  Missing avatars are a
        404 so the browser can fall back to the bundled static icon.
        """
        package = self._registry.load(persona_id)
        if package.id != persona_id or not package.avatar_path or not package.avatar_path.exists():
            raise HTTPException(status_code=404, detail="Persona avatar not found")
        media_type = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }.get(package.avatar_path.suffix.lower(), "application/octet-stream")
        return FileResponse(str(package.avatar_path), media_type=media_type)
