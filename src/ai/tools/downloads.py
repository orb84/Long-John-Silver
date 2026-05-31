"""
Download visibility and management tools for LJS.

Provides declarative AgentTool implementations for listing,
queuing, prioritizing, and pausing/resuming downloads.
"""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from loguru import logger

from src.ai.tools.download_control import ManageDownloadsTool
from src.ai.tools.download_list_support import DownloadListReportService
from src.ai.tools.queue_download_support import QueueDownloadService
from src.ai.tools.torrent_search_support import TorrentSearchToolService
from src.ai.tools.soulseek import SoulseekToolProvider
from src.core.library_sharing import LibrarySharingService
from src.core.models import ToolExecutionContext
from src.core.models import Intent

if TYPE_CHECKING:
    from src.core.downloader import DownloadManager
    from src.core.scheduler import MediaScheduler
    from src.core.database import Database
    from src.core.config import SettingsManager
    from src.search.aggregator import SearchAggregator


class ListDownloadsTool:
    """List all active and queued downloads with status, priority, and progress."""

    name = "list_downloads"
    description = "List all active and queued downloads with their status, priority, progress, and ETA."
    intents = {Intent.SEARCH, Intent.DOWNLOAD, Intent.CONFIG, Intent.CHAT}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["downloader"]

    def __init__(self, downloader: Optional[DownloadManager] = None, settings_manager: Optional[SettingsManager] = None, database: Optional[Database] = None) -> None:
        """Initialize the tool with download manager and optional companion-source read models."""
        self._downloader = downloader
        self._settings_manager = settings_manager
        self._database = database

    def parameters(self) -> dict:
        """Return the public tool parameter schema.

        The schema is consumed by the LLM runtime and should remain
        backward-compatible.  Add optional fields for extensions whenever
        possible, and keep validation rules mirrored in execute().
        """
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, arguments: dict, context: ToolExecutionContext) -> object:
        """Execute the list_downloads tool.

        Args:
            arguments: Empty dict (no parameters required).
            context: Execution context reserved for future user-scoped filtering.

        Returns:
            Dict with active items, queued items, and aggregate summary counts.
        """
        logger.info("Tool: listing active downloads")
        if not self._downloader:
            return {"error": "Download manager not available"}
        try:
            return await DownloadListReportService(self._downloader, self._settings_manager, self._database).report()
        except Exception as e:
            return {"error": str(e)}




class ListLibrarySharesTool:
    """Report seed-in-place library sharing status, ratios, and quotas."""

    name = "list_library_shares"
    description = (
        "List torrent-backed library files that are configured for seed-in-place sharing. "
        "Returns the Fair Share policy, dedicated library upload quota, active upload rate, "
        "per-item ratios, peers/seeds, and library payload paths."
    )
    intents = {Intent.SEARCH, Intent.DOWNLOAD, Intent.CONFIG, Intent.CHAT}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["downloader", "settings_manager"]

    def __init__(self, downloader: Optional[DownloadManager] = None, settings_manager: Optional[SettingsManager] = None) -> None:
        """Initialize with the downloader read model and settings manager.

        Args:
            downloader: DownloadManager instance exposing ``get_library_shares``.
            settings_manager: SettingsManager instance exposing Fair Share policy.
        """
        self._downloader = downloader
        self._settings_manager = settings_manager

    def parameters(self) -> dict:
        """Return the public tool parameter schema."""
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, arguments: dict, context: ToolExecutionContext) -> object:
        """Execute the list_library_shares read-only tool.

        Args:
            arguments: Empty dict; reserved for future filters.
            context: Execution context reserved for future user scoping.

        Returns:
            Sharing policy summary and per-item seed-in-place rows.
        """
        if not self._downloader or not self._settings_manager:
            return {"error": "Library sharing service not available"}
        return await LibrarySharingService(self._downloader, self._settings_manager).snapshot()


