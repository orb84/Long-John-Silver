"""
Category item router for LJS.

Exposes category-generic item CRUD and category-declared item actions.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder

from src.core.models import ActionCommand, ActionSource
from src.core.categories.identity import clean_display_title
from src.web.dependencies import WebDependencies, verify_auth


class CategoryItemsRouter:
    """Class-based router for category item APIs."""

    def __init__(self, deps: WebDependencies) -> None:
        """Initialize with shared web dependencies."""
        self._deps = deps

    def get_router(self) -> APIRouter:
        """Build the category item router."""
        router = APIRouter()
        router.add_api_route("/api/categories/{category_id}/items", self._list_items, methods=["GET"])
        router.add_api_route("/api/categories/{category_id}/items", self._add_item, methods=["POST"])
        router.add_api_route("/api/categories/{category_id}/items/{item_id}", self._get_item, methods=["GET"])
        router.add_api_route("/api/categories/{category_id}/items/{item_id}", self._update_item, methods=["PATCH"])
        router.add_api_route("/api/categories/{category_id}/items/{item_id}", self._delete_item, methods=["DELETE"])
        router.add_api_route("/api/categories/{category_id}/items/{item_id}/pause", self._pause_item, methods=["POST"])
        router.add_api_route("/api/categories/{category_id}/items/{item_id}/resume", self._resume_item, methods=["POST"])
        router.add_api_route("/api/categories/{category_id}/items/{item_id}/actions/{action_name}", self._execute_item_action, methods=["POST"])
        return router

    async def _list_items(self, category_id: str, _auth: bool = Depends(verify_auth)) -> dict[str, Any]:
        """List tracked items for one category, enriched with display metadata."""
        category = self._require_category(category_id)
        overview = await self._category_list_overview(category_id)
        repo_items = await self._repo_items(category_id)
        if repo_items is not None:
            items = [self._enrich_list_item(category, category_id, item, overview) for item in repo_items]
            return {"category_id": category_id, "items": jsonable_encoder(items)}
        settings = self._deps.settings_manager.settings
        items = [
            item for item in getattr(settings, "tracked_items", [])
            if getattr(item, "item_type", category_id) == category_id
        ]
        public_items = [self._enrich_list_item(category, category_id, item, overview) for item in items]
        return {"category_id": category_id, "items": jsonable_encoder(public_items)}


    def _enrich_list_item(self, category: Any, category_id: str, item: Any, overview: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return a fast list-card payload.

        The tracked-items grid must be a cheap overview.  Round 177/178 made the
        endpoint build full canonical objects and cache artwork for every row; on
        large TV libraries that meant dozens of unit/metadata queries plus
        possible poster downloads before the library tab painted.  Keep the
        category boundary intact by exposing only cached item/metadata fields
        here, and reserve expensive canonical/detail work for
        ``GET /api/categories/{category_id}/items/{item_id}``.
        """
        if hasattr(item, "model_dump"):
            payload = item.model_dump(mode="json")
        elif isinstance(item, dict):
            payload = dict(item)
        else:
            payload = {"key": str(item), "item_id": str(item), "display_name": str(item)}

        item_id = str(payload.get("item_id") or payload.get("key") or payload.get("name") or payload.get("display_name") or "")
        display_name = clean_display_title(str(payload.get("display_name") or payload.get("title") or payload.get("name") or item_id))
        payload.setdefault("item_id", item_id)
        payload.setdefault("key", item_id)
        payload["display_name"] = display_name or item_id
        payload.setdefault("category_id", category_id)

        overview = overview or {}
        counts = (overview.get("counts") or {}).get(item_id) or {}
        if counts:
            downloaded = int(counts.get("downloaded") or 0)
            total = int(counts.get("total") or 0)
            payload["total_units"] = total
            payload["computed"] = {
                "unit_count": total,
                "downloaded_unit_count": downloaded,
                "has_local_files": downloaded > 0,
            }

        metadata = (overview.get("metadata") or {}).get(item_id) or {}
        if metadata:
            # Do not run category artwork caching from the list path.
            # It can perform network/disk work and belongs to the detail
            # modal or background artwork repair jobs, not tab paint.
            payload.setdefault("metadata", metadata)
            if hasattr(category, "merge_display_metadata"):
                category.merge_display_metadata(payload, metadata)
        return payload

    async def _add_item(self, category_id: str, request: Request, _auth: bool = Depends(verify_auth)) -> dict[str, Any]:
        """Add a tracked category item."""
        body = await request.json()
        name = body.get("name") or body.get("item_id") or body.get("key")
        if not name:
            raise HTTPException(status_code=400, detail="Missing item name")
        return await self._execute_action(
            "category_item_add",
            {"category_id": category_id, "name": name, **body},
        )

    async def _get_item(self, category_id: str, item_id: str, _auth: bool = Depends(verify_auth)) -> dict[str, Any]:
        """Get one tracked category item with category-owned detail payload."""
        category = self._require_category(category_id)
        settings = self._deps.settings_manager.settings
        repo_item = await self._repo_item(category_id, item_id)
        resolved_item_id = item_id
        if repo_item is None:
            clean_item_id = clean_display_title(item_id)
            if clean_item_id and clean_item_id != item_id:
                repo_item = await self._repo_item(category_id, clean_item_id)
                resolved_item_id = clean_item_id if repo_item is not None else item_id
        if repo_item is not None:
            detail = await category.build_item_detail_payload(
                item_id=resolved_item_id, item=repo_item, settings=settings, db=getattr(self._deps, "db", None),
                artwork_manager=getattr(self._deps, "artwork_manager", None),
            )
            return {"category_id": category_id, "item": jsonable_encoder(detail)}
        for item in getattr(settings, "tracked_items", []):
            key = getattr(item, "key", None)
            if (key == item_id or key == clean_display_title(item_id)) and getattr(item, "item_type", category_id) == category_id:
                detail = await category.build_item_detail_payload(
                    item_id=key or item_id, item=item, settings=settings, db=getattr(self._deps, "db", None),
                    artwork_manager=getattr(self._deps, "artwork_manager", None),
                )
                return {"category_id": category_id, "item": jsonable_encoder(detail)}
        raise HTTPException(status_code=404, detail="Item not found")

    async def _update_item(self, category_id: str, item_id: str, request: Request, _auth: bool = Depends(verify_auth)) -> dict[str, Any]:
        """Update one tracked category item."""
        body = await request.json()
        return await self._execute_action(
            "category_item_update",
            {"category_id": category_id, "item_id": item_id, **body},
        )

    async def _delete_item(self, category_id: str, item_id: str, _auth: bool = Depends(verify_auth)) -> dict[str, Any]:
        """Remove one tracked category item."""
        return await self._execute_action(
            "category_item_remove",
            {"category_id": category_id, "item_id": item_id},
        )

    async def _pause_item(self, category_id: str, item_id: str, _auth: bool = Depends(verify_auth)) -> dict[str, Any]:
        """Pause one tracked category item."""
        return await self._execute_action(
            "category_item_pause",
            {"category_id": category_id, "item_id": item_id},
        )

    async def _resume_item(self, category_id: str, item_id: str, _auth: bool = Depends(verify_auth)) -> dict[str, Any]:
        """Resume one tracked category item."""
        return await self._execute_action(
            "category_item_resume",
            {"category_id": category_id, "item_id": item_id},
        )

    async def _execute_item_action(
        self,
        category_id: str,
        item_id: str,
        action_name: str,
        request: Request,
        _auth: bool = Depends(verify_auth),
    ) -> dict[str, Any]:
        """Execute a category-declared action against one item."""
        body = await request.json()
        arguments = body.get("arguments") or body
        arguments["item_id"] = item_id
        result = await self._execute_action(
            "category_action_execute",
            {"category_id": category_id, "action_name": action_name, "arguments": arguments},
        )
        return result

    async def _execute_action(self, action_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute an ActionGateway command and return encoded result data."""
        command = ActionCommand(
            name=action_name,
            source=ActionSource.UI,
            arguments=arguments,
        )
        result = await self._deps.action_gateway.execute(command)
        if not result.ok:
            raise HTTPException(status_code=400, detail=result.error or "Action failed")
        return jsonable_encoder(result.data)


    async def _category_list_overview(self, category_id: str) -> dict[str, Any]:
        """Return cheap per-category overview maps for list-card rendering."""
        db = getattr(self._deps, "db", None)
        if not db or not getattr(db, "media", None):
            return {"counts": {}, "metadata": {}}
        counts: dict[str, dict[str, int]] = {}
        metadata_by_item: dict[str, dict[str, Any]] = {}
        try:
            if hasattr(db.media, "list_category_unit_counts"):
                counts = await db.media.list_category_unit_counts(category_id)
        except Exception:
            counts = {}
        try:
            for row in await db.media.get_all_category_metadata(category_id):
                item_id = str(row.get("item_id") or "")
                if item_id and item_id not in metadata_by_item:
                    metadata_by_item[item_id] = row.get("metadata") or {}
        except Exception:
            metadata_by_item = {}
        return {"counts": counts, "metadata": metadata_by_item}

    async def _repo_items(self, category_id: str) -> list[dict[str, Any]] | None:
        """Return repository-backed category items when the database is available."""
        db = getattr(self._deps, "db", None)
        if not db or not getattr(db, "media", None):
            return None
        return await db.media.list_category_items(category_id)

    async def _repo_item(self, category_id: str, item_id: str) -> dict[str, Any] | None:
        """Return one repository-backed category item when the database is available."""
        db = getattr(self._deps, "db", None)
        if not db or not getattr(db, "media", None):
            return None
        return await db.media.get_category_item(category_id, item_id)

    def _require_category(self, category_id: str) -> Any:
        """Return a category or raise 404."""
        if not self._deps.category_registry:
            raise HTTPException(status_code=503, detail="Category registry is unavailable")
        category = self._deps.category_registry.get(category_id)
        if not category:
            raise HTTPException(status_code=404, detail=f"Unknown category: {category_id}")
        return category
