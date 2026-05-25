"""
Preference and activity tools for LJS.

Provides declarative AgentTool implementations for reading
user preferences, modifying likes/dislikes, and recent download activity.
"""

from __future__ import annotations

from typing import Any, Optional, TYPE_CHECKING

from loguru import logger

from src.ai.tools.base import AgentTool
from src.core.models import ToolExecutionContext
from src.core.models import Intent

if TYPE_CHECKING:
    from src.core.preferences import PreferenceManager
    from src.core.database import Database
    from src.core.downloader import DownloadManager
    from src.core.taste_profiler import TasteProfiler


class GetPreferencesTool:
    """Return the user's stored likes and dislikes."""

    name = "get_preferences"
    description = "Return the user's stored likes and dislikes preferences."
    intents = {Intent.SEARCH, Intent.DOWNLOAD, Intent.CONFIG, Intent.CHAT}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["preference_manager"]

    def __init__(self, preference_manager: Optional[PreferenceManager] = None) -> None:
        self._preference_manager = preference_manager

    def parameters(self) -> dict:
        """Return the public tool parameter schema.

        The schema is consumed by the LLM runtime and should remain
        backward-compatible.  Add optional fields for extensions whenever
        possible, and keep validation rules mirrored in execute().
        """
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Execute the get_preferences tool."""
        logger.info("Tool: reading preferences")
        if not self._preference_manager:
            return {"error": "Preference manager not available"}
        try:
            summary = await self._preference_manager.get_summary()
            return {
                "preferences": summary if summary else "No preferences stored yet."
            }
        except Exception as e:
            return {"error": str(e)}


class GetRecentActivityTool:
    """Return recent download activity."""

    name = "get_recent_activity"
    description = "Get recent download activity: what was downloaded, when, and status."
    intents = {Intent.SEARCH, Intent.DOWNLOAD, Intent.CONFIG, Intent.CHAT}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["downloader"]

    def __init__(self, database: Optional[Database] = None, downloader: Optional[DownloadManager] = None) -> None:
        self._db = database
        self._downloader = downloader

    def parameters(self) -> dict:
        """Return the public tool parameter schema.

        The schema is consumed by the LLM runtime and should remain
        backward-compatible.  Add optional fields for extensions whenever
        possible, and keep validation rules mirrored in execute().
        """
        return {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of recent items. Default 10.",
                }
            },
            "required": [],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Execute the get_recent_activity tool."""
        logger.info("Tool: reading recent activity")
        limit = arguments.get("limit", 10)
        if not self._downloader:
            return {"error": "Download manager not available"}
        try:
            recent = await self._downloader.get_recent_downloads(limit)
            return {
                "recent": [
                    {
                        "item_name": d.item_name,
                        "status": d.status.value,
                        "reason": d.reason,
                        "quality": "",
                        "created_at": d.created_at.isoformat()
                        if d.created_at
                        else "",
                    }
                    for d in recent
                ],
                "count": len(recent),
            }
        except Exception as e:
            return {"error": str(e)}


