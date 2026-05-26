"""
Library router for LJS.

Provides endpoints for library scanning, inspection, category item config,
and library consolidation. All mutation endpoints delegate to
ActionGateway for unified audit and event emission.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from src.core.models import ActionCommand, ActionSource
from src.web.dependencies import WebDependencies, verify_auth
from src.web.view_models.library_view_model import LibraryInspectionBuilder


class LibraryRouter:
    """Class-based router for library management endpoints."""

    def __init__(self, deps: WebDependencies) -> None:
        self._deps = deps
        self._inspection_builder = LibraryInspectionBuilder(deps.settings_manager, deps.db, deps.downloader, deps.category_registry)

    def get_router(self) -> APIRouter:
        """Build and return an APIRouter with library management endpoints."""
        router = APIRouter()
        router.add_api_route("/api/library/scan", self._scan_library, methods=["POST"])
        router.add_api_route("/api/library/status", self._get_library_status, methods=["GET"])
        router.add_api_route("/api/library/inspect", self._inspect_library, methods=["GET"])
        router.add_api_route("/api/library/categories/{category_id}/items/{item_id}/config", self._update_category_item_config, methods=["POST"])
        router.add_api_route("/api/library/consolidate", self._consolidate_library_endpoint, methods=["POST"])
        return router

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

    async def _scan_library(self, request: Request, _auth: bool = Depends(verify_auth)):
        deps = self._deps
        data = await self._execute_action('library_scan', {})

        if "application/json" in (request.headers.get("accept") or ""):
            return data
        return RedirectResponse(url="/", status_code=303)

    async def _get_library_status(self):
        deps = self._deps
        result = deps.scheduler.get_last_scan_result()
        scan_status = deps.scheduler.get_library_scan_status() if hasattr(deps.scheduler, "get_library_scan_status") else {"state": "unknown", "scan_in_progress": False}
        payload = {"scan": scan_status, "items": []}
        if not result:
            return payload
        payload.update({
            "items": [item.model_dump() for item in result.items],
            "items_found": len(result.items),
            "total_files": result.total_files,
        })
        return payload

    async def _inspect_library(self):
        return await self._inspection_builder.build()

    async def _update_category_item_config(
        self,
        category_id: str,
        item_id: str,
        body: dict,
        _auth: bool = Depends(verify_auth),
    ):
        """Update configurable fields on one category item."""
        args = {"category_id": category_id, "item_id": item_id}
        for field_name in ("language", "subtitle_languages", "quality", "auto_download"):
            if field_name in body:
                args[field_name] = body[field_name]
        data = await self._execute_action('library_update_category_item_config', args)
        if not data.get('found'):
            raise HTTPException(status_code=404, detail="Category item not found")
        return {"status": "ok", "category_id": category_id, "item_id": item_id}

    async def _consolidate_library_endpoint(self, request: Request, _auth: bool = Depends(verify_auth)):
        body = await request.json()
        dry_run = body.get("dry_run", True)
        data = await self._execute_action('library_consolidate', {'dry_run': dry_run})
        return data
