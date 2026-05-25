
"""
Categories router for LJS.

Exposes category manifests, UI schemas, and category-declared actions so the
frontend can render categories without hard-coding Movies/TV behavior.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from src.core.models import CategorySpec
from src.web.dependencies import WebDependencies, verify_auth


class CategoriesRouter:
    """Class-based router for category manifests and actions."""

    def __init__(self, deps: WebDependencies) -> None:
        self._deps = deps

    def get_router(self) -> APIRouter:
        """Build the category router."""
        router = APIRouter()
        router.add_api_route("/api/categories", self._list_categories, methods=["GET"])
        router.add_api_route("/api/categories/scaffold/skill", self._scaffold_skill, methods=["GET"])
        router.add_api_route("/api/categories/scaffold/preview", self._scaffold_preview, methods=["POST"])
        router.add_api_route("/api/categories/scaffold/apply", self._scaffold_apply, methods=["POST"])
        router.add_api_route("/api/categories/{category_id}/manifest", self._get_manifest, methods=["GET"])
        router.add_api_route("/api/categories/{category_id}/actions/{action_name}", self._execute_action, methods=["POST"])
        router.add_api_route("/api/categories/{category_id}/workflows/{workflow_name}", self._execute_workflow, methods=["POST"])
        return router

    async def _list_categories(self, _auth: bool = Depends(verify_auth)):
        """Return all category manifests."""
        if not self._deps.category_registry:
            raise HTTPException(status_code=500, detail="Category registry not configured")
        settings = self._deps.settings_manager.settings
        manifests = self._deps.category_registry.manifests(settings=settings)
        return {"categories": [manifest.model_dump() for manifest in manifests]}

    async def _scaffold_skill(self, _auth: bool = Depends(verify_auth)):
        """Return the controlled skill guide for generating categories."""
        from pathlib import Path

        guide = Path("skills/category_creation_guide.md")
        content = guide.read_text(encoding="utf-8") if guide.exists() else ""
        return {"content": content}

    async def _scaffold_preview(self, request: Request, _auth: bool = Depends(verify_auth)):
        """Preview generated category files from a declarative spec."""
        from src.core.categories.scaffold import CategoryScaffoldService

        body = await request.json()
        spec = CategorySpec(**(body.get("spec") or body))
        service = CategoryScaffoldService()
        preview = service.preview(spec)
        payload = preview.model_dump()
        payload["validation_issues"] = service.validate_preview(preview)
        return payload

    async def _scaffold_apply(self, request: Request, _auth: bool = Depends(verify_auth)):
        """Apply a generated category scaffold after explicit approval."""
        from src.core.categories.scaffold import CategoryScaffoldService

        body = await request.json()
        spec = CategorySpec(**(body.get("spec") or body))
        approved = bool(body.get("approved"))
        receipt = CategoryScaffoldService().apply(
            spec,
            approved=approved,
            overwrite_existing=bool(body.get("overwrite_existing")),
        )
        if receipt.status == "success" and self._deps.category_registry:
            self._deps.category_registry.discover_categories()
        status_code = 200 if receipt.status in {"success", "needs_confirmation", "partial"} else 400
        return JSONResponse(status_code=status_code, content=jsonable_encoder(receipt))

    async def _get_manifest(self, category_id: str, _auth: bool = Depends(verify_auth)):
        """Return one category manifest."""
        if not self._deps.category_registry:
            raise HTTPException(status_code=500, detail="Category registry not configured")
        category = self._deps.category_registry.get(category_id)
        if not category:
            raise HTTPException(status_code=404, detail=f"Category '{category_id}' not found")
        return category.manifest(settings=self._deps.settings_manager.settings).model_dump()

    async def _execute_workflow(self, category_id: str, workflow_name: str, request: Request, _auth: bool = Depends(verify_auth)):
        """Execute a category-owned workflow and return an ActionReceipt."""
        if not self._deps.category_registry:
            raise HTTPException(status_code=500, detail="Category registry not configured")
        category = self._deps.category_registry.get(category_id)
        if not category:
            raise HTTPException(status_code=404, detail=f"Category '{category_id}' not found")
        body = await request.json()
        from src.core.categories.base import CategoryWorkflowContext

        context = CategoryWorkflowContext(
            db=self._deps.db,
            pipeline=self._deps.scheduler.get_search_pipeline() if self._deps.scheduler else None,
            aggregator=self._deps.search_aggregator,
            settings=self._deps.settings_manager.settings,
            downloader=self._deps.downloader,
            metadata_enricher=getattr(self._deps, "metadata_enricher", None),
            artwork_manager=getattr(self._deps, "artwork_manager", None),
        )
        receipt = await category.execute_workflow(workflow_name, body or {}, context=context)
        status_code = 200 if receipt.status in {"success", "needs_confirmation", "partial"} else 400
        return JSONResponse(status_code=status_code, content=jsonable_encoder(receipt))

    async def _execute_action(self, category_id: str, action_name: str, request: Request, _auth: bool = Depends(verify_auth)):
        """Execute a category-owned action and return a receipt."""
        if not self._deps.category_registry:
            raise HTTPException(status_code=500, detail="Category registry not configured")
        category = self._deps.category_registry.get(category_id)
        if not category:
            raise HTTPException(status_code=404, detail=f"Category '{category_id}' not found")
        body = await request.json()
        receipt = await category.execute_action(action_name, body or {}, context=self._deps)
        status_code = 200 if receipt.status in {"success", "needs_confirmation", "partial"} else 400
        return JSONResponse(status_code=status_code, content=jsonable_encoder(receipt))
