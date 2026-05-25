"""Web router for seed-in-place library sharing status."""

from fastapi import APIRouter, Depends

from src.core.library_sharing import LibrarySharingService
from src.web.dependencies import WebDependencies, verify_auth


class SharingRouter:
    """Class-based router exposing library sharing read endpoints."""

    def __init__(self, deps: WebDependencies) -> None:
        """Create the router with shared web dependencies."""
        self._deps = deps
        self._service = LibrarySharingService(deps.downloader, deps.settings_manager)

    def get_router(self) -> APIRouter:
        """Build and return an APIRouter with sharing endpoints."""
        router = APIRouter()
        router.add_api_route("/api/sharing/library", self._get_library_shares, methods=["GET"])
        return router

    async def _get_library_shares(self, _auth: bool = Depends(verify_auth)) -> dict:
        """Return current seed-in-place library sharing state."""
        return await self._service.snapshot()
