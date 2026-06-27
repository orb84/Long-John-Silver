"""Queue-download orchestration services for the download agent tools.

The public ``queue_download`` tool remains a thin facade.  Candidate cache
resolution, batch expansion, priority assignment, and scheduler calls live here
so future queueing policy changes can be tested without editing tool schemas.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loguru import logger

from src.core.models import DownloadImportContext, DownloadPriority, ToolExecutionContext

if TYPE_CHECKING:
    from src.core.database import Database
    from src.core.scheduler import MediaScheduler


@dataclass
class QueueDownloadRequest:
    """Normalized arguments for one queue_download invocation.

    Construct this from raw tool arguments before performing any side effects.
    Extension code should add optional fields here and keep the tool schema
    backward-compatible by accepting older direct magnet and option-index calls.
    """

    session_id: str
    magnet: str | None
    name: str | None
    season: int | None
    episode: int | None
    option_index: int | None
    candidate_ids: list[str]
    result_set_id: str | None
    category_id: str
    estimated_size_bytes: int | None
    selected_torrent_title: str
    selected_source_seeders: int | None
    requested_priority: str
    raw_arguments: dict[str, Any]

    @classmethod
    def from_arguments(cls, arguments: dict[str, Any], context: ToolExecutionContext) -> "QueueDownloadRequest":
        """Build a normalized request from LLM/runtime arguments.

        The method centralizes legacy compatibility: a single ``candidate_id``
        is promoted to ``candidate_ids`` and a string ``candidate_ids`` value is
        accepted for older planner outputs.
        """
        candidate_id = arguments.get("candidate_id")
        candidate_ids = arguments.get("candidate_ids") or []
        if isinstance(candidate_ids, str):
            candidate_ids = [candidate_ids]
        if candidate_id and not candidate_ids:
            candidate_ids = [candidate_id]
        return cls(
            session_id=context.session_id or "default",
            magnet=arguments.get("magnet"),
            name=arguments.get("name"),
            season=arguments.get("season"),
            episode=arguments.get("episode"),
            option_index=arguments.get("option_index"),
            candidate_ids=[str(value) for value in candidate_ids],
            result_set_id=str(arguments.get("result_set_id")) if arguments.get("result_set_id") else None,
            category_id=arguments.get("category_id") or "",
            estimated_size_bytes=arguments.get("estimated_size_bytes"),
            selected_torrent_title=arguments.get("torrent_title") or arguments.get("title") or "",
            selected_source_seeders=arguments.get("source_seeders"),
            requested_priority=str(arguments.get("priority") or "high").lower(),
            raw_arguments=arguments,
        )

    def priority_for_batch_index(self, batch_index: int, total_to_queue: int) -> str:
        """Return the priority string for an item in an ordered batch.

        When the user did not explicitly set priority, early units get scarce
        active slots first while later units remain queued at lower priority.
        """
        if total_to_queue <= 1 or "priority" in self.raw_arguments:
            return self.requested_priority
        if batch_index == 0:
            return "high"
        if batch_index < 3:
            return "normal"
        return "low"


class DownloadPriorityParser:
    """Convert user/tool priority strings into domain enum values."""

    _MAP = {
        "high": DownloadPriority.HIGH,
        "normal": DownloadPriority.NORMAL,
        "low": DownloadPriority.LOW,
    }

    def parse(self, value: str | None) -> DownloadPriority:
        """Return the DownloadPriority for a possibly invalid string.

        Invalid or empty values intentionally default to HIGH for manual chat
        queueing, matching the historical queue_download behavior.
        """
        return self._MAP.get(str(value or "").lower(), DownloadPriority.HIGH)


class CachedCandidateResolver:
    """Resolve LLM-visible candidate IDs or option indexes from cached result sets."""

    def __init__(self, database: "Database", categories: object | None = None) -> None:
        """Create a resolver backed by the result-set cache repository.

        Args:
            database: Result-set cache repository owner.
            categories: Optional category registry used only for category-owned
                ordering of resolved batch candidates. This must be injected by
                QueueDownloadService; the resolver must never assume a global
                registry exists.
        """
        self._database = database
        self._categories = categories

    async def resolve_candidate_batch(self, request: QueueDownloadRequest) -> list[dict[str, Any]] | dict[str, str]:
        """Resolve all requested candidate IDs and expand recommended batches.

        Returns either a sorted list of candidate/cache entries or an error dict
        with an ``error`` key.  Tool facades can pass error dicts through without
        leaking cache internals to the user.
        """
        from src.utils.candidate_ids import find_candidate_in_cached_sets, load_result_set

        cache_data = None
        resolved: list[dict[str, Any]] = []
        if request.result_set_id:
            cache_data = await load_result_set(
                self._database,
                session_id=request.session_id,
                result_set_id=request.result_set_id,
            )
        for candidate_id in request.candidate_ids:
            candidate = self._candidate_from_cache(cache_data, candidate_id)
            if not candidate:
                cache_data, candidate = await find_candidate_in_cached_sets(
                    self._database,
                    session_id=request.session_id,
                    candidate_id=candidate_id,
                    result_set_id=request.result_set_id,
                )
            if not candidate:
                return {"error": f"Candidate {candidate_id} was not found in recent search results. Please run the search again."}
            resolved.append({"candidate_id": candidate_id, "candidate": candidate, "cache_data": cache_data or {}})
        return self._sorted_candidates(self._expand_recommended_batch(resolved))

    async def apply_option_index(self, request: QueueDownloadRequest) -> QueueDownloadRequest | dict[str, str]:
        """Resolve a legacy one-based option_index into direct queue fields."""
        from src.utils.candidate_ids import load_result_set

        cache_data = await load_result_set(
            self._database,
            session_id=request.session_id,
            result_set_id=request.result_set_id,
        )
        if not cache_data:
            return {"error": f"No search results cached for session {request.session_id}. Please perform a search first."}
        candidates = cache_data.get("candidates", [])
        candidate = next((entry for entry in candidates if entry.get("index") == request.option_index), None)
        if not candidate:
            return {"error": f"Invalid option_index {request.option_index}. Available options are 1 to {len(candidates)}."}
        request.magnet = candidate.get("magnet")
        request.name = cache_data.get("name") or request.name
        request.season = self._first_present(request.season, candidate.get("season"), cache_data.get("season"))
        request.episode = self._first_present(request.episode, candidate.get("episode"), cache_data.get("episode"))
        request.category_id = request.category_id or cache_data.get("category_id") or candidate.get("category_id") or ""
        request.estimated_size_bytes = request.estimated_size_bytes or candidate.get("size_bytes")
        request.selected_torrent_title = request.selected_torrent_title or candidate.get("title") or ""
        request.selected_source_seeders = candidate.get("seeders")
        return request

    def _candidate_from_cache(self, cache_data: dict[str, Any] | None, candidate_id: str) -> dict[str, Any] | None:
        """Return a candidate from a loaded cache set by stable ID."""
        if not cache_data:
            return None
        return next((entry for entry in cache_data.get("candidates", []) if entry.get("candidate_id") == candidate_id), None)

    def _expand_recommended_batch(self, resolved: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Expand a single selected candidate into a category-recommended batch."""
        if not resolved:
            return []
        active_cache = resolved[0].get("cache_data") or {}
        recommendation = active_cache.get("batch_recommendation") or {}
        recommended_ids = [str(value) for value in recommendation.get("candidate_ids") or []]
        auto_expand = bool(recommendation.get("auto_expand_single_selection"))
        if len(resolved) != 1 or not auto_expand or str(resolved[0]["candidate_id"]) not in set(recommended_ids):
            return resolved
        expanded: list[dict[str, Any]] = []
        for candidate_id in recommended_ids:
            candidate = self._candidate_from_cache(active_cache, candidate_id)
            if candidate:
                expanded.append({"candidate_id": candidate_id, "candidate": candidate, "cache_data": active_cache})
        if expanded:
            logger.info(
                "Expanding single queue_download candidate %s into recommended batch of %s candidate(s)",
                resolved[0]["candidate_id"],
                len(expanded),
            )
            return expanded
        return resolved

    def _sorted_candidates(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return candidates in category-owned queue order when available."""
        category = self._category_for_entries(entries)
        if category and hasattr(category, "sort_cached_download_candidates"):
            try:
                return category.sort_cached_download_candidates(entries, self._request_context_from_entries(entries))
            except Exception as exc:
                logger.debug("Category candidate ordering failed; preserving cache order: %s", exc)
        return entries

    def _category_for_entries(self, entries: list[dict[str, Any]]) -> object | None:
        """Resolve the category for cached candidate entries without guessing domains."""
        if not self._categories or not entries:
            return None
        first = entries[0]
        candidate = first.get("candidate") or {}
        cache = first.get("cache_data") or {}
        category_id = candidate.get("category_id") or cache.get("category_id") or ""
        if not category_id:
            return None
        try:
            return self._categories.get(category_id)
        except Exception:
            return None

    @staticmethod
    def _request_context_from_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
        """Return small opaque context passed to category queue hooks."""
        first = entries[0] if entries else {}
        cache = first.get("cache_data") or {}
        return {"category_id": cache.get("category_id"), "season": cache.get("season"), "episode": cache.get("episode")}

    def _safe_int(self, value: Any) -> int:
        """Convert a candidate field to int without letting bad cache data crash."""
        try:
            return int(value or 0)
        except Exception:
            return 0

    def _first_present(self, *values: Any) -> Any:
        """Return the first value that is not None."""
        for value in values:
            if value is not None:
                return value
        return None


class QueueDownloadService:
    """Coordinate all queue_download side effects through a small public API."""

    def __init__(self, scheduler: "MediaScheduler", database: "Database" | None = None) -> None:
        """Create the queueing service with a scheduler and optional cache database."""
        self._scheduler = scheduler
        self._database = database
        self._priority_parser = DownloadPriorityParser()
        self._categories = getattr(scheduler, "_categories", None)

    async def queue(self, arguments: dict[str, Any], context: ToolExecutionContext) -> object:
        """Resolve the requested queue operation and return a tool-safe result."""
        request = QueueDownloadRequest.from_arguments(arguments, context)
        database = self._database or getattr(self._scheduler, "database", None)
        if request.candidate_ids:
            return await self._queue_candidate_ids(request, database)
        if request.option_index is not None:
            request_or_error = await self._request_from_option_index(request, database)
            if isinstance(request_or_error, dict):
                return request_or_error
            request = request_or_error
        return await self._queue_direct_request(request)

    async def _queue_candidate_ids(self, request: QueueDownloadRequest, database: "Database" | None) -> object:
        """Queue one or more stable candidates resolved from the result cache."""
        if not database:
            return {"error": "Database not available to resolve cached torrent candidates"}
        try:
            entries_or_error = await CachedCandidateResolver(database, self._categories).resolve_candidate_batch(request)
            if isinstance(entries_or_error, dict):
                return entries_or_error
            return await self._queue_resolved_entries(request, entries_or_error)
        except Exception as exc:
            logger.error(f"Failed to resolve cached candidate batch: {exc}")
            return {"error": f"Failed to resolve cached candidate batch: {str(exc)}"}

    async def _request_from_option_index(
        self,
        request: QueueDownloadRequest,
        database: "Database" | None,
    ) -> QueueDownloadRequest | dict[str, str]:
        """Return a direct request resolved from a legacy option index."""
        if not database:
            return {"error": "Database not available to resolve cached torrent candidate"}
        try:
            return await CachedCandidateResolver(database, self._categories).apply_option_index(request)
        except Exception as exc:
            logger.error(f"Failed to resolve cached candidate: {exc}")
            return {"error": f"Failed to resolve cached candidate: {str(exc)}"}

    async def _queue_resolved_entries(self, request: QueueDownloadRequest, entries: list[dict[str, Any]]) -> dict[str, Any]:
        """Queue every resolved cached candidate and aggregate partial errors.

        Jackett/download URLs can expire between search and queue. When that
        happens, try the next cached candidate for the same concrete unit before
        reporting the unit as failed.
        """
        queued: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        total_to_queue = len(entries)
        for batch_index, entry in enumerate(entries):
            candidate_id = str(entry["candidate_id"])
            if candidate_id in seen_ids:
                continue
            seen_ids.add(candidate_id)
            result = await self._queue_one_entry(request, entry, batch_index, total_to_queue)
            if result.get("error"):
                fallback = await self._queue_fallback_for_failed_entry(
                    request, entry, seen_ids, batch_index, total_to_queue, result["error"],
                )
                if fallback and not fallback.get("error"):
                    queued.append(fallback)
                else:
                    errors.append(self._queue_error_receipt(entry, result["error"]))
            else:
                queued.append(result)
        if not queued:
            return {"error": "No candidates were queued.", "errors": errors}
        return self._batch_result(queued, errors)

    async def _queue_one_entry(
        self,
        request: QueueDownloadRequest,
        entry: dict[str, Any],
        batch_index: int,
        total_to_queue: int,
    ) -> dict[str, Any]:
        """Queue one resolved candidate and return either a receipt or error."""
        payload = self._candidate_queue_payload(request, entry, batch_index, total_to_queue)
        if payload.get("error"):
            return {"error": payload["error"]}
        try:
            result = await self._scheduler.queue_download(**payload["scheduler_kwargs"])
            validation_error = self._queue_result_error(result)
            if validation_error:
                return {"error": validation_error, "raw_result": result}
            await self._record_candidate_quality_choice(entry, payload)
            return self._queued_entry_receipt(entry, payload["candidate_name"], result, entry["candidate"], payload)
        except Exception as exc:
            logger.error(f"Queue download batch item error for {entry['candidate_id']}: {exc}")
            return {"error": str(exc)}

    async def _record_candidate_quality_choice(self, entry: dict[str, Any], payload: dict[str, Any]) -> None:
        """Persist a per-item bitrate target when the user queues a chosen candidate.

        Selecting a candidate is an explicit preference signal.  For a new show
        with no bitrate target yet, store the selected candidate's estimated
        bitrate on the tracked item so later episodes preserve the same
        quality/size tradeoff instead of re-asking or defaulting to 720p.
        """
        candidate = entry.get("candidate") or {}
        cache = entry.get("cache_data") or {}
        try:
            bitrate = int(candidate.get("estimated_bitrate_kbps") or 0)
        except (TypeError, ValueError):
            bitrate = 0
        if bitrate <= 0:
            return
        category_id = str(cache.get("category_id") or candidate.get("category_id") or "")
        item_id = str(cache.get("item_id") or cache.get("name") or payload.get("candidate_name") or "")
        if not category_id or not item_id:
            return
        settings_manager = getattr(self._scheduler, "_settings_manager", None)
        settings = getattr(settings_manager, "settings", None) if settings_manager else None
        if settings is None:
            return
        changed = False
        for item in getattr(settings, "tracked_items", []) or []:
            item_category = getattr(item, "item_type", getattr(item, "category_id", category_id))
            if str(item_category) != category_id or str(getattr(item, "key", "")) != item_id:
                continue
            quality = getattr(item, "quality", None)
            if quality is None:
                break
            existing = getattr(quality, "preferred_bitrate_kbps", None)
            if existing:
                break
            try:
                quality.preferred_bitrate_kbps = bitrate
                max_existing = getattr(quality, "max_bitrate_kbps", None)
                if not max_existing:
                    quality.max_bitrate_kbps = int(bitrate * 1.2)
                changed = True
            except Exception:
                changed = False
            break
        if not changed:
            return
        try:
            coordinator_factory = getattr(self._scheduler, "category_item_coordinator", None)
            if callable(coordinator_factory):
                await coordinator_factory().update_item(category_id, item_id, quality=quality)
            else:
                settings_manager.save(settings)
        except Exception as exc:
            logger.debug(f"Could not persist selected candidate bitrate preference for {category_id}/{item_id}: {exc}")
        logger.info(f"Recorded selected bitrate preference for {category_id}/{item_id}: {bitrate} kbps")


    async def _queue_fallback_for_failed_entry(
        self,
        request: QueueDownloadRequest,
        entry: dict[str, Any],
        seen_ids: set[str],
        batch_index: int,
        total_to_queue: int,
        original_error: str,
    ) -> dict[str, Any] | None:
        """Try alternate cached candidates for the same unit after a queue error."""
        cache = entry.get("cache_data") or {}
        original = entry.get("candidate") or {}
        category = self._category_for_request(request, cache, original)
        original_descriptor = self._candidate_unit_descriptor(original, request, cache)
        season = self._candidate_unit(original, request.season, cache.get("season"), "season")
        episode = self._candidate_unit(original, request.episode, cache.get("episode"), "episode")
        original_id = str(entry.get("candidate_id") or "")
        original_magnet = str(original.get("magnet") or "")

        for alt in cache.get("candidates", []) or []:
            alt_id = str(alt.get("candidate_id") or "")
            if not alt_id or alt_id in seen_ids or alt_id == original_id:
                continue
            alt_descriptor = self._candidate_unit_descriptor(alt, request, cache)
            same_unit = False
            if category and hasattr(category, "candidates_represent_same_unit"):
                try:
                    same_unit = category.candidates_represent_same_unit(
                        {**original, "unit_descriptor": original_descriptor},
                        {**alt, "unit_descriptor": alt_descriptor},
                        {"season": request.season, "episode": request.episode},
                    )
                except Exception:
                    same_unit = False
            if not same_unit and alt_descriptor and original_descriptor:
                same_unit = alt_descriptor.get("stable_key") == original_descriptor.get("stable_key")
            if not same_unit:
                # Final compatibility fallback for cached rows created before
                # unit descriptors existed. Do not use this path for new rows.
                alt_season = self._candidate_unit(alt, request.season, cache.get("season"), "season")
                alt_episode = self._candidate_unit(alt, request.episode, cache.get("episode"), "episode")
                if alt_season != season or alt_episode != episode:
                    continue
            if not alt.get("magnet"):
                continue
            if original_magnet and str(alt.get("magnet") or "") == original_magnet:
                continue

            seen_ids.add(alt_id)
            alt_entry = {"candidate_id": alt_id, "candidate": alt, "cache_data": cache}
            result = await self._queue_one_entry(request, alt_entry, batch_index, total_to_queue)
            if not result.get("error"):
                result["fallback_for_candidate_id"] = original_id
                result["fallback_reason"] = original_error
                logger.info(
                    f"Queued fallback candidate {alt_id} for failed candidate {original_id} "
                    f"({original_descriptor.get('label') or original_descriptor.get('stable_key') or 'same category unit'}): {original_error}"
                )
                return result
            logger.warning(
                f"Fallback candidate {alt_id} also failed for failed candidate "
                f"{original_id}: {result.get('error')}"
            )
        return None

    def _queue_error_receipt(self, entry: dict[str, Any], error: str) -> dict[str, Any]:
        """Build a user/report-safe error receipt for one failed candidate."""
        candidate = entry.get("candidate") or {}
        cache = entry.get("cache_data") or {}
        return {
            "candidate_id": str(entry.get("candidate_id") or ""),
            "season": candidate.get("season", cache.get("season")),
            "episode": candidate.get("episode", cache.get("episode")),
            "unit_descriptor": candidate.get("unit_descriptor") or {},
            "title": candidate.get("title") or "",
            "source": candidate.get("source") or "",
            "error": error,
        }


    def _queue_result_error(self, result: object) -> str | None:
        """Return a tool-safe error if the scheduler did not verify a queueable row."""
        if not isinstance(result, dict):
            return "Queue operation returned no structured receipt."
        if result.get("error"):
            return str(result.get("error"))
        if not result.get("download_id"):
            return "Queue operation returned no verified download_id."
        if result.get("status") not in {"queued", "already_active"}:
            return f"Queue operation did not create or expose an active download (status={result.get('status') or 'unknown'})."
        return None

    def _import_context_for_candidate(self, request: QueueDownloadRequest, candidate_name: str, candidate: dict[str, Any], cache: dict[str, Any], season: int | None, episode: int | None, unit_descriptor: dict[str, Any] | None = None) -> dict[str, Any]:
        """Build a persisted provider/import snapshot for one cached candidate."""
        metadata = dict(cache.get("metadata_snapshot") or {})
        provider = metadata.get("provider")
        provider_id = (
            metadata.get("provider_id") or metadata.get("external_id")
            or metadata.get("tmdb_id") or metadata.get("tvdb_id")
            or metadata.get("tvmaze_id") or metadata.get("imdb_id")
            or (metadata.get("id") if provider else None)
        )
        descriptor = unit_descriptor or self._candidate_unit_descriptor(candidate, request, cache)
        release_title = str(candidate.get("title") or request.selected_torrent_title or "").strip()
        provider_identity_present = bool(provider and provider_id)
        descriptor_key = str((descriptor or {}).get("stable_key") or "").strip()
        has_structured_unit = bool(descriptor_key or season is not None or episode is not None)
        item_identity = cache.get("item_id") or metadata.get("item_id") or candidate_name
        display_title = cache.get("display_name") or metadata.get("title") or candidate_name
        canonical_title = metadata.get("title") or cache.get("display_name") or candidate_name
        if release_title and not provider_identity_present and not has_structured_unit:
            item_identity = release_title
            display_title = release_title
            canonical_title = release_title
        overrides = {
            "category_id": request.category_id or cache.get("category_id") or candidate.get("category_id") or metadata.get("category_id") or "",
            "item_id": item_identity,
            "display_title": display_title,
            "canonical_title": canonical_title,
            "provider": provider,
            "provider_media_type": metadata.get("provider_media_type") or request.category_id or cache.get("category_id") or candidate.get("category_id") or "",
            "provider_id": provider_id,
            "series_start_year": metadata.get("series_start_year") or metadata.get("first_air_date") or metadata.get("year"),
            "release_year": metadata.get("release_year") or metadata.get("release_date") or metadata.get("year"),
            "season_order_type": metadata.get("season_order_type") or "official",
        }
        context = DownloadImportContext.from_selection(
            category_id=str(overrides.get("category_id") or ""),
            item_id=str(overrides.get("item_id") or candidate_name),
            item_name=str(display_title or candidate_name),
            season=season,
            episode=episode,
            unit_descriptor=descriptor,
            language=str(cache.get("language") or request.raw_arguments.get("language") or ""),
            release_title=release_title,
            metadata=metadata,
            candidate=candidate,
            overrides=overrides,
        )
        return context.model_dump(mode="json")

    def _candidate_queue_payload(
        self,
        request: QueueDownloadRequest,
        entry: dict[str, Any],
        batch_index: int,
        total_to_queue: int,
    ) -> dict[str, Any]:
        """Return scheduler kwargs for a resolved cached candidate."""
        candidate = entry["candidate"]
        cache = entry.get("cache_data") or {}
        candidate_name = cache.get("name") or request.name
        if not candidate.get("magnet"):
            return {"error": "Candidate has no queueable magnet/link"}
        if candidate.get("auto_queue_allowed") is False and not request.raw_arguments.get("confirmed"):
            reason = candidate.get("auto_queue_blocked_reason") or "candidate requires user confirmation"
            return {
                "error": f"Candidate requires user confirmation before queueing: {reason}",
                "confirmation_required": True,
                "candidate_id": candidate.get("candidate_id"),
                "title": candidate.get("title"),
                "seeders": candidate.get("seeders"),
                "languages": candidate.get("languages"),
                "next_action": "Show this candidate and at least one safer alternative, then queue with confirmed=true only if the user explicitly accepts it.",
            }
        if not candidate_name:
            return {"error": "Candidate result set has no media item name"}
        unit_descriptor = self._candidate_unit_descriptor(candidate, request, cache)
        coordinates = unit_descriptor.get("coordinates") if isinstance(unit_descriptor.get("coordinates"), dict) else {}
        season = self._candidate_unit(candidate, request.season, cache.get("season"), "season")
        episode = self._candidate_unit(candidate, request.episode, cache.get("episode"), "episode")
        if coordinates.get("season") is not None:
            season = coordinates.get("season")
        if coordinates.get("episode") is not None:
            episode = coordinates.get("episode")
        return {
            "candidate_name": candidate_name,
            "scheduler_kwargs": {
                "name": candidate_name,
                "magnet": candidate.get("magnet"),
                "season": season,
                "episode": episode,
                "category_id": request.category_id or cache.get("category_id") or candidate.get("category_id") or "",
                "estimated_size_bytes": candidate.get("size_bytes") or request.estimated_size_bytes,
                "priority": self._priority_parser.parse(request.priority_for_batch_index(batch_index, total_to_queue)),
                "torrent_title": candidate.get("title") or "",
                "source_seeders": candidate.get("seeders"),
                "import_context": self._import_context_for_candidate(request, candidate_name, candidate, cache, season, episode, unit_descriptor),
            },
        }

    async def _queue_direct_request(self, request: QueueDownloadRequest) -> object:
        """Queue a direct magnet request after validating required fields."""
        if not request.magnet:
            return {"error": "A valid magnet link, candidate_id, or option_index must be provided."}
        if not request.name:
            return {"error": "A media item name must be provided."}
        try:
            result = await self._scheduler.queue_download(
                name=request.name,
                magnet=request.magnet,
                season=request.season,
                episode=request.episode,
                category_id=request.category_id,
                estimated_size_bytes=request.estimated_size_bytes,
                priority=self._priority_parser.parse(request.requested_priority),
                torrent_title=request.selected_torrent_title,
                source_seeders=request.selected_source_seeders,
                import_context=self._import_context_for_direct(request),
            )
            validation_error = self._queue_result_error(result)
            if validation_error:
                return {"error": validation_error, "raw_result": result}
            return result
        except Exception as exc:
            logger.error(f"Queue download tool error: {exc}")
            return {"error": str(exc)}

    def _import_context_for_direct(self, request: QueueDownloadRequest) -> dict[str, Any] | None:
        """Build import context from direct queue_download arguments when supplied."""
        metadata = {
            key: request.raw_arguments.get(key)
            for key in (
                "provider", "provider_id", "provider_media_type", "external_id",
                "tmdb_id", "tvdb_id", "tvmaze_id", "imdb_id",
                "year", "release_year", "series_start_year", "first_air_date",
                "season_order_type", "title", "display_title", "canonical_title",
            )
            if request.raw_arguments.get(key) not in (None, "")
        }
        if not metadata and not request.category_id:
            return None
        context = DownloadImportContext.from_selection(
            category_id=request.category_id or str(metadata.get("category_id") or ""),
            item_id=str(request.raw_arguments.get("item_id") or request.name or ""),
            item_name=str(request.name or metadata.get("title") or ""),
            season=request.season,
            episode=request.episode,
            unit_descriptor=request.raw_arguments.get("unit_descriptor") if isinstance(request.raw_arguments.get("unit_descriptor"), dict) else None,
            language=str(request.raw_arguments.get("language") or ""),
            release_title=request.selected_torrent_title,
            metadata=metadata,
            candidate={"title": request.selected_torrent_title, "magnet": request.magnet},
        )
        return context.model_dump(mode="json")

    def _candidate_unit(self, candidate: dict[str, Any], explicit: Any, cached: Any, field: str) -> Any:
        """Resolve transitional coordinates from descriptor, then legacy fields."""
        descriptor = candidate.get("unit_descriptor") or {}
        coordinates = descriptor.get("coordinates") if isinstance(descriptor.get("coordinates"), dict) else {}
        if coordinates.get(field) is not None:
            return coordinates.get(field)
        if candidate.get(field) is not None:
            return candidate.get(field)
        if explicit is not None:
            return explicit
        return cached

    def _candidate_unit_descriptor(self, candidate: dict[str, Any], request: QueueDownloadRequest, cache: dict[str, Any]) -> dict[str, Any]:
        """Return the category-owned unit descriptor stored with a cached result."""
        descriptor = candidate.get("unit_descriptor")
        if isinstance(descriptor, dict) and descriptor:
            return descriptor
        category = self._category_for_request(request, cache, candidate)
        if category and hasattr(category, "unit_descriptor_from_agent_args"):
            try:
                return category.unit_descriptor_from_agent_args(
                    season=request.season if request.season is not None else cache.get("season"),
                    episode=request.episode if request.episode is not None else cache.get("episode"),
                )
            except Exception:
                return {}
        return {}

    def _category_for_request(self, request: QueueDownloadRequest, cache: dict[str, Any], candidate: dict[str, Any]) -> object | None:
        """Resolve a category from the request/cache/candidate without defaults."""
        if not self._categories:
            return None
        category_id = request.category_id or cache.get("category_id") or candidate.get("category_id") or ""
        if not category_id:
            return None
        try:
            return self._categories.get(category_id)
        except Exception:
            return None

    def _queued_entry_receipt(
        self,
        entry: dict[str, Any],
        candidate_name: str,
        result: object,
        candidate: dict[str, Any],
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build the per-candidate queue receipt returned to the LLM."""
        scheduler_kwargs = (payload or {}).get("scheduler_kwargs", {}) if isinstance(payload, dict) else {}
        receipt = result if isinstance(result, dict) else {}
        return {
            "candidate_id": str(entry["candidate_id"]),
            "download_id": receipt.get("download_id"),
            "queue_status": receipt.get("status"),
            "download_status": receipt.get("download_status"),
            "already_existing": bool(receipt.get("already_existing")),
            "name": candidate_name,
            "season": scheduler_kwargs.get("season", candidate.get("season")),
            "episode": scheduler_kwargs.get("episode", candidate.get("episode")),
            "unit_descriptor": candidate.get("unit_descriptor") or {},
            "title": candidate.get("title"),
        }

    def _batch_result(self, queued: list[dict[str, Any]], errors: list[dict[str, Any]]) -> dict[str, Any]:
        """Aggregate queued receipts and partial errors into the legacy shape."""
        return {
            "status": "queued",
            "download_id": queued[0].get("download_id"),
            "download_ids": [item.get("download_id") for item in queued if item.get("download_id")],
            "queued": queued,
            "queued_count": len(queued),
            "error_count": len(errors),
            "errors": errors,
            "partial_failure": bool(errors),
            "fallback_count": len([item for item in queued if item.get("fallback_for_candidate_id")]),
        }


class SupportToolProvider:
    """Compatibility provider for helper-only tool modules.

    This module contributes service collaborators consumed by a higher-level
    provider, so it intentionally returns no standalone agent tools.  Keeping a
    provider-shaped facade preserves package-wide smoke checks while still
    allowing implementation modules to remain focused and dependency-light.
    """

    def get_tools(self) -> list:
        """Return no tools because this support module is not an agent boundary."""
        return []