class QueueDownloadTool:
    """Queue a magnet link for download with optional category unit metadata."""

    name = "queue_download"
    description = (
        "Queue a magnet link for download. Prefer candidate_id/result_set_id from search results so "
        "category-owned unit descriptors are preserved. Legacy season/episode arguments remain for "
        "categories that explicitly accept them. "
        "Pick the best candidate (right language, strong seeder availability, reasonable size). "
        "When candidates are otherwise equivalent, higher seeders should win."
    )
    intents = {Intent.DOWNLOAD}
    allow_direct = True
    requires_confirmation = True
    destructive = False
    required_dependencies = ["scheduler"]

    def __init__(self, scheduler: Optional[MediaScheduler] = None, database: Optional[Database] = None) -> None:
        """Initialize the tool with a media scheduler and database.

        Args:
            scheduler: MediaScheduler instance for queueing downloads.
            database: Database instance.
        """
        self._scheduler = scheduler
        self._database = database

    def parameters(self) -> dict:
        """Return the public tool parameter schema.

        The schema is consumed by the LLM runtime and should remain
        backward-compatible.  Add optional fields for extensions whenever
        possible, and keep validation rules mirrored in execute().
        """
        return {
            "type": "object",
            "properties": {
                "candidate_id": {
                    "type": "string",
                    "description": "Stable candidate_id returned by search_media_torrents/search_torrents. Prefer this over option_index."
                },
                "candidate_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Stable candidate IDs to queue as a batch. Use this when search_media_torrents returns a batch_recommendation for all/remaining/missing episodes or multiple requested units."
                },
                "result_set_id": {
                    "type": "string",
                    "description": "Optional result_set_id returned by the search that displayed this candidate."
                },
                "option_index": {
                    "type": "integer",
                    "description": "Legacy 1-based index from the latest visible search results. Prefer candidate_id when available."
                },
                "magnet": {"type": "string", "description": "Magnet URI (only if queueing a direct magnet link not in search results)."},
                "name": {"type": "string", "description": "Exact name from list_media or search results."},
                "season": {"type": "integer", "description": "Legacy first unit coordinate for categories that accept it. Prefer candidate_id/result_set_id."},
                "episode": {"type": "integer", "description": "Legacy second unit coordinate for categories that accept it. Prefer candidate_id/result_set_id."},
                "unit_descriptor": {"type": "object", "description": "Category-owned unit descriptor from search results; normally supplied automatically via candidate_id."},
                "priority": {"type": "string", "description": "high, normal, or low. Default: normal."},
                "category_id": {"type": "string", "description": "Optional target category ID, such as tv or movie, for storage preflight."},
                "estimated_size_bytes": {"type": "integer", "description": "Optional estimated torrent size in bytes for storage preflight."},
                "provider": {"type": "string", "description": "Optional stable metadata provider for direct magnets, e.g. tmdb/tvdb/tvmaze/imdb."},
                "provider_id": {"type": "string", "description": "Optional provider item ID for direct magnets. Prefer this over title-only identity."},
                "provider_media_type": {"type": "string", "description": "Optional provider media type such as tv or movie."},
                "tmdb_id": {"type": "integer", "description": "Known TMDB ID, if available."},
                "tvdb_id": {"type": "integer", "description": "Known TheTVDB ID, if available."},
                "tvmaze_id": {"type": "integer", "description": "Known TVmaze ID, if available."},
                "imdb_id": {"type": "string", "description": "Known IMDb ID, if available."},
                "year": {"type": "integer", "description": "Release/start year used only as a fallback disambiguator."},
                "series_start_year": {"type": "integer", "description": "Series start year used to disambiguate same-title shows and folder names."},
                "release_year": {"type": "integer", "description": "Movie/release year used as a fallback when series_start_year is not applicable."},
                "item_id": {"type": "string", "description": "Optional category-local tracked item ID. Provider IDs should still be supplied separately when known."},
                "season_order_type": {"type": "string", "description": "Episode-order namespace, e.g. official, aired, dvd, absolute. Default: official."},
                "confirmed": {"type": "boolean", "description": "Set true only after the user explicitly confirms queueing a candidate that search_media_torrents marked as blocked/low-confidence."},
            },
            "required": [],
        }

    async def execute(self, arguments: dict, context: ToolExecutionContext) -> object:
        """Execute the queue_download tool through the queueing service.

        Args:
            arguments: Dict with candidate_id(s), option_index, or direct magnet data.
            context: Execution context used for result-cache session lookup.

        Returns:
            Dict with status/download IDs or a user-safe error message.
        """
        logger.info(f"Tool: queueing download for '{arguments.get('name')}'")
        if not self._scheduler:
            return {"error": "Scheduler not available"}
        return await QueueDownloadService(self._scheduler, self._database).queue(arguments, context)



