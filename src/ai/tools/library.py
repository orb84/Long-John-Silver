"""
Library management tools for LJS.

Declarative AgentTool implementations for category-generic library status,
file listing, and Plex integration.
"""

from __future__ import annotations

from typing import Any, Optional, TYPE_CHECKING

from loguru import logger

from src.ai.tools.base import AgentTool
from src.ai.tools.media_enquiry_support import MediaEnquiryService
from src.core.models import ToolExecutionContext
from src.core.models import Intent

if TYPE_CHECKING:
    from src.core.config import SettingsManager
    from src.core.scheduler import MediaScheduler
    from src.core.content_cleanup import ContentCleanup
    from src.integrations.plex import PlexClient
    from src.core.database import Database
    from src.core.categories.registry import CategoryRegistry


class GetLibraryStatusTool:
    """Get a summary of the user's library state."""

    name = "get_library_status"
    description = (
        "Get a summary of the user's media library. "
        "Reports category items being tracked and recent activity."
    )
    intents = {Intent.SEARCH, Intent.DOWNLOAD, Intent.CONFIG, Intent.CHAT}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["settings_manager"]

    def __init__(self, settings_manager: Optional[SettingsManager] = None) -> None:
        self._settings_manager = settings_manager

    def parameters(self) -> dict:
        """Return the public tool parameter schema.

        The schema is consumed by the LLM runtime and should remain
        backward-compatible.  Add optional fields for extensions whenever
        possible, and keep validation rules mirrored in execute().
        """
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Get a summary of the user's library state."""
        logger.info("Tool: Getting library status")
        if not self._settings_manager:
            return {"error": "Settings not available for library status."}
        try:
            settings = self._settings_manager.settings
            item_names = [s.key for s in settings.tracked_items if s.enabled]
            return {
                "tracked_items": item_names,
                "total_tracked": len(item_names),
                "disabled_items": [
                    s.key for s in settings.tracked_items if not s.enabled
                ],
            }
        except Exception as e:
            logger.error(f"Library status tool error: {e}")
            return {"error": str(e)}


