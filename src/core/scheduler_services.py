"""Focused services used by MediaScheduler public operations.

MediaScheduler remains the composition root for background jobs.  User-facing
catalog, priority, and torrent-search operations are delegated to these bounded
services so the scheduler class does not accumulate category-specific behavior.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loguru import logger

from src.integrations.slskd_client import SlskdClient
from src.core.models import CategoryItem, DownloadPriority, GenericMediaItem, ScannedLibraryItem, SearchResult
from src.core.library_objects import CanonicalLibraryObjectBuilder
from src.core.categories.search_scope import SearchScopePolicy

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
        search_constraints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Search for torrents for a specific media item via the unified pipeline."""
        settings = self._context.settings_manager.settings
        normalized_name = str(name or "").strip()
        season = self._catalog.safe_structured_unit_int(season)
        episode = self._catalog.safe_structured_unit_int(episode)
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
            requested_category = self._category_for_search_text(normalized_name)
        if not requested_category:
            requested_category = self._category_for_search_scope(normalized_scope)
        initial_category = self._context.categories.get(requested_category) if requested_category and self._context.categories else None
        normalized_name, season, episode = self._category_normalized_search_units(
            initial_category, normalized_name, season, episode, normalized_scope
        )
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
        normalized_scope = self._category_default_search_scope(
            category,
            media=media,
            season=season,
            episode=episode,
            search_scope=normalized_scope,
            language=target_lang,
            settings=settings,
        )
        season = await self._resolve_category_default_season(
            media, category_id, season, episode, normalized_scope, settings,
        )
        constraints = self._normalize_search_constraints(search_constraints)
        companion_query = self._preliminary_query_summary(media, category, season, episode, normalized_scope)
        logger.info(
            f"Starting Soulseek companion task before torrent search: category={category_id} "
            f"query={companion_query!r} scope={normalized_scope!r}"
        )
        companion_task = asyncio.create_task(
            self._soulseek_companion_search(
                query_summary=companion_query,
                media=media,
                category_id=category_id,
                search_scope=normalized_scope,
                settings=settings,
                season=season,
                episode=episode,
                language=target_lang,
                search_constraints=constraints,
            )
        )
        torrent_error: str | None = None
        try:
            results, query_summary = await self._search(media, category_id, season, episode, target_lang, settings, normalized_scope, constraints)
        except Exception as exc:
            # A torrent-side provider/tool failure must not abort the whole
            # assistant turn.  Soulseek is an independent companion source and
            # may still produce usable candidates; return a typed degraded
            # torrent block instead of raising back through the websocket.
            logger.exception("Torrent search failed for %s; preserving companion fallback result.", media.key)
            results = []
            query_summary = self._preliminary_query_summary(media, category, season, episode, normalized_scope)
            torrent_error = str(exc)
        response = self._response(media, category_id, season, episode, target_lang, results, query_summary, normalized_scope, settings=settings, search_constraints=constraints)
        if torrent_error:
            response["torrent_status"] = "error"
            response["torrent_error"] = torrent_error
            response["recoverable"] = True
            response.setdefault("next_actions", []).append({
                "action": "review_companion_soulseek_or_retry",
                "reason": "Torrent search failed before returning candidates; fallback source results are still included when available.",
            })
        if constraints:
            response["search_constraints"] = constraints
        response["source_strategy"] = self._source_strategy(category_id, normalized_name, normalized_scope, settings)
        try:
            response["companion_soulseek"] = await companion_task
        except Exception as exc:
            logger.warning("Soulseek companion task failed for %s: %s", media.key, exc)
            response["companion_soulseek"] = {"enabled": True, "status": "error", "candidate_count": 0, "candidates": [], "error": str(exc)}
        return response

    def _preliminary_query_summary(self, media: CategoryItem, category: object | None, season: int | None, episode: int | None, search_scope: str | None = None) -> str:
        """Return a stable search summary before torrent fanout finishes.

        Soulseek must not wait behind a long Jackett ladder.  This early query
        lets the companion backend run in parallel while the category-owned
        torrent search explores its own candidate schemas.
        """
        label = self._request_unit_label(category, season, episode, search_scope=search_scope)
        return f"{getattr(media, 'key', '')} {label or ''}".strip()

    def _normalize_search_constraints(self, constraints: dict[str, Any] | None) -> dict[str, Any]:
        """Normalize optional user-facing search constraints for category hooks."""
        if not isinstance(constraints, dict):
            return {}
        normalized: dict[str, Any] = {}
        for key in ("target_size_gb", "max_size_gb", "min_size_gb", "current_size_gb"):
            value = constraints.get(key)
            if value in (None, ""):
                continue
            try:
                gb = float(value)
            except (TypeError, ValueError):
                continue
            if gb > 0:
                normalized[key] = gb
                normalized[key.replace("_gb", "_mb")] = gb * 1024.0
        for key in ("target_bitrate_kbps", "preferred_bitrate_kbps", "max_bitrate_kbps", "current_bitrate_kbps"):
            value = constraints.get(key)
            if value in (None, ""):
                continue
            try:
                kbps = float(value)
            except (TypeError, ValueError):
                continue
            if kbps > 0:
                normalized[key] = kbps
        for key in ("preferred_resolution", "required_resolution"):
            value = str(constraints.get(key) or "").strip().lower()
            if value:
                normalized[key] = value
        for key in ("smaller_than_current", "preserve_resolution", "prefer_current_resolution"):
            if constraints.get(key) is not None:
                normalized[key] = bool(constraints.get(key))
        mode = str(constraints.get("size_mode") or "").strip().lower()
        if mode:
            normalized["size_mode"] = mode
        return normalized

    def _source_strategy(self, category_id: str, name: str, search_scope: str, settings: object) -> dict[str, Any]:
        """Return the effective source strategy for the current category search."""
        cfg = getattr(settings, "soulseek", None)
        preference = getattr(cfg, "download_preference", "torrent_first") if cfg else "torrent_first"
        category = self._context.categories.get(category_id) if self._context.categories else None
        category_strategy: dict[str, Any] = {}
        strategy_hook = getattr(category, "soulseek_source_strategy", None)
        if callable(strategy_hook):
            try:
                category_strategy = strategy_hook(
                    item_name=name,
                    search_scope=search_scope,
                    settings=settings,
                    default_preference=preference,
                ) or {}
                preference = str(category_strategy.get("download_preference") or preference)
            except Exception as exc:
                logger.debug("Category Soulseek source strategy failed for %s: %s", category_id, exc)
        return {
            "parallel_search_enabled": bool(getattr(cfg, "parallel_search_enabled", False)) if cfg else False,
            "download_preference": preference,
            "category_strategy": {k: v for k, v in category_strategy.items() if k != "download_preference"},
            "torrent_queue_tool": "queue_download",
            "soulseek_queue_tool": "enqueue_soulseek_download",
            "note": "Evaluate torrent and Soulseek candidates through the active category's candidate rules; use the queue tool that matches the selected backend.",
        }

    async def _soulseek_companion_search(
        self,
        *,
        query_summary: str,
        media: CategoryItem,
        category_id: str,
        search_scope: str,
        settings: object,
        season: int | None = None,
        episode: int | None = None,
        language: str | None = None,
        search_constraints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run a bounded Soulseek companion search alongside torrent search.

        The result is presented next to torrent candidates without mixing backend
        semantics.  Queueing remains explicit through enqueue_soulseek_download.
        """
        cfg = getattr(settings, "soulseek", None)
        if not cfg or not getattr(cfg, "enabled", False):
            logger.debug(f"Soulseek companion search skipped: disabled category={category_id} query={query_summary!r}")
            return {"enabled": False, "status": "disabled", "candidate_count": 0, "candidates": []}
        enabled_categories = {str(cat).strip().lower() for cat in (getattr(cfg, "search_enabled_categories", []) or []) if str(cat).strip()}
        if enabled_categories and str(category_id or "").lower() not in enabled_categories:
            logger.info(f"Soulseek companion search skipped: category_disabled category={category_id} enabled_categories={sorted(enabled_categories)} query={query_summary!r}")
            return {"enabled": True, "status": "category_disabled", "category_id": category_id, "candidate_count": 0, "candidates": [], "enabled_categories": sorted(enabled_categories)}
        if not getattr(cfg, "parallel_search_enabled", True):
            logger.info(f"Soulseek companion search skipped: parallel_disabled category={category_id} query={query_summary!r}")
            return {"enabled": True, "status": "parallel_disabled", "candidate_count": 0, "candidates": []}
        if not getattr(cfg, "api_configured", False):
            logger.info(f"Soulseek companion search skipped: not_configured category={category_id} query={query_summary!r}")
            return {"enabled": True, "status": "not_configured", "candidate_count": 0, "candidates": [], "error": "slskd is not configured."}
        if getattr(cfg, "managed", True):
            if not getattr(cfg, "soulseek_credentials_configured", False):
                logger.info(f"Soulseek companion search skipped: needs_credentials category={category_id} query={query_summary!r}")
                return {
                    "enabled": True,
                    "status": "needs_credentials",
                    "account_status": getattr(cfg, "account_status", "needs_credentials"),
                    "candidate_count": 0,
                    "candidates": [],
                    "error": "Soulseek username and password are required before searching.",
                }
            if str(getattr(cfg, "account_status", "")).lower() == "auth_failed":
                logger.info(f"Soulseek companion search skipped: auth_failed category={category_id} query={query_summary!r}")
                return {
                    "enabled": True,
                    "status": "auth_failed",
                    "account_status": getattr(cfg, "account_status", "auth_failed"),
                    "candidate_count": 0,
                    "candidates": [],
                    "error": getattr(cfg, "account_status_message", "Soulseek rejected these credentials."),
                }
        category = self._context.categories.get(category_id) if self._context.categories else None
        unit_label = self._request_unit_label(category, season, episode, search_scope=search_scope)
        context = self._category_workflow_context(settings, search_constraints)
        query_builder = getattr(category, "build_soulseek_search_queries", None)
        if callable(query_builder):
            queries = query_builder(
                query_summary,
                media,
                unit_label=unit_label,
                language=language,
                search_scope=search_scope,
                context=context,
            )
        else:
            queries = [query_summary] if str(query_summary or "").strip() else []
        queries = [str(query).strip() for query in (queries or []) if str(query or "").strip()]
        if not queries:
            logger.debug(f"Soulseek companion search skipped: empty_query category={category_id} query={query_summary!r}")
            return {"enabled": True, "status": "empty_query", "candidate_count": 0, "candidates": []}
        limit_hook = getattr(category, "soulseek_search_limit", None)
        if callable(limit_hook):
            try:
                raw_limit = int(limit_hook(item=media, unit_label=unit_label, search_scope=search_scope, context=context) or 80)
            except Exception:
                raw_limit = 80
        else:
            raw_limit = 80
        raw_limit = max(10, min(raw_limit, 300))
        result: dict[str, Any] = {}
        raw_candidates: list[dict[str, Any]] = []
        candidates: list[dict[str, Any]] = []
        tried_queries: list[str] = []
        try:
            for query in queries:
                tried_queries.append(query)
                logger.info(f"Soulseek companion search: category={category_id} query={query!r} raw_limit={raw_limit}")
                result = await SlskdClient(cfg).search(query, max_results=raw_limit)
                candidate_rows = result.get("candidates") if isinstance(result, dict) else []
                if isinstance(candidate_rows, list) and candidate_rows:
                    raw_candidates = candidate_rows
                    ranker = getattr(category, "rank_soulseek_search_results", None)
                    if callable(ranker):
                        candidates = await ranker(
                            raw_candidates,
                            item=media,
                            language=language,
                            unit_label=unit_label,
                            search_scope=search_scope,
                            context=context,
                        )
                    else:
                        candidates = raw_candidates
                    if candidates:
                        break
        except Exception as exc:
            logger.warning("Soulseek companion search failed for %s: %s", queries, exc)
            return {"enabled": True, "status": "error", "candidate_count": 0, "candidates": [], "error": str(exc), "queries": tried_queries}
        logger.info(
            f"Soulseek companion search complete: category={category_id} queries={tried_queries} "
            f"raw_candidates={len(raw_candidates)} category_candidates={len(candidates)}"
        )
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
            "status": ("ready" if candidates else ("category_filtered_empty" if raw_candidates and isinstance(result, dict) and result.get("ok") is True else ("account_not_ready" if isinstance(result, dict) and any(token in str(result.get("error") or "").lower() for token in ("not logged in", "not connected", "connect to server")) else "error"))),
            "source": "slskd",
            "query": tried_queries[0] if tried_queries else "",
            "queries": tried_queries,
            "raw_candidate_count": len(raw_candidates),
            "category_filtered_count": max(0, len(raw_candidates) - len(candidates)),
            "candidate_count": len(candidates),
            "candidates": candidates[:10],
            "filtering": result.get("filtering") if isinstance(result, dict) else {},
            "raw_response_count": result.get("raw_response_count") if isinstance(result, dict) else None,
            "raw_file_count": result.get("raw_file_count") if isinstance(result, dict) else None,
            "queueing_note": "Use enqueue_soulseek_download with candidate_id/result_set_id. These Soulseek rows have already passed the active category's filtering/ranking hook.",
            "category_id": category_id,
            "unit_label": unit_label,
            "category_filter_note": ("Soulseek returned raw rows, but none matched this category's file/quality/language rules." if raw_candidates and not candidates else ""),
            "error": result.get("error") if isinstance(result, dict) else None,
        }

    def _category_for_search_text(self, name: str) -> str | None:
        """Resolve a category from router/parser evidence without parsing units in scheduler core."""
        registry = self._context.categories
        if not registry:
            return None
        try:
            routed = registry.resolve_from_text(name) if hasattr(registry, "resolve_from_text") else None
            if routed is not None:
                return str(getattr(routed, "category_id", "") or "") or None
        except Exception:
            pass
        try:
            classified = registry.classify(name) if hasattr(registry, "classify") else None
            if classified:
                return str(getattr(classified[0], "category_id", "") or "") or None
        except Exception:
            pass
        return None

    @staticmethod
    def _category_normalized_search_units(
        category: object | None,
        name: str,
        season: int | None,
        episode: int | None,
        search_scope: str,
    ) -> tuple[str, int | None, int | None]:
        """Let the owning category extract any structured unit words from a title."""
        hook = getattr(category, "normalize_agent_search_units_from_name", None)
        if not callable(hook):
            return name, season, episode
        try:
            normalized = hook(name, season=season, episode=episode, search_scope=search_scope)
        except Exception:
            return name, season, episode
        if not isinstance(normalized, tuple) or len(normalized) != 3:
            return name, season, episode
        normalized_name, normalized_season, normalized_episode = normalized
        return str(normalized_name or name).strip() or name, normalized_season, normalized_episode

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

    def _category_default_search_scope(
        self,
        category: object | None,
        *,
        media: CategoryItem,
        season: int | None,
        episode: int | None,
        search_scope: str,
        language: str | None,
        settings: object,
    ) -> str:
        """Allow the owning category to refine an omitted search phase."""
        hook = getattr(category, "default_agent_search_scope", None)
        if not callable(hook):
            return search_scope
        try:
            context = self._category_workflow_context(settings)
            resolved = hook(
                media,
                season=season,
                episode=episode,
                search_scope=search_scope,
                language=language,
                context=context,
            )
            normalized = self._normalize_search_scope(resolved)
            if normalized != search_scope:
                logger.info(
                    "Category default search scope refined: category=%s item=%r %r -> %r",
                    getattr(category, "category_id", None), getattr(media, "key", ""), search_scope, normalized,
                )
            return normalized
        except Exception as exc:
            logger.debug("Category default search scope hook failed for %s: %s", getattr(media, "key", ""), exc)
            return search_scope

    def _category_for_search_scope(self, search_scope: str | None) -> str | None:
        """Resolve a category for category-neutral pack search scopes."""
        if not SearchScopePolicy.is_bundle_scope(search_scope):
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
        """Create an in-memory item through category-owned routing when possible."""
        registry = self._context.categories
        if not category_id and registry:
            try:
                routed = registry.resolve_from_text(normalized_name) if hasattr(registry, "resolve_from_text") else None
                if routed is not None:
                    category_id = str(getattr(routed, "category_id", "") or "") or None
            except Exception as exc:
                logger.debug("Category router could not resolve %s: %s", normalized_name, exc)
        if not category_id and registry:
            try:
                classified = registry.classify(normalized_name) if hasattr(registry, "classify") else None
                if classified:
                    category_id = str(getattr(classified[0], "category_id", "") or "") or None
            except Exception as exc:
                logger.debug("Category parser could not classify %s: %s", normalized_name, exc)
        if not category_id:
            category_id = "media"
        category = registry.get(category_id) if registry else None
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
        if not SearchScopePolicy.is_bundle_scope(search_scope):
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

    def _category_workflow_context(self, settings: object, search_constraints: dict[str, Any] | None = None):
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
            category_registry=self._context.categories,
            search_constraints=search_constraints or {},
        )

    async def _search(self, media: CategoryItem, category_id: str, season: int | None, episode: int | None, target_lang: str, settings: object, search_scope: str | None = None, search_constraints: dict[str, Any] | None = None) -> tuple[list[SearchResult], str]:
        """Run category-owned search with safe fallback to the pipeline."""
        category = self._context.categories.get(category_id) if self._context.categories else None
        if category and hasattr(category, "search_agent_candidates"):
            try:
                context = self._category_workflow_context(settings, search_constraints)
                return await category.search_agent_candidates(
                    media, season=season, episode=episode, language=target_lang,
                    search_scope=search_scope, context=context,
                )
            except RecursionError:
                logger.exception("Category-owned search recursed for %s; falling back to pipeline.", media.key)
            except Exception as exc:
                logger.warning("Category-owned search failed for %s: %s; falling back to pipeline.", media.key, exc)
        return await self._fallback_search(media, category, season, episode, target_lang)

    async def _fallback_search(self, media: CategoryItem, category: object | None, season: int | None, episode: int | None, target_lang: str) -> tuple[list[SearchResult], str]:
        """Run the generic search pipeline with a category-owned unit label."""
        unit_label = self._request_unit_label(category, season, episode)
        results = await self._context.pipeline.run_search(media, unit_label, mode='llm', language=target_lang)
        return results, f'{media.key} {unit_label or ""}'.strip()


    @staticmethod
    def _normalize_search_scope(value: str | None) -> str:
        """Normalize category-neutral assistant search scope hints."""
        if str(value or "").strip().lower() in {"individual_units", "individual_unit_only"}:
            return SearchScopePolicy.INDIVIDUAL_UNITS_ONLY
        if str(value or "").strip().lower() == "broad":
            return SearchScopePolicy.DEFAULT
        return SearchScopePolicy.normalize(value)

    def _response(
        self,
        media: CategoryItem,
        category_id: str,
        season: int | None,
        episode: int | None,
        target_lang: str,
        results: list[SearchResult],
        query_summary: str,
        search_scope: str = "default",
        *,
        settings: object | None = None,
        search_constraints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build the final search_media_torrents response payload."""
        candidates = []
        category = self._context.categories.get(category_id) if self._context.categories else None
        context = self._category_workflow_context(settings, search_constraints) if settings is not None else None
        response_facts = self._category_response_facts(
            category,
            item=media,
            season=season,
            episode=episode,
            query_summary=query_summary,
            search_scope=search_scope,
            context=context,
        )
        projector = TorrentCandidateProjector()
        request_label = self._request_unit_label(category, season, episode, search_scope=search_scope)
        for result in (results or []):
            try:
                payload = projector.payload(result, category_id=category_id)
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
                if category and hasattr(category, "search_candidate_quality_facts"):
                    facts = category.search_candidate_quality_facts(result, item=media, unit_label=request_label, context=context)
                    if isinstance(facts, dict):
                        payload.update({k: v for k, v in facts.items() if v not in (None, "", [], {})})
                payload = self._category_annotated_candidate_payload(
                    category,
                    payload,
                    result,
                    item=media,
                    unit_label=request_label,
                    season=season,
                    episode=episode,
                    search_scope=search_scope,
                    response_facts=response_facts,
                    context=context,
                )
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
            **response_facts,
            "metadata_snapshot": self._media_identity_snapshot(media, category_id),
            "search_scope": search_scope,
            "candidates": candidates,
        }

    def _category_response_facts(
        self,
        category: object | None,
        *,
        item: CategoryItem,
        season: int | None,
        episode: int | None,
        query_summary: str,
        search_scope: str,
        context: object | None,
    ) -> dict[str, Any]:
        """Return category-owned response facts without parsing domain names in scheduler."""
        hook = getattr(category, "agent_search_response_facts", None)
        if not callable(hook):
            return {}
        try:
            facts = hook(
                item=item,
                season=season,
                episode=episode,
                query_summary=query_summary,
                search_scope=search_scope,
                context=context,
            )
        except Exception as exc:
            logger.debug("Category response fact hook failed for %s: %s", getattr(item, "key", ""), exc)
            return {}
        return facts if isinstance(facts, dict) else {}

    def _category_annotated_candidate_payload(
        self,
        category: object | None,
        payload: dict[str, Any],
        result: SearchResult,
        *,
        item: CategoryItem,
        unit_label: str | None,
        season: int | None,
        episode: int | None,
        search_scope: str,
        response_facts: dict[str, Any],
        context: object | None,
    ) -> dict[str, Any]:
        """Let the owning category add final candidate payload annotations."""
        hook = getattr(category, "annotate_agent_search_candidate_payload", None)
        if not callable(hook):
            return payload
        try:
            annotated = hook(
                payload,
                result,
                item=item,
                unit_label=unit_label,
                season=season,
                episode=episode,
                search_scope=search_scope,
                response_facts=response_facts,
                context=context,
            )
            return annotated if isinstance(annotated, dict) else payload
        except Exception as exc:
            logger.debug("Category candidate annotation hook failed for %s: %s", getattr(item, "key", ""), exc)
            return payload

    def _request_unit_label(
        self,
        category: object | None,
        season: int | None,
        episode: int | None,
        *,
        search_scope: str | None = None,
    ) -> str | None:
        """Return a category-owned label for transitional structured unit args."""
        if season is None and episode is None:
            return None
        hook = getattr(category, "agent_unit_label_from_args", None)
        if callable(hook):
            try:
                label = hook(season=season, episode=episode, search_scope=search_scope)
                text = str(label or "").strip()
                if text:
                    return text
            except Exception:
                return None
        return None

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