class AddPreferenceTool:
    """Add a preference (like or dislike)."""

    name = "add_preference"
    description = (
        "Add a preference (e.g., 'likes Action', 'dislikes Horror'). "
        "Category is 'likes' or 'dislikes'."
    )
    intents = {Intent.CONFIG}
    allow_direct = True
    requires_confirmation = True
    destructive = False
    required_dependencies = ["preference_manager"]

    def __init__(self, preference_manager: Optional[PreferenceManager] = None) -> None:
        self._preference_manager = preference_manager

    def parameters(self) -> dict:
        """Return the public tool parameter schema.

        The schema is consumed by the LLM runtime and should remain
        backward-compatible.  Add optional fields for extensions whenever
        possible, and keep validation rules mirrored in execute().
        """
        return {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Either 'likes' or 'dislikes'.",
                },
                "value": {
                    "type": "string",
                    "description": "The preference value (e.g., 'Action', 'Sci-Fi').",
                },
            },
            "required": ["category", "value"],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Add a preference (like or dislike)."""
        category = arguments["category"]
        value = arguments["value"]
        logger.info(f"Tool: Adding preference {category}: {value}")
        if not self._preference_manager:
            return {
                "error": "Cannot add preference: preference manager not configured."
            }
        try:
            category_lower = category.lower()
            if category_lower == "likes":
                await self._preference_manager.add_like(value)
            elif category_lower == "dislikes":
                await self._preference_manager.add_dislike(value)
            else:
                return {
                    "error": f"Invalid category '{category}'. Use 'likes' or 'dislikes'."
                }
            return {
                "message": f"Added {category_lower}: {value} to your preferences."
            }
        except Exception as e:
            logger.error(f"Add preference tool error: {e}")
            return {"error": str(e)}


class RemovePreferenceTool:
    """Remove a preference (like or dislike)."""

    name = "remove_preference"
    description = "Remove a preference."
    intents = {Intent.CONFIG}
    allow_direct = True
    requires_confirmation = True
    destructive = False
    required_dependencies = ["preference_manager"]

    def __init__(self, preference_manager: Optional[PreferenceManager] = None) -> None:
        self._preference_manager = preference_manager

    def parameters(self) -> dict:
        """Return the public tool parameter schema.

        The schema is consumed by the LLM runtime and should remain
        backward-compatible.  Add optional fields for extensions whenever
        possible, and keep validation rules mirrored in execute().
        """
        return {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Either 'likes' or 'dislikes'.",
                },
                "value": {
                    "type": "string",
                    "description": "The preference to remove.",
                },
            },
            "required": ["category", "value"],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Remove a preference (like or dislike)."""
        category = arguments["category"]
        value = arguments["value"]
        logger.info(f"Tool: Removing preference {category}: {value}")
        if not self._preference_manager:
            return {
                "error": "Cannot remove preference: preference manager not configured."
            }
        try:
            category_lower = category.lower()
            if category_lower == "likes":
                await self._preference_manager.remove_like(value)
            elif category_lower == "dislikes":
                await self._preference_manager.remove_dislike(value)
            else:
                return {
                    "error": f"Invalid category '{category}'. Use 'likes' or 'dislikes'."
                }
            return {
                "message": f"Removed {category_lower}: {value} from your preferences."
            }
        except Exception as e:
            logger.error(f"Remove preference tool error: {e}")
            return {"error": str(e)}