class InspectTorrentCandidateTool:
    """Inspect a cached torrent candidate by result_set_id/candidate_id.

    The LLM receives compact candidate workspaces first.  When bundle coverage,
    file selection, or exact quality is ambiguous, it can call this tool to ask
    for the richer cached candidate record without replaying every raw tracker
    result into the conversation.
    """

    name = "inspect_torrent_candidate"
    description = (
        "Inspect one cached torrent candidate from a previous search result set. "
        "Use this before queueing a season/full-series bundle when you need more "
        "details about title, size, language, bundle hints, unit descriptor, or "
        "whether file-list inspection is available."
    )
    intents = {Intent.DOWNLOAD, Intent.SEARCH}
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
                "candidate_id": {"type": "string", "description": "Candidate ID from candidate_picker/search results."},
                "result_set_id": {"type": "string", "description": "Optional result_set_id containing the candidate."},
                "detail": {
                    "type": "string",
                    "enum": ["summary", "full_record", "file_list"],
                    "description": "Which detail to inspect. file_list returns cached file-list data when available and otherwise explains the limitation.",
                },
            },
            "required": ["candidate_id"],
        }

    async def execute(self, arguments: dict, context: ToolExecutionContext) -> object:
        """Return cached candidate details for agent follow-up decisions."""
        if not self._database:
            return {"ok": False, "error_code": "DATABASE_UNAVAILABLE", "error": "Candidate cache is not available."}
        from src.utils.candidate_ids import find_candidate_in_cached_sets

        candidate_id = str(arguments.get("candidate_id") or "").strip()
        result_set_id = str(arguments.get("result_set_id") or "").strip() or None
        detail = str(arguments.get("detail") or "summary").strip().lower()
        cache_data, candidate = await find_candidate_in_cached_sets(
            self._database,
            session_id=context.session_id or "default",
            candidate_id=candidate_id,
            result_set_id=result_set_id,
        )
        if not candidate:
            return {
                "ok": False,
                "error_code": "CANDIDATE_NOT_FOUND",
                "recoverable": True,
                "error": "Candidate ID was not found in recent cached result sets.",
                "next_actions": ["Use a candidate_id from the latest candidate_picker", "Call search_media_torrents again if the result set is stale"],
            }
        public = {
            "candidate_id": candidate.get("candidate_id"),
            "result_set_id": (cache_data or {}).get("result_set_id") or result_set_id,
            "name": (cache_data or {}).get("name") or (cache_data or {}).get("query"),
            "category_id": candidate.get("category_id") or (cache_data or {}).get("category_id"),
            "title": candidate.get("title"),
            "size": candidate.get("size"),
            "size_bytes": candidate.get("size_bytes"),
            "seeders": candidate.get("seeders"),
            "source": candidate.get("source"),
            "languages": candidate.get("languages"),
            "resolution": candidate.get("resolution"),
            "codec": candidate.get("codec"),
            "unit_descriptor": candidate.get("unit_descriptor") or {},
            "bundle_context": candidate.get("bundle_context") or {},
            "is_bundle": candidate.get("is_bundle"),
            "bundle_scope": candidate.get("bundle_scope"),
            "pack_type": candidate.get("pack_type"),
            "bundle_unit_count": candidate.get("bundle_unit_count"),
        }
        if detail == "file_list":
            files = candidate.get("files") or candidate.get("file_list") or []
            if files:
                public["files"] = files[:120] if isinstance(files, list) else files
                public["file_list_status"] = "available_from_cache"
            else:
                public["file_list_status"] = "not_available_from_search_cache"
                public["file_list_note"] = (
                    "The tracker result did not include a file list. If this is a bundle/full-series candidate, "
                    "queue_download can still preserve category-owned selective descriptors; exact file priorities are applied after torrent metadata arrives."
                )
        elif detail == "full_record":
            public["cached_fields"] = {k: v for k, v in candidate.items() if k != "magnet"}
        return {
            "ok": True,
            "detail": detail,
            "candidate": {k: v for k, v in public.items() if v not in (None, "", [], {})},
            "next_actions": [
                "queue_download with candidate_id/result_set_id if this candidate clearly matches the goal",
                "inspect another candidate when bundle coverage or language is ambiguous",
                "search_media_torrents with a narrower scope if no candidate is acceptable",
            ],
        }

