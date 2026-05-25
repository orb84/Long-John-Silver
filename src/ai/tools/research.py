"""
Research tools for LJS.

This provider exposes category-neutral research helpers and a small generic
metadata lookup tool. Category subclasses still own category actions and
workflows; this module only gives the LLM access to external metadata services
(TMDB/TVMaze/IMDb fallback) so it can answer open-ended factual media questions
without requiring one bespoke tool per fact.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any, Optional, TYPE_CHECKING

from loguru import logger

from src.core.models import Intent, ToolExecutionContext
from src.ai.tools.metadata_lookup_support import (
    LibraryMetadataSnapshotLookup,
    MetadataClientResolver,
    MetadataLookupRequest,
    MetadataResultNormalizer,
)

if TYPE_CHECKING:
    from src.core.config import SettingsManager
    from src.integrations.tmdb import TMDBClient
    from src.integrations.tvmaze import TVMazeClient

try:
    from imdb import Cinemagoer
except ImportError:  # pragma: no cover - optional dependency
    Cinemagoer = None


def _resolve_title(arguments: dict[str, Any]) -> str | None:
    """Resolve a media title from common category-neutral argument names."""
    for key in ("title", "query", "name", "item_name", "movie_name", "series_title", "q", "search"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for value in arguments.values():
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _safe_int(value: Any) -> int | None:
    """Convert common numeric inputs to int, returning None on failure."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


