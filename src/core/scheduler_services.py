"""Focused services used by MediaScheduler public operations.

MediaScheduler remains the composition root for background jobs.  User-facing
catalog, priority, and torrent-search operations are delegated to these bounded
services so the scheduler class does not accumulate category-specific behavior.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loguru import logger

from src.integrations.slskd_client import SlskdClient
from src.core.models import CategoryItem, DownloadPriority, GenericMediaItem, ScannedLibraryItem, SearchResult
from src.core.library_objects import CanonicalLibraryObjectBuilder

if TYPE_CHECKING:
    from src.core.config import SettingsManager
    from src.core.database import Database
    from src.core.downloader import DownloadManager
    from src.core.search_pipeline import SearchPipeline
    from src.search.aggregator import SearchAggregator


@dataclass
class SchedulerServiceContext:
    """Collaborator bundle shared by scheduler sub-services.

    Add new fields here only when a service genuinely needs the collaborator.
    This explicit context keeps services testable and avoids reaching into
    MediaScheduler private attributes from outside its class.
    """

    settings_manager: "SettingsManager"
    db: "Database"
    downloader: "DownloadManager"
    pipeline: "SearchPipeline"
    aggregator: "SearchAggregator"
    categories: object | None = None
    tvmaze: object | None = None
    metadata_enricher: object | None = None


class SchedulerCatalogService:
    """Handle media catalog listing, matching, and download priority changes."""

    def __init__(self, context: SchedulerServiceContext) -> None:
        """Create the catalog service with explicit scheduler collaborators."""
        self._context = context

    async def list_media(self) -> dict[str, Any]:
        """Return tracked media items with progress and paused state."""
        items = []
        for media in self._context.settings_manager.settings.tracked_items:
            items.append(await self._media_row(media))
        return {"media": items}

    async def list_media_items(self, name: str) -> dict[str, Any]:
        """Return downloaded and active units for one tracked media item."""
        media = self.find_tracked_media(name)
        if not media:
            return {"error": f"Media '{name}' not found."}
        items = await self._downloaded_units(media, name)
        items.extend(await self._active_download_units(name))
        return {"name": name, "category": media.item_type, "items": self._sorted_units(items)}

    async def set_download_priority(
        self,
        name: str,
        priority: str,
        season: int | None = None,
        episode: int | None = None,
    ) -> dict[str, Any]:
        """Change priority for active or queued downloads matching filters."""
        try:
            download_priority = DownloadPriority(priority.lower())
        except ValueError:
            return {"error": f"Invalid priority: {priority}. Use: high, normal, low."}
        downloads = await self._target_downloads(name, season, episode)
        for download in downloads:
            await self._context.downloader.set_priority(download.id, download_priority)
        return {"status": "ok", "updated_count": len(downloads)}

    def find_tracked_media(self, name: str, category_id: str | None = None) -> CategoryItem | None:
        """Find a tracked item by exact key/display name, then conservative fuzzy match."""
        from src.utils.item_matcher import ItemMatcher

        needle = (name or "").strip()
        if not needle:
            return None
        candidates = self._tracked_candidates(category_id)
        lowered = needle.lower()
        for item in candidates:
            if lowered in self._item_names(item):
                return item
        for item in candidates:
            display = getattr(item, "display_name", None)
            if ItemMatcher.fuzzy_match_names(item.key, needle):
                return item
            if display and ItemMatcher.fuzzy_match_names(display, needle):
                return item
        return None

    @staticmethod
    def safe_structured_unit_int(value: Any) -> int | None:
        """Return a positive structured-unit integer or None for placeholders."""
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        return None

    @classmethod
    def extract_structured_unit_from_name(cls, name: str, season: int | None, episode: int | None) -> tuple[str, int | None, int | None]:
        """Recover obvious structured unit hints from an under-structured name.

        The public assistant schema currently exposes ``season``/``episode`` as
        generic unit coordinates.  This parser only extracts coordinates; the
        registered categories decide whether those coordinates mean anything.
        """
        season = cls.safe_structured_unit_int(season)
        episode = cls.safe_structured_unit_int(episode)
        cleaned = (name or "").strip()
        if not cleaned:
            return cleaned, season, episode
        cleaned, season, episode = cls._extract_episode_pattern(cleaned, season, episode)
        cleaned, season = cls._extract_season_pattern(cleaned, season, episode)
        cleaned = re.sub(r"\b(?:complete|pack|torrent|torrents|missing|episodes?|episodi|serie|series)\b", " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_.,")
        return cleaned or name, season, episode

    async def _media_row(self, media: CategoryItem) -> dict[str, Any]:
        """Build one list_media row from the canonical category object."""
        canonical = await self._canonical_object(media)
        computed = canonical.get("computed") or {}
        progress = await self._context.db.media.get_item_progress(canonical.get("category_id"), media.key)
        paused = await self._context.db.media.get_category_item_paused(canonical.get("category_id"), media.key)
        downloaded_count = int(
            computed.get("downloaded_unit_count")
            or computed.get("downloaded_episode_count")
            or computed.get("downloaded_file_count")
            or 0
        )
        return {
            "name": media.key,
            "category": canonical.get("category_id"),
            "language": getattr(media, "language", None),
            "enabled": media.enabled,
            "paused": paused,
            "downloaded_count": downloaded_count,
            "progress": progress,
            "canonical_object": canonical,
        }

    async def _downloaded_units(self, media: CategoryItem, name: str) -> list[dict[str, Any]]:
        """Return downloaded canonical units for one media item."""
        canonical = await self._canonical_object(media)
        return [unit for unit in canonical.get("units", []) if unit.get("status") == "downloaded"]

    async def _canonical_object(self, media: CategoryItem) -> dict[str, Any]:
        """Build a canonical library object through the category-owned contract."""
        category_id = getattr(media, "category_id", getattr(media, "item_type", "media")) or "media"
        builder = CanonicalLibraryObjectBuilder(self._context.db, self._context.categories)
        return await builder.build(category_id, media.key, settings_item=media)

    async def _active_download_units(self, name: str) -> list[dict[str, Any]]:
        """Return active download units for one media item."""
        active = await self._context.downloader.get_active_downloads()
        return [{"season": dl.season, "episode": dl.episode, "status": "downloading", "progress": dl.progress, "id": dl.id} for dl in active if dl.item_name == name]

    async def _target_downloads(self, name: str, season: int | None, episode: int | None) -> list[object]:
        """Return downloads matching a priority-change request."""
        active = await self._context.downloader.get_active_downloads()
        queued = await self._context.downloader.get_queued_downloads()
        all_downloads = active + queued
        return [dl for dl in all_downloads if dl.item_name == name and (season is None or dl.season == season) and (episode is None or dl.episode == episode)]

    def _tracked_candidates(self, category_id: str | None) -> list[CategoryItem]:
        """Return tracked items matching an optional category id."""
        candidates = []
        for item in self._context.settings_manager.settings.tracked_items:
            item_category = getattr(item, "category_id", None) or getattr(item, "item_type", None)
            if not category_id or item_category == category_id:
                candidates.append(item)
        return candidates

    def _item_names(self, item: CategoryItem) -> set[str]:
        """Return exact-match names for one tracked item."""
        names = {str(item.key).lower()}
        display = getattr(item, "display_name", None)
        if display:
            names.add(str(display).lower())
        return names

    def _sorted_units(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Sort category units by season and episode fields."""
        return sorted(items, key=lambda item: (item.get("season") or 0, item.get("episode") or 0))

    @classmethod
    def _extract_episode_pattern(cls, cleaned: str, season: int | None, episode: int | None) -> tuple[str, int | None, int | None]:
        """Extract compact two-coordinate unit notation from free text."""
        match = re.search(r"\bS0*(\d{1,2})\s*E0*(\d{1,3})\b", cleaned, re.IGNORECASE)
        if not match:
            return cleaned, season, episode
        season = season or int(match.group(1))
        episode = episode or int(match.group(2))
        return re.sub(r"\bS0*\d{1,2}\s*E0*\d{1,3}\b", " ", cleaned, flags=re.IGNORECASE), season, episode

    @classmethod
    def _extract_season_pattern(cls, cleaned: str, season: int | None, episode: int | None) -> tuple[str, int | None]:
        """Extract first-coordinate references from free text."""
        match = re.search(r"\b(?:season|stagione)\s*0*(\d{1,2})\b", cleaned, re.IGNORECASE)
        if match:
            season = season or int(match.group(1))
            cleaned = re.sub(r"\b(?:season|stagione)\s*0*\d{1,2}\b", " ", cleaned, flags=re.IGNORECASE)
        compact = re.search(r"\bS0*(\d{1,2})\b", cleaned, re.IGNORECASE)
        if compact and episode is None:
            season = season or int(compact.group(1))
            cleaned = re.sub(r"\bS0*\d{1,2}\b", " ", cleaned, flags=re.IGNORECASE)
        return cleaned, season