class ListLibraryFilesTool:
    """List files in the library for deletion or management."""

    name = "list_library_files"
    description = (
        "List files in the library. "
        "Useful before deletion or to check what's available."
    )
    intents = {Intent.CONFIG, Intent.CHAT}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["content_cleanup"]

    def __init__(self, content_cleanup: Optional[ContentCleanup] = None) -> None:
        self._content_cleanup = content_cleanup

    def parameters(self) -> dict:
        """Return the public tool parameter schema.

        The schema is consumed by the LLM runtime and should remain
        backward-compatible.  Add optional fields for extensions whenever
        possible, and keep validation rules mirrored in execute().
        """
        return {
            "type": "object",
            "properties": {
                "media_type": {
                    "type": "string",
                    "description": "Category ID to list, or all. Default: all.",
                },
                "name_filter": {
                    "type": "string",
                    "description": "Filter by category item name (optional).",
                },
            },
            "required": [],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """List files in the library."""
        media_type = arguments.get("media_type", "all")
        name_filter = arguments.get("name_filter")
        logger.info(
            f"Tool: Listing library files ({media_type}, name_filter={name_filter})"
        )
        if not self._content_cleanup:
            return {"error": "Content cleanup not available."}
        try:
            files = await self._content_cleanup.list_library_files(
                media_type, name_filter=name_filter
            )
            return {"files": files, "total": len(files)}
        except Exception as e:
            logger.error(f"List library files tool error: {e}")
            return {"error": str(e)}


class GetPlexWatchedTool:
    """Get watched items from Plex."""

    name = "get_plex_watched"
    description = (
        "Get a list of watched movies and episodes from Plex. "
        "Requires Plex integration to be configured."
    )
    intents = {Intent.SEARCH, Intent.DOWNLOAD, Intent.CHAT}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["plex_client"]

    def __init__(self, plex_client: Optional[PlexClient] = None) -> None:
        self._plex = plex_client

    def parameters(self) -> dict:
        """Return the public tool parameter schema.

        The schema is consumed by the LLM runtime and should remain
        backward-compatible.  Add optional fields for extensions whenever
        possible, and keep validation rules mirrored in execute().
        """
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Get watched items from Plex."""
        logger.info("Tool: Getting watched items from Plex")
        if not self._plex:
            return {
                "error": "Plex not configured. Set config/categories/media.yaml services.plex.url and services.plex.token."
            }
        try:
            items = await self._plex.get_watched_items()
            if not items:
                return {"message": "No watched items found in Plex."}
            return {
                "watched": [
                    {
                        "title": i.title,
                        "type": i.media_type,
                        "season": i.season,
                        "episode": i.episode,
                        "watched_at": i.watched_at.isoformat()
                        if i.watched_at
                        else None,
                    }
                    for i in items
                ],
                "total": len(items),
            }
        except Exception as e:
            logger.error(f"Plex watched tool error: {e}")
            return {"error": str(e)}


class EnquireAboutMediaTool:
    """Return category-owned status and metadata for a tracked media item.

    This is the only public agent tool for local media state enquiries.  The
    tool accepts a media title and optional category id, then delegates to
    ``MediaEnquiryService`` so TV, movies, and future categories own their
    progress, metadata, and missing-unit semantics through ``category.enquire``.
    Do not add new category-specific read-only status tools; add or improve the
    category implementation instead.
    """

    name = "enquire_about_media"
    description = (
        "Ask the owning media category for detailed local status and metadata "
        "about a tracked item, such as downloaded episodes, missing aired "
        "episodes, language preference, library presence, and cached provider metadata. "
        "Use this generic tool instead of adding category-specific read-only status tools."
    )
    intents = {Intent.SEARCH, Intent.DOWNLOAD, Intent.CONFIG, Intent.CHAT}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["settings_manager", "database"]

    def __init__(
        self,
        settings_manager: Optional[SettingsManager] = None,
        database: Optional[Database] = None,
        category_registry: Optional[CategoryRegistry] = None,
    ) -> None:
        """Create a media enquiry tool.

        Args:
            settings_manager: Runtime settings provider used to read tracked
                items and provider configuration.
            database: Database facade used by categories for cached metadata and
                local download/library state.
            category_registry: Optional registry of installed categories.  When
                omitted, the service creates a default registry lazily.
        """
        self._settings_manager = settings_manager
        self._database = database
        self._category_registry = category_registry

    def parameters(self) -> dict:
        """Return the public tool parameter schema.

        ``item_name`` is required because category enquiry APIs operate on a
        media title.  ``category_id`` is optional; omitting it lets the service
        infer the category from tracked items and then fall back to registry
        text resolution.  Future optional filters should be added here rather
        than creating category-specific LLM tools.
        """
        return {
            "type": "object",
            "properties": {
                "item_name": {
                    "type": "string",
                    "description": "Name/title of the media item to inspect.",
                },
                "name": {
                    "type": "string",
                    "description": "Backward-compatible alias for item_name.",
                },
                "category_id": {
                    "type": "string",
                    "description": "Optional category id such as 'tv' or 'movie'.",
                },
            },
            "required": ["item_name"],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Execute a category-owned media enquiry and return its payload."""
        item_name = str(arguments.get("item_name") or arguments.get("name") or "").strip()
        service = MediaEnquiryService(
            settings_manager=self._settings_manager,
            database=self._database,
            category_registry=self._category_registry,
        )
        return await service.enquire(item_name, str(arguments.get("category_id") or ""))


class SuggestionsListTool:
    """List persisted category suggestions with compact evidence for the agent.

    Suggestion rows are produced by category lifecycle workflows and already
    contain the category-owned rationale for missing, upgrade, or maintenance
    actions.  Exposing this as a normal agent tool keeps the policy/registry
    contract honest: if prompts tell the model to call ``suggestions_list``, the
    tool must actually exist in the registry.
    """

    name = "suggestions_list"
    description = (
        "List pending or filtered category suggestions with compact evidence. "
        "Use this to inspect why the app thinks an item is missing, stale, or actionable."
    )
    intents = {Intent.SEARCH, Intent.DOWNLOAD, Intent.CONFIG, Intent.CHAT}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["database"]

    def __init__(self, database: Optional[Database] = None) -> None:
        self._database = database

    def parameters(self) -> dict:
        """Return the public tool parameter schema."""
        return {
            "type": "object",
            "properties": {
                "category_id": {
                    "type": "string",
                    "description": "Optional category id, such as tv or movie.",
                },
                "item_id": {
                    "type": "string",
                    "description": "Optional tracked item id/key to filter suggestions.",
                },
                "status": {
                    "type": "string",
                    "description": "Suggestion status to list. Defaults to pending.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum suggestions to return, capped at 100.",
                },
            },
            "required": [],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Return compact suggestion evidence from the database."""
        if not self._database:
            return {"error": "Suggestion database is not available."}
        try:
            from src.core.suggestion_support import summarize_suggestion_for_agent

            limit = max(1, min(int(arguments.get("limit") or 50), 100))
            status = str(arguments.get("status") or "pending").strip() or "pending"
            suggestions = await self._database.downloads.get_suggested_actions(
                category_id=arguments.get("category_id") or None,
                item_id=arguments.get("item_id") or None,
                status=status,
            )
            rows = [summarize_suggestion_for_agent(s) for s in suggestions[:limit]]
            return {
                "suggestions": rows,
                "status_filter": status,
                "total_returned": len(rows),
                "total_available": len(suggestions),
                "note": "Suggestions include category-owned explanations/evidence; approve/deny through suggestion actions, not this read-only tool.",
            }
        except Exception as e:
            logger.error(f"Suggestions list tool error: {e}")
            return {"error": str(e)}


class LibraryToolProvider:
    """Provides library management agent tools.

    Aggregates all AgentTool implementations from the library domain
    and returns instantiated instances via get_tools().
    """

    def __init__(
        self,
        settings_manager: Optional[SettingsManager] = None,
        scheduler: Optional[MediaScheduler] = None,
        content_cleanup: Optional[ContentCleanup] = None,
        plex_client: Optional[PlexClient] = None,
        database: Optional[Database] = None,
        category_registry: Optional[CategoryRegistry] = None,
    ) -> None:
        """Initialize with optional dependencies.

        Args:
            settings_manager: SettingsManager instance.
            scheduler: MediaScheduler instance.
            content_cleanup: ContentCleanup instance.
            plex_client: PlexClient instance.
            database: Database instance.
            category_registry: CategoryRegistry instance.
        """
        self._settings_manager = settings_manager
        self._scheduler = scheduler
        self._content_cleanup = content_cleanup
        self._plex_client = plex_client
        self._database = database
        self._category_registry = category_registry

    def get_tools(self) -> list:
        """Return instantiated library tool instances.

        Returns:
            List of AgentTool-compatible tool instances.
        """
        return [
            GetLibraryStatusTool(settings_manager=self._settings_manager),
            ListLibraryFilesTool(content_cleanup=self._content_cleanup),
            GetPlexWatchedTool(plex_client=self._plex_client),
            SuggestionsListTool(database=self._database),
            EnquireAboutMediaTool(
                settings_manager=self._settings_manager,
                database=self._database,
                category_registry=self._category_registry,
            ),
        ]