class MetadataLookupTool:
    """Look up structured media metadata from TMDB/TVMaze/IMDb fallback.

    The tool owns the agent-facing contract only.  Argument normalization,
    settings-aware client resolution, persisted library snapshots, and result
    scoring are delegated to support services so metadata lookup remains a
    reusable capability rather than a series of scenario-specific patches.
    """

    name = "metadata_lookup"
    description = (
        "Look up structured movie/TV metadata using services such as TMDB and TVMaze. "
        "Use this before general web_search for factual media questions about cast, "
        "creators, seasons, episodes, air dates, ratings, summaries, IDs, or artwork. "
        "Returns structured results including cast lists when the service provides them."
    )
    intents = {Intent.SEARCH, Intent.DOWNLOAD, Intent.CHAT}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies: list[str] = []

    def __init__(
        self,
        tmdb_client: Optional["TMDBClient"] = None,
        tvmaze_client: Optional["TVMazeClient"] = None,
        settings_manager: Optional["SettingsManager"] = None,
        database: Any | None = None,
    ) -> None:
        """Create the lookup tool with optional injected provider clients."""
        self._clients = MetadataClientResolver(
            tmdb_client=tmdb_client,
            tvmaze_client=tvmaze_client,
            settings_manager=settings_manager,
        )
        self._library_lookup = LibraryMetadataSnapshotLookup(database)
        self._ia = Cinemagoer() if Cinemagoer else None

    def parameters(self) -> dict:
        """Return JSON schema for generic metadata lookup arguments."""
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Media title or person/show/movie search query, e.g. 'Twin Peaks'.",
                },
                "media_type": {
                    "type": "string",
                    "description": "auto, tv, movie, person, or multi. Prefer tv for series questions and movie for films.",
                    "enum": ["auto", "tv", "movie", "person", "multi"],
                },
                "service": {
                    "type": "string",
                    "description": "auto, tmdb, tvmaze, or imdb. Auto tries the best category-appropriate services.",
                    "enum": ["auto", "tmdb", "tvmaze", "imdb"],
                },
                "tmdb_id": {"type": "integer", "description": "Known TMDB id, if available."},
                "tvmaze_id": {"type": "integer", "description": "Known TVMaze show id, if available."},
                "season": {"type": "integer", "description": "Optional TV season number for episode/air-date details."},
                "episode": {"type": "integer", "description": "Optional TV episode number for episode/air-date details."},
                "question": {
                    "type": "string",
                    "description": "Optional natural-language question to help the assistant focus the returned metadata.",
                },
                "include_episodes": {
                    "type": "boolean",
                    "description": "Whether to include episode lists when available. Defaults to true when season is provided.",
                },
            },
            "required": ["query"],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Run a generic metadata lookup across configured metadata services."""
        request_or_error = MetadataLookupRequest.from_arguments(arguments)
        if isinstance(request_or_error, dict):
            return request_or_error
        request = request_or_error

        logger.info(
            "Tool: metadata_lookup query={!r} media_type={} service={} season={} episode={}",
            request.query,
            request.media_type,
            request.service,
            request.season,
            request.episode,
        )

        services_tried: list[str] = []
        results: list[dict[str, Any]] = []

        library_result = await self._library_lookup.lookup(
            query=request.query,
            media_type=request.media_type,
            season=request.season,
        )
        if library_result:
            services_tried.append("library_snapshot")
            results.append(library_result)
            if request.service == "auto" and self._result_can_answer(library_result, request):
                return self._success_payload(request, services_tried, results, library_result)

        tmdb_client = await self._clients.get_tmdb_client()
        availability = self._clients.availability(
            library_snapshot_found=bool(library_result),
            imdb_client_ready=bool(self._ia),
        )
        logger.info("Metadata lookup availability for query={!r}: {}", request.query, availability)

        if request.service in {"auto", "tmdb"}:
            services_tried.append("tmdb")
            if tmdb_client:
                result = await self._lookup_tmdb(tmdb_client, request)
                if result:
                    results.append(result)
                    if request.service != "auto" or self._result_can_answer(result, request):
                        return self._success_payload(request, services_tried, results, result)
            else:
                logger.warning(
                    "metadata_lookup wanted TMDB for {!r}, but the agent-facing TMDB client was not ready. Availability: {}",
                    request.query,
                    availability,
                )

        allow_fallbacks = request.service != "auto" and not results
        tvmaze_client = self._clients.get_tvmaze_client() if (request.service in {"auto", "tvmaze"} or allow_fallbacks) else None
        if tvmaze_client and request.media_type in {"auto", "tv", "multi", "person"}:
            if "tvmaze" not in services_tried:
                services_tried.append("tvmaze")
            result = await self._lookup_tvmaze(tvmaze_client, request)
            if result:
                results.append(result)
                if request.service != "auto" or self._result_can_answer(result, request):
                    return self._success_payload(request, services_tried, results, result)

        if (request.service in {"auto", "imdb"} or allow_fallbacks) and self._ia:
            if "imdb" not in services_tried:
                services_tried.append("imdb")
            result = await asyncio.to_thread(self._lookup_imdb_sync, request.query)
            if result:
                results.append(result)

        if not results:
            availability = self._clients.availability(
                library_snapshot_found=bool(library_result),
                imdb_client_ready=bool(self._ia),
            )
            return self._empty_payload(request, services_tried, availability)

        best = MetadataResultNormalizer.choose_best_result(results, request.media_type)
        return self._success_payload(request, services_tried, results, best)

    def _result_can_answer(self, result: dict[str, Any], request: MetadataLookupRequest) -> bool:
        """Return whether a metadata result can answer this exact request."""
        if not self._library_lookup.can_answer(result, request.question):
            return False
        if request.episode is None:
            return True
        requested = self.requested_episode_hint(result, request.season, request.episode)
        if not requested:
            return False
        return bool(requested.get("air_date") or requested.get("title"))

    async def _lookup_tmdb(self, client: "TMDBClient", request: MetadataLookupRequest) -> dict[str, Any] | None:
        """Lookup TMDB metadata by id or search query."""
        try:
            kind = "tv" if request.media_type in {"tv", "auto"} else request.media_type
            if request.media_type in {"multi", "person"}:
                kind = "multi"
            selected: dict[str, Any] | None = None
            tmdb_id = request.tmdb_id
            if tmdb_id is None:
                search_type = kind if kind in {"movie", "tv", "multi"} else "multi"
                matches = []
                for candidate_query in self._candidate_queries(request.query):
                    matches = await client.search(candidate_query, media_type=search_type)
                    if matches:
                        break
                if not matches:
                    return None
                selected = MetadataResultNormalizer.select_tmdb_match(matches, request.media_type)
                tmdb_id = _safe_int(selected.get("id")) if selected else None
            if not tmdb_id:
                return None

            result_type = "movie" if request.media_type == "movie" else "tv"
            if selected and selected.get("type") in {"movie", "tv"}:
                result_type = selected["type"]
            if request.media_type == "movie":
                details = await client.get_movie_details(tmdb_id)
            else:
                details = await client.get_tv_details(tmdb_id)
                result_type = "tv"
            if not details:
                return None
            normalized = MetadataResultNormalizer.normalize_tmdb_details(details, result_type)
            if selected:
                normalized["search_match"] = selected
            if result_type == "tv" and request.include_episodes:
                season_number = request.season or self._latest_numbered_season(normalized)
                if season_number is not None:
                    season_details = await client.get_tv_season_details(tmdb_id, season_number)
                    if season_details:
                        normalized["season_details"] = season_details
            return normalized
        except Exception as exc:  # pragma: no cover - defensive around network clients
            logger.warning(f"TMDB metadata lookup failed for {request.query!r}: {exc}")
            return None

    @staticmethod
    def _latest_numbered_season(details: dict[str, Any]) -> int | None:
        """Return the latest non-special season number from normalized details."""
        seasons = details.get("seasons") if isinstance(details.get("seasons"), list) else []
        numbers: list[int] = []
        for season in seasons:
            if not isinstance(season, dict):
                continue
            value = _safe_int(season.get("season_number"))
            if value is not None and value > 0:
                numbers.append(value)
        return max(numbers) if numbers else None

    async def _lookup_tvmaze(self, client: "TVMazeClient", request: MetadataLookupRequest) -> dict[str, Any] | None:
        """Lookup TVMaze metadata by id or search query."""
        try:
            selected: dict[str, Any] | None = None
            tvmaze_id = request.tvmaze_id
            if tvmaze_id is None:
                matches = []
                for candidate_query in self._candidate_queries(request.query):
                    matches = await client.search(candidate_query)
                    if matches:
                        break
                if not matches:
                    return None
                selected = matches[0]
                tvmaze_id = _safe_int(selected.get("id"))
            if not tvmaze_id:
                return None
            details = await client.get_show_details(tvmaze_id)
            if not details:
                return None
            normalized = {
                "provider": "tvmaze",
                "type": "tv",
                "id": details.get("id"),
                "title": details.get("name"),
                "genres": details.get("genres") or [],
                "rating": details.get("rating"),
                "status": details.get("status"),
                "first_air_date": details.get("premiered"),
                "schedule": details.get("schedule"),
                "network": details.get("network") or details.get("web_channel"),
                "next_episode": details.get("next_episode"),
                "imdb_id": details.get("imdb_id"),
                "search_match": selected,
            }
            if request.include_episodes:
                normalized["episodes"] = await client.get_episode_list(tvmaze_id, season=request.season)
            return normalized
        except Exception as exc:  # pragma: no cover - defensive around network clients
            logger.warning(f"TVMaze metadata lookup failed for {request.query!r}: {exc}")
            return None

    def _lookup_imdb_sync(self, query: str) -> dict[str, Any] | None:
        """Lookup IMDb metadata through optional Cinemagoer fallback."""
        if not self._ia:
            return None
        try:
            matches = []
            for candidate_query in self._candidate_queries(query):
                matches = self._ia.search_movie(candidate_query)
                if matches:
                    break
            if not matches:
                return None
            media = matches[0]
            self._ia.update(media, ["main", "plot"])
            cast = [
                {"name": str(person), "character": ""}
                for person in (media.get("cast") or [])[:10]
            ]
            return {
                "provider": "imdb",
                "type": media.get("kind"),
                "id": media.movieID,
                "title": media.get("title"),
                "year": media.get("year"),
                "rating": media.get("rating"),
                "genres": media.get("genres") or [],
                "overview": media.get("plot outline") or media.get("plot"),
                "cast": cast,
                "lead_cast": cast[:3],
            }
        except Exception as exc:  # pragma: no cover - optional dependency/network
            logger.warning(f"IMDb metadata lookup failed for {query!r}: {exc}")
            return None

    @staticmethod
    def _candidate_queries(query: str) -> list[str]:
        """Return title-focused query variants; kept for regression-test compatibility."""
        return MetadataResultNormalizer.candidate_queries(query)

    @staticmethod
    def requested_episode_hint(best: dict[str, Any], season: int | None, episode: int | None) -> dict[str, Any] | None:
        """Return the requested episode details when present in provider data."""
        if episode is None:
            return None
        season_details = best.get("season_details") if isinstance(best.get("season_details"), dict) else {}
        episodes = best.get("episodes") or season_details.get("episodes") or []
        if not isinstance(episodes, list):
            return None
        for ep in episodes:
            if not isinstance(ep, dict):
                continue
            ep_no = _safe_int(ep.get("episode_number") or ep.get("number"))
            ep_season = _safe_int(ep.get("season") or season_details.get("season_number"))
            if ep_no != episode:
                continue
            if season is not None and ep_season is not None and ep_season != season:
                continue
            return {
                "season": ep_season or season,
                "episode_number": ep_no,
                "title": ep.get("name"),
                "air_date": ep.get("air_date") or ep.get("airdate"),
                "runtime_minutes": ep.get("runtime_minutes"),
                "source_provider": best.get("provider"),
            }
        return None

    @staticmethod
    def _success_payload(
        request: MetadataLookupRequest,
        services_tried: list[str],
        results: list[dict[str, Any]],
        best: dict[str, Any],
    ) -> dict[str, Any]:
        """Return the standard successful tool result envelope."""
        answer_hints = MetadataResultNormalizer.answer_hints(best)
        requested_episode = MetadataLookupTool.requested_episode_hint(best, request.season, request.episode)
        payload = {
            "ok": True,
            "query": request.query,
            "media_type": request.media_type,
            "question": request.question,
            "services_tried": services_tried,
            "best": best,
            "results": results,
            "answer_hints": answer_hints,
        }
        if requested_episode:
            # Keep the canonical compact hint and a top-level compatibility alias.
            # Some local planners produce placeholders such as
            # ${lookup.episode.air_date}; exposing this alias keeps the
            # deterministic plan executor from failing after a successful
            # episode-level lookup.  The historical ``results`` key remains a
            # provider-result list and is intentionally not overloaded.
            answer_hints["requested_episode"] = requested_episode
            payload["requested_episode"] = requested_episode
            payload["episode"] = requested_episode
        return payload

    @staticmethod
    def _empty_payload(
        request: MetadataLookupRequest,
        services_tried: list[str],
        availability: dict[str, bool],
    ) -> dict[str, Any]:
        """Return a non-terminal metadata miss that the planner can follow with web search."""
        if not services_tried or all(name == "tmdb" for name in services_tried):
            return {
                "ok": False,
                "query": request.query,
                "media_type": request.media_type,
                "error": "No metadata service result was available to the agent; fall back to web search if available.",
                "services_tried": services_tried,
                "availability": availability,
            }
        return {
            "ok": False,
            "query": request.query,
            "media_type": request.media_type,
            "error": "No metadata results found; fall back to web search if available.",
            "services_tried": services_tried,
            "availability": availability,
        }


class DateComparisonTool:
    """Compare a date against the current runtime date for tense-safe replies."""

    name = "compare_date_to_now"
    description = (
        "Compare a YYYY-MM-DD date or ISO timestamp against the current runtime date. "
        "Use this after reading an air/release date when tense matters, so future "
        "dates are described as scheduled/upcoming rather than already aired/released."
    )
    intents = {Intent.SEARCH, Intent.DOWNLOAD, Intent.CHAT}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies: list[str] = []

    def parameters(self) -> dict:
        """Return JSON schema for date comparison."""
        return {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date to compare, preferably YYYY-MM-DD or ISO timestamp."},
                "label": {"type": "string", "description": "Optional label for the date, e.g. S05E10 air date."},
            },
            "required": ["date"],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Compare the supplied date to today's runtime date."""
        value = str(arguments.get("date") or "").strip()
        parsed = self._parse_date(value)
        now = datetime.now(timezone.utc).astimezone()
        today = now.date()
        if parsed is None:
            return {
                "ok": False,
                "error": "Could not parse date; expected YYYY-MM-DD or ISO timestamp.",
                "input": value,
                "current_date": today.isoformat(),
                "current_datetime": now.isoformat(timespec="seconds"),
            }
        delta = (parsed - today).days
        relation = "future" if delta > 0 else "past" if delta < 0 else "today"
        return {
            "ok": True,
            "label": str(arguments.get("label") or "").strip() or None,
            "input": value,
            "date": parsed.isoformat(),
            "current_date": today.isoformat(),
            "current_datetime": now.isoformat(timespec="seconds"),
            "relation": relation,
            "days_delta": delta,
            "tense_guidance": self._tense_guidance(relation),
        }

    @staticmethod
    def _parse_date(value: str):
        """Parse conservative date shapes without external dependencies."""
        if not value:
            return None
        iso_match = re.search(r"(\d{4}-\d{2}-\d{2})", value)
        if iso_match:
            try:
                return datetime.fromisoformat(iso_match.group(1)).date()
            except ValueError:
                return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            return None

    @staticmethod
    def _tense_guidance(relation: str) -> str:
        """Return a user-facing wording hint for the relation."""
        if relation == "future":
            return "Use future wording: is scheduled to air/release; do not say aired, premiered, or released."
        if relation == "today":
            return "Use same-day wording: airs/releases today, or has aired today only if a time/source confirms it."
        return "Past wording is acceptable: aired, premiered, or released."