class SchedulerTorrentSearchService:
    """Search category-aware torrent candidates for the assistant."""

    def __init__(self, context: SchedulerServiceContext) -> None:
        """Create the search service with explicit scheduler collaborators."""
        self._context = context
        self._catalog = SchedulerCatalogService(context)

    async def search_media_torrents(
        self,
        name: str,
        season: int | None = None,
        episode: int | None = None,
        language: str | None = None,
        language_explicit: bool = False,
        search_scope: str | None = None,
        category_id: str | None = None,
    ) -> dict[str, Any]:
        """Search for torrents for a specific media item via the unified pipeline."""
        settings = self._context.settings_manager.settings
        normalized_name, season, episode = self._catalog.extract_structured_unit_from_name(name, season, episode)
        normalized_scope = self._normalize_search_scope(search_scope)
        explicit_category_id = str(category_id or "").strip() or None
        if explicit_category_id and (not self._context.categories or not self._context.categories.get(explicit_category_id)):
            return {
                "error": "Unknown category",
                "ok": False,
                "category_id": explicit_category_id,
                "recoverable": True,
                "available_categories": self._context.categories.list_ids() if self._context.categories else [],
                "next_actions": [
                    {
                        "action": "choose_registered_category",
                        "tool": "search_media_torrents",
                        "reason": "The requested category_id is not registered in this installation.",
                    }
                ],
            }
        requested_category = explicit_category_id or self._category_for_units(season, episode)
        if not requested_category:
            requested_category = self._category_for_search_scope(normalized_scope)
        initial_category = self._context.categories.get(requested_category) if requested_category and self._context.categories else None
        initial_lang = self._effective_search_language(
            initial_category,
            requested_language=language,
            explicit=language_explicit,
            settings=settings,
            category_id=requested_category,
        )
        media = await self._media_for_request(name, normalized_name, requested_category, initial_lang or "")
        category_id = getattr(media, "category_id", None) or getattr(media, "item_type", "")
        category = self._context.categories.get(category_id) if self._context.categories else None
        target_lang = self._effective_search_language(
            category,
            requested_language=language,
            explicit=language_explicit,
            settings=settings,
            category_id=category_id,
            tracked_language=getattr(media, "language", None),
        )
        season = await self._resolve_category_default_season(
            media, category_id, season, episode, normalized_scope, settings,
        )
        results, query_summary = await self._search(media, category_id, season, episode, target_lang, settings, normalized_scope)
        response = self._response(media, category_id, season, episode, target_lang, results, query_summary, normalized_scope)
        response["source_strategy"] = self._source_strategy(category_id, normalized_name, normalized_scope, settings)
        response["companion_soulseek"] = await self._soulseek_companion_search(
            query_summary=query_summary,
            media=media,
            category_id=category_id,
            search_scope=normalized_scope,
            settings=settings,
        )
        return response

    def _source_strategy(self, category_id: str, name: str, search_scope: str, settings: object) -> dict[str, Any]:
        """Return the effective source strategy for the current category search."""
        cfg = getattr(settings, "soulseek", None)
        preference = getattr(cfg, "download_preference", "torrent_first") if cfg else "torrent_first"
        category = str(category_id or "").lower()
        text = str(name or "").lower()
        is_large_music_bundle = category == "music" and any(term in text for term in ("discography", "complete", "catalog", "catalogue", "collection"))
        if category == "music" and not is_large_music_bundle and search_scope == "default" and getattr(cfg, "enabled", False):
            # The user specifically asked for this behavior: albums and single songs
            # should prefer Soulseek when it is enabled, while full discographies
            # remain torrent-first because bundles are more torrent-shaped.
            preference = "soulseek_first"
        return {
            "parallel_search_enabled": bool(getattr(cfg, "parallel_search_enabled", False)) if cfg else False,
            "download_preference": preference,
            "torrent_queue_tool": "queue_download",
            "soulseek_queue_tool": "enqueue_soulseek_download",
            "note": "Evaluate torrent and Soulseek candidates together when companion_soulseek has ready candidates; use the queue tool that matches the selected backend.",
        }

    async def _soulseek_companion_search(
        self,
        *,
        query_summary: str,
        media: CategoryItem,
        category_id: str,
        search_scope: str,
        settings: object,
    ) -> dict[str, Any]:
        """Run a bounded Soulseek companion search alongside torrent search.

        The result is presented next to torrent candidates without mixing backend
        semantics.  Queueing remains explicit through enqueue_soulseek_download.
        """
        cfg = getattr(settings, "soulseek", None)
        if not cfg or not getattr(cfg, "enabled", False):
            return {"enabled": False, "status": "disabled", "candidate_count": 0, "candidates": []}
        enabled_categories = {str(cat).strip().lower() for cat in (getattr(cfg, "search_enabled_categories", []) or []) if str(cat).strip()}
        if enabled_categories and str(category_id or "").lower() not in enabled_categories:
            return {"enabled": True, "status": "category_disabled", "category_id": category_id, "candidate_count": 0, "candidates": []}
        if not getattr(cfg, "parallel_search_enabled", True):
            return {"enabled": True, "status": "parallel_disabled", "candidate_count": 0, "candidates": []}
        if not getattr(cfg, "api_configured", False):
            return {"enabled": True, "status": "not_configured", "candidate_count": 0, "candidates": [], "error": "slskd is not configured."}
        if getattr(cfg, "managed", True):
            if not getattr(cfg, "soulseek_credentials_configured", False):
                return {
                    "enabled": True,
                    "status": "needs_credentials",
                    "account_status": getattr(cfg, "account_status", "needs_credentials"),
                    "candidate_count": 0,
                    "candidates": [],
                    "error": "Soulseek username and password are required before searching.",
                }
            if str(getattr(cfg, "account_status", "")).lower() == "auth_failed":
                return {
                    "enabled": True,
                    "status": "auth_failed",
                    "account_status": getattr(cfg, "account_status", "auth_failed"),
                    "candidate_count": 0,
                    "candidates": [],
                    "error": getattr(cfg, "account_status_message", "Soulseek rejected these credentials."),
                }
        # For very broad pack searches, keep Soulseek as visible-but-secondary;
        # a single-user queue is rarely the best way to pull huge catalogues.
        queries = self._soulseek_query_variants(query_summary, media)
        if not queries:
            return {"enabled": True, "status": "empty_query", "candidate_count": 0, "candidates": []}
        result: dict[str, Any] = {}
        candidates: list[dict[str, Any]] = []
        tried_queries: list[str] = []
        try:
            for query in queries:
                tried_queries.append(query)
                logger.info(f"Soulseek companion search: category={category_id} query={query!r}")
                result = await SlskdClient(cfg).search(query, max_results=min(int(getattr(cfg, "max_search_results", 10) or 10), 10))
                candidate_rows = result.get("candidates") if isinstance(result, dict) else []
                if isinstance(candidate_rows, list) and candidate_rows:
                    candidates = candidate_rows
                    break
        except Exception as exc:
            logger.warning("Soulseek companion search failed for %s: %s", queries, exc)
            return {"enabled": True, "status": "error", "candidate_count": 0, "candidates": [], "error": str(exc), "queries": tried_queries}
        logger.info(f"Soulseek companion search complete: category={category_id} queries={tried_queries} candidates={len(candidates)}")
        if not isinstance(candidates, list):
            candidates = []
        if isinstance(result, dict) and result.get("ok") is True:
            try:
                cfg.account_status = "ready"
                cfg.account_status_message = "Soulseek account authenticated."
            except Exception:
                pass
        elif isinstance(result, dict):
            error_text = str(result.get("error") or "").lower()
            if any(token in error_text for token in ("not logged in", "not connected", "connect to server")):
                try:
                    cfg.account_status = "checking"
                    cfg.account_status_message = "slskd is running but not connected/logged in to Soulseek yet."
                except Exception:
                    pass
        return {
            "enabled": True,
            "status": "ready" if result.get("ok") is True else ("account_not_ready" if isinstance(result, dict) and any(token in str(result.get("error") or "").lower() for token in ("not logged in", "not connected", "connect to server")) else "error"),
            "source": "slskd",
            "query": tried_queries[0] if tried_queries else "",
            "queries": tried_queries,
            "candidate_count": len(candidates),
            "candidates": candidates[:10],
            "filtering": result.get("filtering") if isinstance(result, dict) else {},
            "raw_response_count": result.get("raw_response_count") if isinstance(result, dict) else None,
            "raw_file_count": result.get("raw_file_count") if isinstance(result, dict) else None,
            "queueing_note": "Use enqueue_soulseek_download with username + filename. Do not pass these candidates to queue_download.",
            "error": result.get("error") if isinstance(result, dict) else None,
        }

    @staticmethod
    def _soulseek_query_variants(query_summary: str, media: CategoryItem) -> list[str]:
        """Return ordered Soulseek queries for one category item.

        Soulseek is much more literal than torrent indexers.  Do not include
        explanatory words such as "album" or torrent-style quality noise in
        the first search.  Try a few short artist/title permutations so album
        folders shared as either "Artist/Album" or "Album Artist" can match.
        """
        raw_values = [
            str(query_summary or "").strip(),
            str(getattr(media, "key", "") or "").strip(),
            str(getattr(media, "display_name", "") or "").strip(),
        ]
        queries: list[str] = []
        seen: set[str] = set()

        def add(value: str) -> None:
            cleaned = SchedulerTorrentSearchService._clean_soulseek_query(value)
            if cleaned and cleaned.casefold() not in seen:
                seen.add(cleaned.casefold())
                queries.append(cleaned)

        for value in raw_values:
            cleaned = SchedulerTorrentSearchService._clean_soulseek_query(value)
            # Handle common natural phrases before stripping relation words.
            natural = re.search(r"(?P<title>.+?)\s+(?:by|from|di|da)\s+(?P<artist>.+)$", cleaned, re.IGNORECASE)
            if natural:
                title = natural.group("title")
                artist = natural.group("artist")
                add(f"{artist} {title}")
                add(f"{title} {artist}")
                add(title)
                add(artist)
            add(re.sub(r"\b(?:from|by|di|da)\b", " ", cleaned, flags=re.IGNORECASE))
            if " - " in cleaned:
                left, right = [part.strip() for part in cleaned.split(" - ", 1)]
                add(f"{left} {right}")
                add(f"{right} {left}")
                add(left)
                add(right)
            tokens = cleaned.split()
            if len(tokens) >= 4:
                mid = len(tokens) // 2
                first = " ".join(tokens[:mid])
                second = " ".join(tokens[mid:])
                add(f"{second} {first}")
                add(first)
                add(second)
            elif len(tokens) == 3:
                add(" ".join(tokens[1:] + tokens[:1]))

        return queries[:8]

    @staticmethod
    def _clean_soulseek_query(value: str) -> str:
        text = str(value or "")
        text = re.sub(r"\b(?:album|track|song|single|ep|music|release|download|torrent|torrents|grab|get|please)\b", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"\b(?:flac|mp3|aac|alac|m4a|lossless|bitrate|kbps)\b", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"[\[\]{}()]+", " ", text)
        text = re.sub(r"\s+", " ", text).strip(" -_.,")
        return text

    def _category_for_units(self, season: int | None, episode: int | None) -> str | None:
        """Return the first category that accepts requested structured unit arguments."""
        if season is None and episode is None:
            return None
        for category in (self._context.categories.list_all() if self._context.categories else []):
            try:
                if category.accepts_agent_unit_args(season=season, episode=episode):
                    return category.category_id
            except Exception:
                continue
        return None

    def _category_for_search_scope(self, search_scope: str | None) -> str | None:
        """Resolve a category for category-neutral pack search scopes."""
        if search_scope not in {"bundle_preferred", "bundle_only", "season_pack_preferred", "season_pack_only"}:
            return None
        for category in (self._context.categories.list_all() if self._context.categories else []):
            try:
                brief = category.router_brief()
                if "season_pack" in set(brief.item_types or []):
                    return category.category_id
            except Exception:
                continue
        return None

    async def _media_for_request(self, name: str, normalized_name: str, category_id: str | None, language: str) -> CategoryItem:
        """Return a tracked or temporary media item for one torrent search."""
        media = self._catalog.find_tracked_media(normalized_name, category_id)
        if not media and normalized_name != name:
            media = self._catalog.find_tracked_media(name, category_id)
        if media:
            return media
        return await self._temporary_media(normalized_name, category_id, language)

    @staticmethod
    def _normalize_category_search_language(category: object | None, language: str | None, *, explicit: bool = False) -> str | None:
        """Return the category-approved language facet for a search."""
        value = str(language or "").strip()
        if "," in value:
            value = next((part.strip() for part in value.split(",") if part.strip()), "")
        if not value:
            return None
        normalizer = getattr(category, "normalize_search_language", None)
        if callable(normalizer):
            try:
                return normalizer(value, explicit=explicit)
            except TypeError:
                return normalizer(value)
        return value

    def _effective_search_language(
        self,
        category: object | None,
        *,
        requested_language: str | None,
        explicit: bool,
        settings: object,
        category_id: str | None = None,
        tracked_language: str | None = None,
    ) -> str | None:
        """Resolve media/download language without confusing it with chat language.

        The language of the current chat message is only a reply-language hint and
        must not become a torrent audio/subtitle constraint.  ``settings.language``
        is different: it is the user's global/default media preference from setup
        and Compass.  Use it only after explicit request, tracked-item language,
        and category/media download-profile language have had a chance to win.
        """
        if explicit and requested_language:
            return self._normalize_category_search_language(category, requested_language, explicit=True)
        if tracked_language:
            normalized = self._normalize_category_search_language(category, tracked_language, explicit=False)
            if normalized:
                return normalized
        for candidate in self._category_profile_language_candidates(category, settings, category_id):
            normalized = self._normalize_category_search_language(category, candidate, explicit=False)
            if normalized:
                return normalized
        return None

    def _category_profile_language_candidates(self, category: object | None, settings: object, category_id: str | None) -> list[str]:
        """Return configured media-language candidates for a category search."""
        candidates: list[str] = []

        def add(value: object) -> None:
            if isinstance(value, str) and value.strip():
                for part in value.split(','):
                    text = part.strip()
                    if text:
                        candidates.append(text)
            elif isinstance(value, list):
                for entry in value:
                    add(entry)

        for owner in (category,):
            profile_getter = getattr(owner, "category_download_profile", None)
            if callable(profile_getter):
                try:
                    profile = profile_getter(settings) or {}
                except Exception:
                    profile = {}
                if isinstance(profile, dict):
                    for key in (
                        "language",
                        "preferred_language",
                        "audio_language",
                        "preferred_audio_language",
                        "audio_languages",
                        "preferred_audio_languages",
                    ):
                        add(profile.get(key))

        # TV/movie inherit abstract media defaults at the config layer in normal
        # installations, but some old local configs predate that inheritance.
        # Search category/media download profiles before falling back to the
        # global setup language.
        category_settings = getattr(settings, "category_settings", {}) or {}
        for key in (str(category_id or ""), "media"):
            value = category_settings.get(key) if isinstance(category_settings, dict) else None
            profile = value.get("download_profile") if isinstance(value, dict) else None
            if isinstance(profile, dict):
                for field in ("language", "preferred_language", "audio_language", "preferred_audio_language", "audio_languages", "preferred_audio_languages"):
                    add(profile.get(field))

        # The global language chosen during setup is the user's default media
        # language when no category/tracked-item preference is more specific.
        # This is not inferred from the current chat message.
        add(getattr(settings, "language", None))

        # Preserve order but deduplicate case-insensitively.
        seen: set[str] = set()
        unique: list[str] = []
        for value in candidates:
            marker = value.casefold()
            if marker in seen:
                continue
            seen.add(marker)
            unique.append(value)
        return unique


    async def _temporary_media(self, normalized_name: str, category_id: str | None, language: str) -> CategoryItem:
        """Create an in-memory item using category-aware classification when possible."""
        if not category_id:
            try:
                from src.utils.media_classifier import MediaClassifier
                category_id = await MediaClassifier(self._context.settings_manager).classify(normalized_name)
            except Exception as exc:
                logger.warning("Media classification failed for %s: %s; using generic media.", normalized_name, exc)
                category_id = "media"
        category = self._context.categories.get(category_id) if self._context.categories else None
        if category:
            return category.create_item(normalized_name, language=language)
        return GenericMediaItem(key=normalized_name, category_id=category_id, language=language)

    async def _resolve_category_default_season(
        self,
        media: CategoryItem,
        category_id: str,
        season: int | None,
        episode: int | None,
        search_scope: str,
        settings: object,
    ) -> int | None:
        """Let the category resolve omitted season coordinates for pack searches."""
        if season is not None or episode is not None:
            return season
        if search_scope not in {"bundle_preferred", "bundle_only", "season_pack_preferred", "season_pack_only"}:
            return season
        category = self._context.categories.get(category_id) if self._context.categories else None
        if not category or not hasattr(category, "resolve_agent_pack_season"):
            return season
        try:
            context = self._category_workflow_context(settings)
            resolved = await category.resolve_agent_pack_season(media, context)
            return int(resolved) if resolved is not None else season
        except Exception as exc:
            logger.debug("Category default season resolution failed for %s: %s", media.key, exc)
            return season

    def _category_workflow_context(self, settings: object):
        """Build the bounded category context used by assistant search hooks."""
        from src.core.categories.base import CategoryWorkflowContext

        return CategoryWorkflowContext(
            db=self._context.db,
            pipeline=self._context.pipeline,
            aggregator=self._context.aggregator,
            settings=settings,
            downloader=self._context.downloader,
            metadata_clients={"tvmaze": self._context.tvmaze} if self._context.tvmaze else {},
            metadata_enricher=self._context.metadata_enricher,
        )

    async def _search(self, media: CategoryItem, category_id: str, season: int | None, episode: int | None, target_lang: str, settings: object, search_scope: str | None = None) -> tuple[list[SearchResult], str]:
        """Run category-owned search with safe fallback to the pipeline."""
        category = self._context.categories.get(category_id) if self._context.categories else None
        if category and hasattr(category, "search_agent_candidates"):
            try:
                context = self._category_workflow_context(settings)
                return await category.search_agent_candidates(
                    media, season=season, episode=episode, language=target_lang,
                    search_scope=search_scope, context=context,
                )
            except RecursionError:
                logger.exception("Category-owned search recursed for %s; falling back to pipeline.", media.key)
            except Exception as exc:
                logger.warning("Category-owned search failed for %s: %s; falling back to pipeline.", media.key, exc)
        return await self._fallback_search(media, season, episode, target_lang)

    async def _fallback_search(self, media: CategoryItem, season: int | None, episode: int | None, target_lang: str) -> tuple[list[SearchResult], str]:
        """Run the generic search pipeline for a media item and unit label."""
        episode_label = self._fallback_episode_label(season, episode)
        results = await self._context.pipeline.run_search(media, episode_label, mode='llm', language=target_lang)
        return results, f'{media.key} {episode_label or ""}'.strip()

    def _fallback_episode_label(self, season: int | None, episode: int | None) -> str | None:
        """Return a season/episode label for generic pipeline searches."""
        if season is not None and episode is None:
            return f'Season {season}'
        if season is not None and episode is not None:
            return f'S{int(season):02d}E{int(episode):02d}'
        return None

    @staticmethod
    def _normalize_search_scope(value: str | None) -> str:
        """Normalize category-neutral assistant search scope hints."""
        aliases = {
            "": "default",
            "broad": "default",
            "default": "default",
            "pack_preferred": "bundle_preferred",
            "season_pack_preferred": "bundle_preferred",
            "bundle_preferred": "bundle_preferred",
            "pack_only": "bundle_only",
            "season_pack_only": "bundle_only",
            "bundle_only": "bundle_only",
            "individual_units": "individual_units_only",
            "individual_unit_only": "individual_units_only",
            "individual_units_only": "individual_units_only",
        }
        text = str(value or "default").strip().lower()
        return aliases.get(text, "default")

    def _response(self, media: CategoryItem, category_id: str, season: int | None, episode: int | None, target_lang: str, results: list[SearchResult], query_summary: str, search_scope: str = "default") -> dict[str, Any]:
        """Build the final search_media_torrents response payload."""
        candidates = []
        category = self._context.categories.get(category_id) if self._context.categories else None
        projector = TorrentCandidateProjector()
        for result in (results or []):
            try:
                payload = projector.payload(result, category_id=category_id)
                request_label = self._request_unit_label(season, episode)
                if category and hasattr(category, "unit_descriptor_from_search_result"):
                    descriptor = category.unit_descriptor_from_search_result(result, media, request_label)
                    payload["unit_descriptor"] = descriptor
                    coordinates = descriptor.get("coordinates") if isinstance(descriptor.get("coordinates"), dict) else {}
                    # Transitional compatibility: expose old fields only when
                    # the category descriptor explicitly supplies them.
                    if coordinates.get("season") is not None:
                        payload["season"] = coordinates.get("season")
                    if coordinates.get("episode") is not None:
                        payload["episode"] = coordinates.get("episode")
                if category and hasattr(category, "torrent_bundle_candidate_context"):
                    bundle_context = category.torrent_bundle_candidate_context(result, item=media, unit_label=request_label)
                    if bundle_context:
                        payload["bundle_context"] = bundle_context
                        payload["is_bundle"] = True
                        payload["bundle_scope"] = bundle_context.get("scope")
                        payload["pack_type"] = bundle_context.get("pack_type")
                        payload["bundle_unit_count"] = bundle_context.get("unit_count")
                candidates.append(payload)
            except RecursionError:
                logger.exception("Skipping candidate that recursed during payload projection: %s", getattr(result, "title", result))
            except Exception as exc:
                logger.warning("Skipping candidate with invalid payload data: %s", exc)
        return {
            "query": query_summary,
            "language": target_lang,
            "category_id": category_id,
            "name": media.key,
            "item_id": getattr(media, "key", None) or media.key,
            "display_name": getattr(media, "display_name", None) or media.key,
            "season": season,
            "episode": episode,
            "metadata_snapshot": self._media_identity_snapshot(media, category_id),
            "search_scope": search_scope,
            "candidates": candidates,
        }

    @staticmethod
    def _request_unit_label(season: int | None, episode: int | None) -> str | None:
        """Return an opaque TV-compatible label for category descriptor hooks."""
        if season is None:
            return None
        if episode is None:
            return f"Season {int(season)}"
        return f"S{int(season):02d}E{int(episode):02d}"

    def _media_identity_snapshot(self, media: CategoryItem, category_id: str) -> dict[str, Any]:
        """Return stable item/provider metadata for later download import."""
        metadata = dict(getattr(media, "metadata", {}) or {})
        snapshot: dict[str, Any] = {
            "category_id": category_id,
            "item_id": getattr(media, "key", ""),
            "title": getattr(media, "display_name", None) or getattr(media, "key", ""),
            "tmdb_id": getattr(media, "tmdb_id", None) or metadata.get("tmdb_id"),
            "tvmaze_id": getattr(media, "tvmaze_id", None) or metadata.get("tvmaze_id"),
            "imdb_id": getattr(media, "imdb_id", None) or metadata.get("imdb_id"),
            "tvdb_id": getattr(media, "tvdb_id", None) or metadata.get("tvdb_id"),
            "year": getattr(media, "year", None) or metadata.get("year") or metadata.get("release_year"),
            "first_air_date": metadata.get("first_air_date"),
            "release_date": metadata.get("release_date"),
            "status": metadata.get("status") or getattr(media, "_lifecycle", ""),
        }
        provider = metadata.get("provider")
        if not provider:
            if snapshot.get("tmdb_id"):
                provider = "tmdb"
            elif snapshot.get("tvmaze_id"):
                provider = "tvmaze"
            elif snapshot.get("tvdb_id"):
                provider = "tvdb"
        if provider:
            snapshot["provider"] = provider
        category = self._context.categories.get(category_id) if self._context.categories else None
        if category and hasattr(category, "provider_media_type"):
            snapshot["provider_media_type"] = category.provider_media_type()
        else:
            snapshot["provider_media_type"] = category_id
        snapshot.update({k: v for k, v in metadata.items() if k not in snapshot and k in {"external_id", "provider_id", "original_title", "localized_title", "season_order_type"}})
        return {k: v for k, v in snapshot.items() if v not in (None, "", [], {})}


class TorrentCandidateProjector:
    """Project raw SearchResult objects into UI/LLM candidate dictionaries."""

    def payload(self, result: SearchResult, category_id: str | None = None) -> dict[str, Any]:
        """Return a candidate payload enriched with deterministic quality facts."""
        from src.utils.quality import extract_quality_tags

        tags = extract_quality_tags(result.title)
        # Projection is intentionally category-neutral. Category-owned hooks add
        # unit descriptors and any transitional coordinates in _response().
        return {"title": result.title, "size": result.size, "size_bytes": result.size_bytes, "seeders": result.seeders, "magnet": result.magnet, "source": result.source, "quality_score": result.quality_score, "languages": tags.get("languages", []), "resolution": tags.get("resolution"), "codec": tags.get("codec"), "release_type": tags.get("release_type")}
