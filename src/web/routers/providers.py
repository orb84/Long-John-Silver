"""
LLM provider API router for LJS.

Exposes REST endpoints for listing providers, managing API keys,
checking provider health, and activating provider presets.
"""

from fastapi import APIRouter, Depends, HTTPException, Request

from src.core.models import ActionCommand, ActionSource
from src.web.dependencies import WebDependencies, verify_auth


class ProvidersRouter:
    """Class-based router for LLM provider management endpoints."""

    def __init__(self, deps: WebDependencies) -> None:
        self._deps = deps

    async def _execute_action(self, name: str, arguments: dict) -> dict:
        """Execute an action through the gateway and return the data dict.

        Raises HTTPException on failure with an appropriate status code.
        """
        result = await self._deps.action_gateway.execute(ActionCommand(
            name=name,
            arguments=arguments,
            source=ActionSource.UI,
        ))
        if not result.ok:
            code = 404 if 'not found' in (result.error or '').lower() else 400
            raise HTTPException(status_code=code, detail=result.error or 'Action failed')
        return result.data

    def get_router(self) -> APIRouter:
        """Build and return an APIRouter with LLM provider management endpoints."""
        router = APIRouter()
        router.add_api_route("/api/providers", self._get_providers, methods=["GET"])
        router.add_api_route("/api/providers/security-status", self._get_key_store_security_status, methods=["GET"])
        router.add_api_route("/api/providers/{provider_id}/models", self._get_provider_models, methods=["GET"])
        router.add_api_route("/api/providers/{provider_id}/keys", self._list_provider_keys, methods=["GET"])
        router.add_api_route("/api/providers/{provider_id}/keys", self._add_provider_key, methods=["POST"])
        router.add_api_route("/api/providers/{provider_id}/keys/{key_id}", self._remove_provider_key, methods=["DELETE"])
        router.add_api_route("/api/providers/{provider_id}/keys/{key_id}/activate", self._activate_provider_key, methods=["POST"])
        router.add_api_route("/api/providers/{provider_id}/activate", self._activate_provider, methods=["POST"])
        router.add_api_route("/api/providers/{provider_id}/health", self._check_provider_health, methods=["GET"])
        return router

    async def _get_providers(self):
        deps = self._deps
        providers = []
        for preset in deps.llm_manager.list_providers():
            info = deps.llm_manager.get_full_provider_info(preset.id)
            if info:
                providers.append(info)
        return {"providers": providers}

    async def _get_key_store_security_status(self, _auth: bool = Depends(verify_auth)):
        deps = self._deps
        return deps.llm_manager.keys.storage_security_status()

    async def _get_provider_models(self, provider_id: str, refresh: bool = False):
        deps = self._deps
        models = await deps.llm_manager.get_models_for_provider(
            provider_id, force_refresh=refresh
        )
        return {"provider_id": provider_id, "models": [m.model_dump() for m in models]}

    async def _list_provider_keys(self, provider_id: str, _auth: bool = Depends(verify_auth)):
        deps = self._deps
        return {"keys": deps.llm_manager.keys.list_keys_masked(provider_id)}

    async def _add_provider_key(self, request: Request, provider_id: str, _auth: bool = Depends(verify_auth)):
        body = await request.json()
        key = body.get("key", "")
        label = body.get("label", "default")
        set_active = body.get("set_active", True)
        if not key:
            raise HTTPException(status_code=400, detail="API key is required")
        return await self._execute_action('provider_add_key', {
            'provider_id': provider_id, 'key': key, 'label': label, 'set_active': set_active,
        })

    async def _remove_provider_key(self, provider_id: str, key_id: str, _auth: bool = Depends(verify_auth)):
        return await self._execute_action('provider_remove_key', {
            'provider_id': provider_id, 'key_id': key_id,
        })

    async def _activate_provider_key(self, provider_id: str, key_id: str, _auth: bool = Depends(verify_auth)):
        return await self._execute_action('provider_activate_key', {
            'provider_id': provider_id, 'key_id': key_id,
        })

    async def _activate_provider(self, provider_id: str, _auth: bool = Depends(verify_auth)):
        return await self._execute_action('provider_activate', {'provider_id': provider_id})

    async def _check_provider_health(self, provider_id: str):
        deps = self._deps
        preset = deps.llm_manager.registry.get_preset(provider_id)
        if not preset:
            raise HTTPException(status_code=404, detail="Provider not found")
        status = await deps.llm_manager.catalog.check_health(provider_id, preset)
        return status.model_dump()