class DownloadToolProvider:
    """Provides download-related agent tools.

    Aggregates all AgentTool implementations from the downloads domain
    and returns instantiated instances via get_tools(). Dependencies
    are injected at construction time.
    """

    def __init__(
        self,
        downloader: Optional[DownloadManager] = None,
        scheduler: Optional[MediaScheduler] = None,
        database: Optional[Database] = None,
        search_aggregator: Optional[SearchAggregator] = None,
        settings_manager: Optional[SettingsManager] = None,
    ) -> None:
        """Initialize with optional dependencies.

        Args:
            downloader: DownloadManager instance.
            scheduler: MediaScheduler instance.
            database: Database instance.
            search_aggregator: SearchAggregator instance.
            settings_manager: SettingsManager instance for Fair Share policy.
        """
        self._downloader = downloader
        self._scheduler = scheduler
        self._database = database
        self._search_aggregator = search_aggregator
        self._settings_manager = settings_manager

    def get_tools(self) -> list:
        """Return instantiated download tool instances.

        Returns:
            List of AgentTool-compatible tool instances.
        """
        return [
            ListDownloadsTool(downloader=self._downloader, settings_manager=self._settings_manager, database=self._database),
            ListLibrarySharesTool(downloader=self._downloader, settings_manager=self._settings_manager),
            QueueDownloadTool(scheduler=self._scheduler, database=self._database),
            InspectTorrentCandidateTool(database=self._database),
            SetDownloadPriorityTool(scheduler=self._scheduler),
            ManageDownloadsTool(downloader=self._downloader),
            GetUpgradesTool(database=self._database),
            SearchTorrentsTool(search_aggregator=self._search_aggregator, database=self._database),
            *SoulseekToolProvider(settings_manager=self._settings_manager, database=self._database).get_tools(),
        ]


class SetDownloadPriorityTool:
    """Change priority for queued/paused downloads by media name."""

    name = "set_download_priority"
    description = (
        "Unified priority control for queued/paused downloads. Works for any media type. "
        "Target all downloads, a season (TV), or a specific episode. "
        "Priority: high, normal, low. "
        "Chain: list_media \u2192 set_download_priority(name='...', priority='high', season=5)."
    )
    intents = {Intent.DOWNLOAD}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["scheduler"]

    def __init__(self, scheduler: Optional[MediaScheduler] = None) -> None:
        """Initialize the tool with a media scheduler.

        Args:
            scheduler: MediaScheduler instance for priority changes.
        """
        self._scheduler = scheduler

    def parameters(self) -> dict:
        """Return the public tool parameter schema.

        The schema is consumed by the LLM runtime and should remain
        backward-compatible.  Add optional fields for extensions whenever
        possible, and keep validation rules mirrored in execute().
        """
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Exact name from list_media."},
                "priority": {"type": "string", "description": "high, normal, or low."},
                "season": {"type": "integer", "description": "Optional \u2014 target a TV season."},
                "episode": {"type": "integer", "description": "Optional \u2014 target a specific TV episode."},
            },
            "required": ["name", "priority"],
        }

    async def execute(self, arguments: dict, context: ToolExecutionContext) -> object:
        """Execute the set_download_priority tool.

        Args:
            arguments: Dict with 'name', 'priority', optional 'season', 'episode'.
            context: Execution context (unused by this tool).

        Returns:
            Dict with status and updated_count.
        """
        logger.info(f"Tool: setting priority for '{arguments.get('name')}' to {arguments.get('priority')}")
        if not self._scheduler:
            return {"error": "Scheduler not available"}
        try:
            result = await self._scheduler.set_download_priority(
                name=arguments["name"],
                priority=arguments["priority"],
                season=arguments.get("season"),
                episode=arguments.get("episode"),
            )
            return result
        except Exception as e:
            logger.error(f"Set download priority tool error: {e}")
            return {"error": str(e)}


