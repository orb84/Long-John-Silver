"""Category-agnostic media enquiry services for agent tools.

This module contains the shared service used by the public
``enquire_about_media`` tool and by temporary compatibility adapters.  Keeping
category resolution here prevents legacy aliases from re-implementing TV or
movie logic and makes new categories participate by implementing their own
``MediaCategory.enquire`` method.
"""

from __future__ import annotations

from typing import Any

from loguru import logger


class MediaEnquiryService:
    """Resolve a media item to its owning category and run category enquiry.

    The service is intentionally thin: it reads runtime settings, resolves a
    category through explicit ``category_id``, tracked-item metadata, or the
    category registry's text resolver, then delegates to the category's
    ``enquire`` method.  Extensions should add category behavior to the
    category implementation rather than adding branches to this service.
    """

    def __init__(self, settings_manager: Any, database: Any, category_registry: Any | None = None) -> None:
        """Create a service with runtime settings, database, and registry access.

        Args:
            settings_manager: Object exposing a ``settings`` attribute.
            database: Database facade passed through to category enquiry.
            category_registry: Optional category registry.  A default registry
                is created lazily when omitted so tests and simple adapters do
                not need the full application composition root.
        """
        self._settings_manager = settings_manager
        self._database = database
        self._category_registry = category_registry

    async def enquire(self, item_name: str, category_id: str = "") -> dict[str, Any]:
        """Return category-owned status and metadata for one media item.

        Args:
            item_name: Human-visible media title or tracked item key.
            category_id: Optional explicit category id such as ``tv`` or
                ``movie``.  When absent, tracked settings and category registry
                text matching are used.

        Returns:
            A serializable dict produced by the owning category, with stable
            ``category_id`` and ``item_name`` fields added when missing.
        """
        validation_error = self._validate_dependencies(item_name)
        if validation_error:
            return validation_error
        normalized_name = str(item_name or "").strip()
        settings = self._settings_manager.settings
        resolved_category_id = str(category_id or "").strip() or self.infer_category_id(normalized_name, settings)
        category = self.resolve_category(normalized_name, resolved_category_id, settings)
        if not category:
            return {"error": f"Could not resolve a category for '{normalized_name}'."}
        return await self._run_category_enquiry(category, normalized_name, resolved_category_id, settings)

    def _validate_dependencies(self, item_name: str) -> dict[str, Any] | None:
        """Return a user-safe error when required enquiry inputs are missing."""
        if not self._settings_manager:
            return {"error": "Settings manager not available for media enquiry."}
        if not self._database:
            return {"error": "Database not available for media enquiry."}
        if not str(item_name or "").strip():
            return {"error": "item_name is required."}
        return None

    async def _run_category_enquiry(
        self, category: Any, item_name: str, category_id: str, settings: Any
    ) -> dict[str, Any]:
        """Execute category enquiry and normalize its result envelope."""
        try:
            result = await category.enquire(item_name, settings, self._database)
            if not isinstance(result, dict):
                result = {"result": result}
            result.setdefault("category_id", getattr(category, "category_id", category_id))
            result.setdefault("item_name", item_name)
            return result
        except Exception as exc:
            logger.error(f"Media enquiry service error for {item_name}: {exc}")
            return {
                "error": str(exc),
                "category_id": getattr(category, "category_id", category_id),
                "item_name": item_name,
            }

    def infer_category_id(self, item_name: str, settings: Any) -> str:
        """Infer a category id from tracked-item settings.

        Exact tracked item key matches are preferred over text classification so
        user-defined category choices remain stable.  Unknown items return an
        empty string and are resolved by the registry.
        """
        wanted = item_name.lower()
        for item in getattr(settings, "tracked_items", []):
            if str(getattr(item, "key", "")).lower() == wanted:
                return str(getattr(item, "item_type", "") or "")
        return ""

    def resolve_category(self, item_name: str, category_id: str, settings: Any) -> Any | None:
        """Resolve the most appropriate category for an enquiry request.

        Args:
            item_name: Media title to resolve.
            category_id: Optional explicit category id.
            settings: Runtime settings whose tracked items can guide fallback
                resolution.
        """
        registry = self.get_registry()
        if category_id:
            category = registry.get(category_id)
            if category:
                return category
        return registry.resolve_from_text(item_name, getattr(settings, "tracked_items", []))

    def get_registry(self) -> Any:
        """Return a category registry, creating the default registry lazily."""
        if self._category_registry:
            return self._category_registry
        from src.core.categories.registry import CategoryRegistry

        registry = CategoryRegistry()
        registry.register_defaults()
        self._category_registry = registry
        return registry


class SupportToolProvider:
    """Sentinel provider for tool-support modules.

    The import-smoke tests require every module under ``src.ai.tools`` to expose
    a ``get_tools`` provider class.  This support module supplies reusable
    services rather than public tools, so the provider intentionally returns an
    empty list.
    """

    def get_tools(self) -> list[Any]:
        """Return no tools because this module only provides support services."""
        return []
