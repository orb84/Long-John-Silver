"""
HTML page router for LJS.

Serves the dashboard pages: index dashboard, library inspector,
settings, and the first-time setup wizard.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from src.integrations.trakt_defaults import is_bundled_trakt_client_id
from src.web.dependencies import WebDependencies, verify_auth


class PagesRouter:
    """Class-based router for HTML page endpoints."""

    def __init__(self, deps: WebDependencies) -> None:
        self._deps = deps

    def get_router(self) -> APIRouter:
        """Build and return an APIRouter with HTML page endpoints."""
        router = APIRouter()
        router.add_api_route("/", self._index, methods=["GET"], response_class=HTMLResponse)
        router.add_api_route("/library", self._library_page, methods=["GET"], response_class=HTMLResponse)
        router.add_api_route("/settings", self._settings_page, methods=["GET"], response_class=HTMLResponse)
        router.add_api_route("/setup", self._setup_page, methods=["GET"], response_class=HTMLResponse)
        return router

    async def _index(self, request: Request, _auth: bool = Depends(verify_auth)):
        deps = self._deps
        settings = deps.settings_manager.settings
        item_progress = await deps.db.media.get_all_item_progress()
        active_downloads = await deps.downloader.get_active_downloads()
        recent_downloads = await deps.downloader.get_recent_downloads(10)
        paused_items = set()
        for item in settings.tracked_items:
            category_id = getattr(item, "category_id", getattr(item, "item_type", "media")) or "media"
            if await deps.db.media.get_category_item_paused(category_id, item.key):
                paused_items.add(item.key)
        grouped_items = {}
        for item in settings.tracked_items:
            d = item.model_dump()
            itype = getattr(item, "item_type", "media")
            d["item_type"] = itype
            if itype not in grouped_items:
                grouped_items[itype] = []
            grouped_items[itype].append(d)
        for itype in grouped_items:
            grouped_items[itype].sort(key=lambda x: x["key"])
        return deps.templates.TemplateResponse(request, "index.html", {
            "request": request,
            "settings": settings,
            "grouped_items": grouped_items,
            "item_progress": item_progress,
            "active_downloads": active_downloads,
            "recent_downloads": recent_downloads,
            "paused_items": paused_items,
        })

    async def _library_page(self, request: Request, _auth: bool = Depends(verify_auth)):
        deps = self._deps
        settings = deps.settings_manager.settings
        return deps.templates.TemplateResponse(request, "library.html", {
            "request": request,
            "settings": settings,
        })

    async def _settings_page(self, request: Request, _auth: bool = Depends(verify_auth)):
        deps = self._deps
        settings = deps.settings_manager.settings
        return deps.templates.TemplateResponse(request, "settings.html", {
            "request": request,
            "settings": settings,
            "categories": self._template_categories(),
            "trakt_custom_client_id": self._custom_trakt_client_id(settings),
        })

    async def _setup_page(self, request: Request):
        deps = self._deps
        if deps.settings_manager.settings.setup_complete:
            return RedirectResponse(url="/", status_code=302)
        presets = deps.llm_manager.list_providers()
        settings = deps.settings_manager.settings
        return deps.templates.TemplateResponse(request, "setup.html", {
            "request": request,
            "presets": presets,
            "settings": settings,
            "categories": self._template_categories(),
            "trakt_custom_client_id": self._custom_trakt_client_id(settings),
            "default_provider": settings.llm.active_provider,
            "default_model": settings.llm.model,
            "default_api_base": settings.llm.api_base or "",
        })

    def _template_categories(self) -> list[dict]:
        """Return registered categories as template-friendly dictionaries."""
        registry = self._deps.category_registry
        if registry is None:
            return []
        settings = self._deps.settings_manager.settings
        return [
            {
                "id": category.category_id,
                "display_name": category.display_name,
                "default_folder": category.default_folder,
                "default_library_path": category.default_root_path(settings) if hasattr(category, "default_root_path") else "",
                "effective_library_path": category.get_root_path(settings),
                "properties": [p.model_dump() for p in category.get_properties(settings)],
                "setup_requirements": [r.model_dump() for r in category.setup_requirements(settings)],
            }
            for category in registry.list_all()
        ]

    @staticmethod
    def _custom_trakt_client_id(settings) -> str:
        """Return only a user-supplied/custom Trakt app ID for template fields.

        The bundled public Client ID is intentionally not echoed into custom-app
        inputs.  Blank UI values mean "use the bundled LJS Trakt app".
        """
        configured = settings.category_service_value("media", "trakt", "client_id") if hasattr(settings, "category_service_value") else None
        configured_text = str(configured or "").strip()
        if not configured_text or is_bundled_trakt_client_id(configured_text):
            return ""
        return configured_text