# Category item pause/resume and episodic detail tools are registered dynamically
# from category manifests instead of this global download provider.


class GetUpgradesTool:
    """List pending quality upgrade candidates."""

    name = "get_upgrades"
    description = (
        "List pending quality upgrade candidates. "
        "Lists what is available at higher resolution/codec "
        "than what you currently have."
    )
    intents = {Intent.SEARCH, Intent.DOWNLOAD, Intent.CONFIG, Intent.CHAT}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["database"]

    def __init__(self, database: Optional[Database] = None) -> None:
        self._db = database

    def parameters(self) -> dict:
        """Return the public tool parameter schema.

        The schema is consumed by the LLM runtime and should remain
        backward-compatible.  Add optional fields for extensions whenever
        possible, and keep validation rules mirrored in execute().
        """
        return {
            "type": "object",
            "properties": {
                "item_id": {
                    "type": "string",
                    "description": "Optional: filter to a specific category item id.",
                },
            },
            "required": [],
        }

    async def execute(self, arguments: dict, context: ToolExecutionContext) -> object:
        """List pending quality upgrade candidates."""
        item_id = arguments.get("item_id")
        logger.info(f"Tool: listing upgrades for '{item_id or 'all'}'")
        if not self._db:
            return {"error": "Database not available"}
        candidates = await self._db.downloads.get_upgrade_candidates(
            item_id=item_id, status="pending"
        )
        return {
            "upgrades": [
                {
                    "id": c.id,
                    "item_name": c.item_name,
                    "current": c.current_resolution,
                    "upgrade": c.best_upgrade_resolution,
                    "improvement": c.quality_improvement,
                    "title": c.best_upgrade_title,
                }
                for c in candidates
            ],
            "count": len(candidates),
        }


class SearchTorrentsTool:
    """Search for torrent downloads for a given query.

    Calls the search aggregator directly and enriches results with
    quality tags (language, resolution, codec). No dependency on
    AIAssistant — fully self-contained.
    """

    name = "search_torrents"
    description = (
        "Raw fallback torrent search for non-library/general downloads. For tracked media, TV episodes, movies, "
        "or replacement searches, prefer search_media_torrents so category language, unit, size, Soulseek companion, "
        "and pack-selection rules are applied. Returns filtered results with magnet links, language tags, resolution, "
        "and codec info."
    )
    intents = {Intent.DOWNLOAD}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["search_aggregator"]

    def __init__(self, search_aggregator: Optional[SearchAggregator] = None, database: Optional[Database] = None) -> None:
        self._search_aggregator = search_aggregator
        self._database = database

    def parameters(self) -> dict:
        """Return the public tool parameter schema.

        The schema is consumed by the LLM runtime and should remain
        backward-compatible.  Add optional fields for extensions whenever
        possible, and keep validation rules mirrored in execute().
        """
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Raw query for fallback torrent searches. Do not use this for tracked/category media or replacement searches; call search_media_torrents instead. Do not append language words for category media merely to express configured audio preferences.",
                },
            },
            "required": ["query"],
        }

    async def execute(self, arguments: dict, context: ToolExecutionContext) -> object:
        """Search for torrent downloads using the search aggregator service."""
        if not self._search_aggregator:
            return {"error": "Search aggregator not configured."}
        return await TorrentSearchToolService(self._search_aggregator, self._database).search(arguments, context)
