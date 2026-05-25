"""
Storage status router for LJS.

Exposes category-aware disk-space information to the frontend and
setup diagnostics. The data is grouped by physical/logical volume so
multiple categories on the same disk are not double-counted.
"""

import asyncio

from fastapi import APIRouter, Depends, Request

from src.core.server_path_browser import ServerPathBrowser
from src.web.dependencies import WebDependencies, verify_auth


class StorageRouter:
    """Class-based router for storage and disk-space endpoints."""

    def __init__(self, deps: WebDependencies) -> None:
        """Initialize with shared web dependencies."""
        self._deps = deps

    def get_router(self) -> APIRouter:
        """Build and return storage endpoints."""
        router = APIRouter()
        router.add_api_route("/api/storage/status", self._status, methods=["GET"])
        router.add_api_route("/api/storage/capacity-check", self._capacity_check, methods=["POST"])
        router.add_api_route("/api/storage/browse", self._browse, methods=["GET"])
        router.add_api_route("/api/storage/mkdir", self._mkdir, methods=["POST"])
        return router

    async def _status(self, category_id: str | None = None, _auth: bool = Depends(verify_auth)) -> dict:
        """Return the current category-aware storage report."""
        monitor = self._deps.storage_monitor
        if not monitor:
            return {"ok": False, "error": "Storage monitor is not configured", "volumes": [], "paths": []}
        report = monitor.build_report()
        data = report.model_dump(mode="json")
        if category_id:
            data["paths"] = [p for p in data.get("paths", []) if p.get("category_id") == category_id]
            data["volumes"] = [
                v for v in data.get("volumes", [])
                if category_id in (v.get("category_ids") or [])
            ]
        return data


    async def _browse(self, path: str | None = None, _auth: bool = Depends(verify_auth)) -> dict:
        """Browse server-side directories for remote setup/settings UIs."""
        browser = ServerPathBrowser(seed_paths=self._path_seed_values())
        return await asyncio.to_thread(browser.browse, path)

    async def _mkdir(self, request: Request, _auth: bool = Depends(verify_auth)) -> dict:
        """Create a server-side directory from the path browser panel."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        browser = ServerPathBrowser(seed_paths=self._path_seed_values())
        return await asyncio.to_thread(
            browser.create_directory,
            body.get("parent"),
            body.get("name", ""),
        )

    def _path_seed_values(self) -> list[str]:
        """Return configured paths that should appear as quick browser roots."""
        settings = self._deps.settings_manager.settings
        seeds: list[str] = []
        if getattr(settings, "download_dir", None):
            seeds.append(str(settings.download_dir))
        for props in getattr(settings, "category_settings", {}).values():
            if isinstance(props, dict) and props.get("library_path"):
                seeds.append(str(props["library_path"]))
        return seeds

    async def _capacity_check(self, request: Request, _auth: bool = Depends(verify_auth)) -> dict:
        """Preflight a planned download against target disk free space."""
        monitor = self._deps.storage_monitor
        if not monitor:
            return {"ok": False, "error": "Storage monitor is not configured"}
        try:
            body = await request.json()
        except Exception:
            body = {}
        estimated_bytes = body.get("estimated_bytes")
        if estimated_bytes is None and body.get("estimated_gb") is not None:
            estimated_bytes = int(float(body["estimated_gb"]) * 1024 ** 3)
        decision = monitor.check_download_capacity(
            category_id=body.get("category_id"),
            estimated_bytes=estimated_bytes,
        )
        return decision.model_dump(mode="json")
