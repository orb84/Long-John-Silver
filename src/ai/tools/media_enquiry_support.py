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

    The service is intentionally thin: it reads runtime settings, then asks
    the evidence-based identity resolver to compare tracked/canonical state and
    category-owned metadata probes before delegating to the winning category's
    ``enquire`` method. Vocabulary hints may rank evidence but never authorize
    discovery. Extensions belong in category hooks, not branches here.
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

    async def enquire(
        self,
        item_name: str,
        category_id: str = "",
        *,
        request_text: str | None = None,
    ) -> dict[str, Any]:
        """Return category-owned status and metadata for one media item.

        Args:
            item_name: Human-visible media title or tracked item key.
            category_id: Optional category hint such as ``tv`` or ``movie``.
                The hint is verified against tracked/canonical state or
                category-owned metadata before it can authorize enquiry.

        Returns:
            A serializable dict produced by the owning category, with stable
            ``category_id`` and ``item_name`` fields added when missing.
        """
        validation_error = self._validate_dependencies(item_name)
        if validation_error:
            return validation_error
        normalized_name = str(item_name or "").strip()
        settings = self._settings_manager.settings
        tracked_category_id = self.infer_category_id(normalized_name, settings)
        from src.core.categories.identity_resolution import CategoryIdentityResolver

        identity = await CategoryIdentityResolver(
            settings_manager=self._settings_manager,
            database=self._database,
            category_registry=self.get_registry(),
        ).resolve(
            normalized_name,
            category_hint=tracked_category_id or str(category_id or "").strip() or None,
            request_text=request_text,
        )
        if not identity.get("resolved"):
            error_code = "category_ambiguous" if identity.get("status") == "ambiguous" else "category_resolution_required"
            return {
                "ok": False,
                "error": identity.get("reason") or f"Could not resolve a category for '{normalized_name}'.",
                "error_code": error_code,
                "item_name": normalized_name,
                "category_resolution": identity,
                "clarification_question": identity.get("clarification_question"),
                "next_actions": [
                    "Ask the user the clarification_question before searching or queueing.",
                    "Do not fall back to the abstract media category.",
                ],
            }
        resolved_category_id = str(identity.get("category_id") or "")
        category = self.get_registry().get(resolved_category_id)
        if not category:
            return {
                "ok": False,
                "error": f"Resolved category '{resolved_category_id}' is not installed.",
                "error_code": "category_resolution_required",
                "category_resolution": identity,
            }
        result = await self._run_category_enquiry(category, normalized_name, resolved_category_id, settings)
        result.setdefault("ok", "error" not in result)
        result["category_resolution"] = identity
        return result

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
        """Resolve only already-authoritative explicit or tracked categories.

        This synchronous compatibility seam deliberately does not use router
        vocabulary. Unknown titles must pass through :meth:`enquire`, whose
        asynchronous identity resolver can verify category-owned metadata or
        return a clarification request.
        """
        registry = self.get_registry()
        explicit = str(category_id or "").strip()
        if explicit:
            category = registry.get(explicit)
            if category:
                return category
        tracked = self.infer_category_id(str(item_name or "").strip(), settings)
        return registry.get(tracked) if tracked else None

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