class GetIMDBDetailsTool:
    """Retrieve details about a movie or TV item from IMDb/Cinemagoer."""

    name = "get_imdb_details"
    description = "Get category-neutral IMDb details including plot, rating, kind, and optional episode data. Prefer metadata_lookup for new media factual questions."
    intents = {Intent.SEARCH, Intent.DOWNLOAD}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = []

    def __init__(self) -> None:
        """Initialize the optional Cinemagoer client when installed."""
        self._ia = Cinemagoer() if Cinemagoer else None

    def parameters(self) -> dict:
        """Return JSON schema for IMDb lookup arguments."""
        return {
            "type": "object",
            "properties": {"title": {"type": "string", "description": "The media title to search on IMDb."}},
            "required": ["title"],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Retrieve details about a media title from IMDb."""
        title = _resolve_title(arguments)
        if not title:
            return {"error": "Missing required argument: title"}
        if not self._ia:
            return {"error": "IMDb lookup unavailable; cinemagoer is not installed."}
        logger.info(f"Tool: getting IMDb details for '{title}'")
        return await asyncio.to_thread(self._get_imdb_details_sync, title)

    def _get_imdb_details_sync(self, title: str) -> dict[str, Any]:
        """Run the blocking Cinemagoer lookup in a worker thread."""
        try:
            search_results = self._ia.search_movie(title)
            if not search_results:
                return {"error": "Media not found"}

            media = search_results[0]
            self._ia.update(media, ["episodes", "plot", "main"])
            cast = [{"name": str(person), "character": ""} for person in (media.get("cast") or [])[:10]]
            result = {
                "title": media.get("title"),
                "year": media.get("year"),
                "rating": media.get("rating"),
                "genres": media.get("genres"),
                "plot": media.get("plot outline") or media.get("plot"),
                "kind": media.get("kind"),
                "cast": cast,
                "lead_cast": cast[:3],
            }
            if media.get("kind") == "tv series" and "episodes" in media.data:
                result["episodes"] = self._extract_episode_data(media.data["episodes"])
            return result
        except Exception as exc:
            logger.error(f"IMDb details lookup failed: {exc}")
            return {"error": str(exc)}

    @staticmethod
    def _extract_episode_data(raw_episodes: dict[Any, Any]) -> dict[int, list[dict[str, Any]]]:
        """Normalize Cinemagoer episode data by season."""
        episodes_data: dict[int, list[dict[str, Any]]] = {}
        for season_num, episodes in raw_episodes.items():
            try:
                season_num_int = int(season_num)
            except (ValueError, TypeError):
                continue
            episodes_data[season_num_int] = [
                {
                    "title": episode.get("title"),
                    "air_date": episode.get("original air date"),
                    "episode_num": episode.get("episode"),
                }
                for episode in episodes.values()
            ]
        return episodes_data


class ResearchToolProvider:
    """Provides category-neutral research agent tools."""

    def __init__(
        self,
        tmdb_client: Optional["TMDBClient"] = None,
        tvmaze_client: Optional["TVMazeClient"] = None,
        settings_manager: Optional["SettingsManager"] = None,
        database: Any | None = None,
    ) -> None:
        """Keep old constructor dependencies and expose generic metadata lookup."""
        self._tmdb_client = tmdb_client
        self._tvmaze_client = tvmaze_client
        self._settings_manager = settings_manager
        self._database = database

    def get_tools(self) -> list:
        """Return instantiated research tool instances."""
        tmdb_setting_present = False
        if self._settings_manager is not None:
            tmdb_setting_present = bool(getattr(self._settings_manager.settings, "tmdb_api_key", None))
        logger.info(
            "Research metadata tool wiring: tmdb_client_injected={} tmdb_setting_present={} "
            "tvmaze_client_injected={} settings_manager_injected={} database_injected={}",
            bool(self._tmdb_client),
            tmdb_setting_present,
            bool(self._tvmaze_client),
            self._settings_manager is not None,
            self._database is not None,
        )
        tools: list[Any] = [
            DateComparisonTool(),
            MetadataLookupTool(
                tmdb_client=self._tmdb_client,
                tvmaze_client=self._tvmaze_client,
                settings_manager=self._settings_manager,
                database=self._database,
            )
        ]
        if Cinemagoer is None:
            logger.info("Cinemagoer is not installed; excluding legacy IMDb details tool.")
        else:
            tools.append(GetIMDBDetailsTool())
        return tools