class RecordCategoryTasteSignalTool:
    """Record category-scoped taste evidence discovered during chat or research."""

    name = "record_category_taste_signal"
    description = (
        "Record a category-scoped taste signal for an item discussed with the user, including researched metadata. "
        "Use this when the user mentions, likes, dislikes, rejects, asks about, or compares a category item even if it is not in the library. "
        "The category owns the semantics: for movies use genres/cast/directors; for games use genres/studios/platforms/creators; custom categories may add their own metadata keys."
    )
    intents = {Intent.SEARCH, Intent.CHAT, Intent.CONFIG}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["taste_profiler"]

    def __init__(self, taste_profiler: Optional[TasteProfiler] = None) -> None:
        self._taste_profiler = taste_profiler

    def parameters(self) -> dict:
        """Return the tool schema for category-scoped taste evidence."""
        return {
            "type": "object",
            "properties": {
                "category_id": {
                    "type": "string",
                    "description": "Owning category id, e.g. movie, tv, book, video_game, or a custom category id.",
                },
                "item_id": {
                    "type": "string",
                    "description": "Stable category item key or external id. Use display_name if no better id exists.",
                },
                "display_name": {
                    "type": "string",
                    "description": "Human-readable item name.",
                },
                "signal_type": {
                    "type": "string",
                    "enum": ["mention", "like", "explicit_like", "dislike", "explicit_dislike", "curious", "reject", "watchlist", "favorite", "negative", "downloaded", "library_item", "watched"],
                    "description": "Kind of taste signal inferred from the conversation. Prefer explicit_like/dislike when the user states taste directly; use downloaded/library_item only for implicit interest.",
                },
                "polarity": {
                    "type": "string",
                    "enum": ["positive", "negative", "interest", "engagement", "neutral", "mixed"],
                    "description": "Explicit evidence polarity. Do not mark downloads/library presence as positive taste; use interest unless the user says they liked it.",
                },
                "strength": {
                    "type": "number",
                    "description": "0.0-1.0 signal strength before confidence. Explicit love/hate is high; download/library is weak.",
                },
                "confidence": {
                    "type": "number",
                    "description": "0.0-1.0 confidence. Use lower values for weak/implicit signals.",
                },
                "weight": {
                    "type": "number",
                    "description": "Optional explicit weight. Omit to use defaults per signal_type.",
                },
                "metadata": {
                    "type": "object",
                    "description": "Category-owned researched metadata. Common useful keys: genres, overview, rating, cast_names, directors, writers, creators, studios, platforms, mechanics, moods, themes, release_year, external_id, provider.",
                },
                "interpreted_facets": {
                    "type": "object",
                    "description": "LLM-extracted reasons/facets from the user's wording. Use liked_aspects/disliked_aspects/do_not_infer/dimensions to avoid dumb genre scoreboards.",
                },
                "evidence_text": {
                    "type": "string",
                    "description": "Short quote or paraphrase of the user evidence that caused this signal.",
                },
                "notes": {
                    "type": "string",
                    "description": "Brief evidence note, e.g. user said they loved the pacing but disliked the ending.",
                },
            },
            "required": ["category_id", "item_id", "display_name", "signal_type"],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Persist category-scoped taste evidence through the TasteProfiler."""
        if not self._taste_profiler:
            return {"error": "Cannot record taste signal: taste profiler not configured."}
        try:
            signal_id = await self._taste_profiler.record_taste_signal(
                category_id=str(arguments.get("category_id") or "").strip(),
                item_id=str(arguments.get("item_id") or "").strip(),
                display_name=str(arguments.get("display_name") or "").strip(),
                signal_type=str(arguments.get("signal_type") or "mention").strip(),
                metadata=arguments.get("metadata") if isinstance(arguments.get("metadata"), dict) else {},
                user_id=context.user_id,
                confidence=float(arguments.get("confidence", 1.0) or 1.0),
                weight=float(arguments["weight"]) if arguments.get("weight") is not None else None,
                notes=str(arguments.get("notes") or "").strip(),
                polarity=str(arguments.get("polarity") or "").strip() or None,
                strength=float(arguments["strength"]) if arguments.get("strength") is not None else None,
                interpreted_facets=arguments.get("interpreted_facets") if isinstance(arguments.get("interpreted_facets"), dict) else {},
                evidence_text=str(arguments.get("evidence_text") or "").strip(),
            )
            return {
                "status": "ok",
                "signal_id": signal_id,
                "message": "Recorded category-scoped taste evidence for future recommendations and context.",
            }
        except Exception as exc:
            logger.error(f"Record category taste signal tool error: {exc}")
            return {"error": str(exc)}


class PreferencesToolProvider:
    """Provides preference management agent tools.

    Aggregates all AgentTool implementations from the preferences domain
    and returns instantiated instances via get_tools().
    """

    def __init__(
        self,
        preference_manager: Optional[PreferenceManager] = None,
        database: Optional[Database] = None,
        downloader: Optional[DownloadManager] = None,
        taste_profiler: Optional[TasteProfiler] = None,
    ) -> None:
        """Initialize with optional dependencies.

        Args:
            preference_manager: PreferenceManager instance.
            database: Database instance.
            downloader: DownloadManager instance.
            taste_profiler: TasteProfiler instance for category-scoped taste evidence.
        """
        self._preference_manager = preference_manager
        self._database = database
        self._downloader = downloader
        self._taste_profiler = taste_profiler

    def get_tools(self) -> list:
        """Return instantiated preference tool instances.

        Returns:
            List of AgentTool-compatible tool instances.
        """
        return [
            GetPreferencesTool(preference_manager=self._preference_manager),
            GetRecentActivityTool(database=self._database, downloader=self._downloader),
            AddPreferenceTool(preference_manager=self._preference_manager),
            RemovePreferenceTool(preference_manager=self._preference_manager),
            RecordCategoryTasteSignalTool(taste_profiler=self._taste_profiler),
        ]
