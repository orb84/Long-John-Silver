"""
Scheduling and recurring task tools for LJS.

Declarative AgentTool implementations for creating, listing,
and removing scheduled tasks, as well as immediate show checking.
"""

from __future__ import annotations

from typing import Any, Optional, TYPE_CHECKING

from loguru import logger

from src.ai.tools.base import AgentTool
from src.core.models import ToolExecutionContext
from src.core.models import Intent

if TYPE_CHECKING:
    from src.core.prompt_scheduler import PromptScheduler
    from src.core.scheduler import MediaScheduler
    from src.core.config import SettingsManager
    from src.core.task_supervisor import TaskSupervisor


def _format_size(size_bytes: int | None) -> str | None:
    """Format byte sizes for LLM-facing candidate summaries."""
    if not size_bytes:
        return None
    units = [(1024 ** 3, "GB"), (1024 ** 2, "MB"), (1024, "KB")]
    for factor, suffix in units:
        if size_bytes >= factor:
            return f"{size_bytes / factor:.2f} {suffix}"
    return f"{size_bytes} B"


class CreateScheduledTaskTool:
    """Create a recurring task that the AI will execute periodically."""

    name = "create_scheduled_task"
    description = (
        "Create a recurring task that the AI will run automatically "
        "at a set interval."
    )
    intents = {Intent.CONFIG}
    allow_direct = True
    requires_confirmation = True
    destructive = False
    required_dependencies = ["prompt_scheduler"]

    def __init__(self, prompt_scheduler: Optional[PromptScheduler] = None) -> None:
        self._prompt_scheduler = prompt_scheduler

    def parameters(self) -> dict:
        """Return the public tool parameter schema.

        The schema is consumed by the LLM runtime and should remain
        backward-compatible.  Add optional fields for extensions whenever
        possible, and keep validation rules mirrored in execute().
        """
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": (
                        "The natural-language instruction for the recurring task."
                    ),
                },
                "interval_minutes": {
                    "type": "integer",
                    "description": (
                        "How often to run in minutes. "
                        "1440=daily, 10080=weekly (default), 43200=monthly."
                    ),
                },
            },
            "required": ["prompt"],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Create a recurring task."""
        prompt = arguments["prompt"]
        interval_minutes = arguments.get("interval_minutes", 10080)
        logger.info(f"Tool: Creating scheduled task: {prompt[:80]}")
        if not self._prompt_scheduler:
            return {"error": "Scheduled tasks not available."}
        try:
            task = await self._prompt_scheduler.create_task(
                prompt=prompt, interval_minutes=interval_minutes
            )
            hours = interval_minutes / 60
            if interval_minutes <= 1440:
                period = "daily"
            elif interval_minutes <= 10080:
                period = "weekly"
            else:
                period = f"every {hours:.0f}h"
            return {
                "message": f"Created {period} task (id={task.id}): '{prompt}'",
                "task_id": task.id,
                "interval_minutes": interval_minutes,
            }
        except Exception as e:
            logger.error(f"Create scheduled task tool error: {e}")
            return {"error": str(e)}


class ListScheduledTasksTool:
    """List all active recurring tasks."""

    name = "list_scheduled_tasks"
    description = (
        "List all active recurring tasks with their schedules."
    )
    intents = {Intent.CONFIG}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["prompt_scheduler"]

    def __init__(self, prompt_scheduler: Optional[PromptScheduler] = None) -> None:
        self._prompt_scheduler = prompt_scheduler

    def parameters(self) -> dict:
        """Return the public tool parameter schema.

        The schema is consumed by the LLM runtime and should remain
        backward-compatible.  Add optional fields for extensions whenever
        possible, and keep validation rules mirrored in execute().
        """
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """List all active recurring tasks."""
        logger.info("Tool: Listing scheduled tasks")
        if not self._prompt_scheduler:
            return {"error": "Scheduled tasks not available."}
        try:
            tasks = await self._prompt_scheduler.list_tasks()
            if not tasks:
                return {"message": "No scheduled tasks configured.", "tasks": []}
            return {
                "tasks": [
                    {
                        "id": t.id,
                        "prompt": t.prompt,
                        "interval_minutes": t.interval_minutes,
                        "enabled": t.enabled,
                        "last_run": t.last_run_at.isoformat()
                        if t.last_run_at
                        else None,
                    }
                    for t in tasks
                ],
                "total": len(tasks),
            }
        except Exception as e:
            logger.error(f"List scheduled tasks tool error: {e}")
            return {"error": str(e)}


class RemoveScheduledTaskTool:
    """Remove a recurring task by its ID."""

    name = "remove_scheduled_task"
    description = (
        "Remove a recurring task so it no longer runs."
    )
    intents = {Intent.CONFIG}
    allow_direct = True
    requires_confirmation = True
    destructive = False
    required_dependencies = ["prompt_scheduler"]

    def __init__(self, prompt_scheduler: Optional[PromptScheduler] = None) -> None:
        self._prompt_scheduler = prompt_scheduler

    def parameters(self) -> dict:
        """Return the public tool parameter schema.

        The schema is consumed by the LLM runtime and should remain
        backward-compatible.  Add optional fields for extensions whenever
        possible, and keep validation rules mirrored in execute().
        """
        return {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The ID of the scheduled task to remove.",
                },
            },
            "required": ["task_id"],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Remove a recurring task by its ID."""
        task_id = arguments["task_id"]
        logger.info(f"Tool: Removing scheduled task '{task_id}'")
        if not self._prompt_scheduler:
            return {"error": "Scheduled tasks not available."}
        try:
            await self._prompt_scheduler.remove_task(task_id)
            return {"message": f"Removed scheduled task '{task_id}'."}
        except Exception as e:
            logger.error(f"Remove scheduled task tool error: {e}")
            return {"error": str(e)}


# Category-owned episodic workflow tools replace the retired immediate show-check tool.


class ListMediaTool:
    """List all tracked category items with category, language, and enabled status."""

    name = "list_media"
    description = (
        "List all tracked category items with category, language, "
        "enabled status, and download counts. ALWAYS call this first to find a media "
        "item by exact name before using other tools. Returns 'media' list where each "
        "item has 'name' and 'category'."
    )
    intents = {Intent.SEARCH, Intent.DOWNLOAD, Intent.CONFIG}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["scheduler"]

    def __init__(self, scheduler: Optional[MediaScheduler] = None) -> None:
        self._scheduler = scheduler

    def parameters(self) -> dict:
        """Return the public tool parameter schema.

        The schema is consumed by the LLM runtime and should remain
        backward-compatible.  Add optional fields for extensions whenever
        possible, and keep validation rules mirrored in execute().
        """
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """List all tracked media."""
        if not self._scheduler:
            return {"error": "Scheduler not available"}
        return await self._scheduler.list_media()


class ListMediaItemsTool:
    """Return all units/status entries for a category item."""

    name = "list_media_items"
    description = (
        "Return all units/status entries for a category item "
        "with download state (downloaded/downloading/missing). "
        "Chain: list_media -> list_media_items -> set_download_priority or search_media_torrents."
    )
    intents = {Intent.SEARCH, Intent.DOWNLOAD, Intent.CONFIG}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["scheduler"]

    def __init__(self, scheduler: Optional[MediaScheduler] = None) -> None:
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
                "name": {
                    "type": "string",
                    "description": "Exact name from list_media.",
                },
            },
            "required": ["name"],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Return all items for a media entry."""
        name = arguments["name"]
        if not self._scheduler:
            return {"error": "Scheduler not available"}
        return await self._scheduler.list_media_items(name)


class SearchMediaTorrentsTool:
    """Search torrents for a media item."""

    name = "search_media_torrents"
    description = (
        "Search torrents for any media item (tracked or untracked). Returns candidates with title, size, "
        "seeders, language, resolution, codec, magnet. Use exact tracked item keys from the "
        "CATEGORY LIBRARY CONTEXT PACKET when available. For category units such as season, "
        "episode, chapter, disc, or track, use dedicated arguments rather than appending "
        "localized phrases to the name. YOU MUST evaluate candidates for the correct language. "
        "IF NO candidates are found for the preferred language, DO NOT download anything; "
        "instead, ASK the user if they want to try another language or wait. For bundled "
        "payloads, pass category-owned unit arguments/descriptors rather than forcing a "
        "category-specific phrase into the title."
    )
    intents = {Intent.DOWNLOAD}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["scheduler"]

    def __init__(self, scheduler: Optional[MediaScheduler] = None) -> None:
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
                "name": {
                    "type": "string",
                    "description": "Name of the category item (tracked or untracked).",
                },
                "season": {
                    "type": "integer",
                    "description": "Season number for episodic categories. Omit episode to search for a full season/unit pack.",
                },
                "episode": {
                    "type": "integer",
                    "description": "Episode/unit number for episodic categories. Omit to search for a full season/unit pack.",
                },
                "language": {
                    "type": "string",
                    "description": "Preferred language (e.g. 'English', 'Italian'). Defaults to global setting.",
                },
                "search_scope": {
                    "type": "string",
                    "enum": ["default", "season_pack_preferred", "season_pack_only", "individual_units_only"],
                    "description": "Category-neutral search phase preference. Use season_pack_preferred when the user asks to prefer a full season/pack but can fall back to individual units; use season_pack_only only when the user explicitly wants pack-only.",
                },
                "category_id": {
                    "type": "string",
                    "description": "Optional explicit registered category ID. Pass 'general' only for exact miscellaneous file targets that do not fit richer categories; never use it as a fallback for a failed TV/movie search without user approval.",
                },
            },
            "required": ["name"],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Search torrents for a media item."""
        name = arguments["name"]
        season = arguments.get("season")
        episode = arguments.get("episode")
        language = arguments.get("language")
        search_scope = arguments.get("search_scope") or "default"
        category_id = str(arguments.get("category_id") or "").strip() or None
        session_id = context.session_id or "default"
        if not self._scheduler:
            return {"error": "Scheduler not available"}
        
        res = await self._scheduler.search_media_torrents(
            name=name,
            season=season,
            episode=episode,
            language=language,
            search_scope=search_scope,
            category_id=category_id,
        )
        
        if not isinstance(res, dict) or "candidates" not in res:
            return res

        # Cache candidates (with magnets) in database preferences under a stable result_set_id.
        from src.utils.candidate_ids import attach_candidate_ids, stable_result_set_id, store_result_set
        cache_candidates = []
        for i, c in enumerate(res.get("candidates", [])):
            cache_candidates.append({
                "index": i + 1,
                "title": c.get("title"),
                "magnet": c.get("magnet"),
                "size": c.get("size"),
                "size_bytes": c.get("size_bytes"),
                "seeders": c.get("seeders"),
                "source": c.get("source"),
                "size_bytes": c.get("size_bytes"),
                "category_id": res.get("category_id"),
                "season": c.get("season"),
                "episode": c.get("episode"),
                "languages": c.get("languages"),
                "resolution": c.get("resolution"),
                "codec": c.get("codec"),
                "per_episode_size_bytes": c.get("per_episode_size_bytes"),
                "estimated_bitrate_kbps": c.get("estimated_bitrate_kbps"),
                "unit_descriptor": c.get("unit_descriptor") or {},
                "bundle_context": c.get("bundle_context") or {},
                "is_bundle": c.get("is_bundle"),
                "bundle_scope": c.get("bundle_scope"),
                "pack_type": c.get("pack_type"),
                "bundle_unit_count": c.get("bundle_unit_count"),
            })

        cache_candidates = attach_candidate_ids(cache_candidates)
        result_set_id = stable_result_set_id(
            session_id=session_id,
            name=res.get("name") or name,
            query=res.get("query"),
            season=res.get("season", season),
            episode=res.get("episode", episode),
            candidate_ids=[c["candidate_id"] for c in cache_candidates],
        )

        category = None
        registry = getattr(self._scheduler, "_categories", None)
        if registry and res.get("category_id"):
            try:
                category = registry.get(res.get("category_id"))
            except Exception:
                category = None
        batch_recommendation = _build_batch_recommendation(
            name=res.get("name") or name,
            category_id=res.get("category_id"),
            season=res.get("season", season),
            episode=res.get("episode", episode),
            result_set_id=result_set_id,
            candidates=cache_candidates,
            category=category,
            preferred_language=res.get("language") or language,
        )

        cache_data = {
            "name": res.get("name") or name,
            "display_name": res.get("display_name") or res.get("name") or name,
            "item_id": res.get("item_id") or res.get("name") or name,
            "query": res.get("query"),
            "season": res.get("season", season),
            "episode": res.get("episode", episode),
            "category_id": res.get("category_id"),
            "metadata_snapshot": res.get("metadata_snapshot") or {},
            "search_scope": res.get("search_scope") or search_scope,
            "result_set_id": result_set_id,
            "candidates": cache_candidates,
            "batch_recommendation": batch_recommendation,
        }

        db = getattr(self._scheduler, "_db", None)
        if db:
            try:
                await store_result_set(db, session_id=session_id, cache_data=cache_data)
            except Exception as e:
                logger.warning(f"Failed to cache search_media_torrents options: {e}")

        # Format clean candidates for LLM (with stable IDs, without magnets)
        clean_candidates = []
        for c in cache_candidates:
            clean_candidates.append({
                "index": c.get("index"),
                "candidate_id": c.get("candidate_id"),
                "result_set_id": result_set_id,
                "title": c.get("title"),
                "size": c.get("size"),
                "size_bytes": c.get("size_bytes"),
                "seeders": c.get("seeders"),
                "source": c.get("source"),
                "quality_score": next((raw.get("quality_score") for raw in res.get("candidates", []) if raw.get("title") == c.get("title")), None),
                "season": c.get("season"),
                "episode": c.get("episode"),
                "languages": c.get("languages"),
                "resolution": c.get("resolution"),
                "codec": c.get("codec"),
                "per_episode_size": _format_size(c.get("per_episode_size_bytes")),
                "estimated_bitrate_kbps": c.get("estimated_bitrate_kbps"),
                "unit_descriptor": c.get("unit_descriptor") or {},
                "bundle_context": c.get("bundle_context") or {},
                "is_bundle": c.get("is_bundle"),
                "bundle_scope": c.get("bundle_scope"),
                "pack_type": c.get("pack_type"),
                "bundle_unit_count": c.get("bundle_unit_count"),
            })

        selected_for_estimate = _candidate_ids_for_estimate(
            clean_candidates,
            batch_recommendation=batch_recommendation,
            search_scope=res.get("search_scope") or search_scope,
        )
        estimated_total_size_bytes = _estimated_total_size_bytes(clean_candidates, selected_for_estimate)

        res["result_set_id"] = result_set_id
        res["search_scope"] = res.get("search_scope") or search_scope
        res["candidate_count"] = len(clean_candidates)
        res["estimated_total_size_bytes"] = estimated_total_size_bytes
        res["results_total_size_gb"] = round(estimated_total_size_bytes / (1024 ** 3), 3) if estimated_total_size_bytes else 0
        res["candidate_picker"] = _candidate_picker_rows(clean_candidates, limit=60)
        res["result_handle"] = {
            "type": "torrent_result_set",
            "result_set_id": result_set_id,
            "name": res.get("display_name") or res.get("name") or name,
            "category_id": res.get("category_id"),
            "search_scope": res.get("search_scope") or search_scope,
        }
        res["next_actions"] = _search_result_next_actions(
            candidates=clean_candidates,
            search_scope=res.get("search_scope") or search_scope,
            result_set_id=result_set_id,
            has_batch=bool(batch_recommendation),
        )
        res["search_summary"] = {
            "query": res.get("query"),
            "candidate_count": len(clean_candidates),
            "search_scope": res.get("search_scope") or search_scope,
            "pack_first_fallback": "pack unavailable" in str(res.get("query") or "").lower(),
            "estimated_total_size_bytes": estimated_total_size_bytes,
            "results_total_size_gb": res["results_total_size_gb"],
            "candidate_picker_note": "Use candidate_picker ids for selection; full records remain cached under result_set_id.",
            "next_action_note": "Use next_actions as affordances; do not invent JSON paths into this result.",
        }
        res["candidates"] = clean_candidates
        if batch_recommendation:
            # The recommendation is deliberately explicit and copyable by the LLM.
            # It is built from the same cached candidate IDs that queue_download can
            # resolve, while hiding raw magnets from the model.
            clean_groups = []
            by_id = {c.get("candidate_id"): c for c in clean_candidates}
            for group in batch_recommendation.get("groups", []):
                rec_id = group.get("recommended_candidate_id")
                rec = by_id.get(rec_id)
                clean_groups.append({
                    "unit": group.get("unit"),
                    "recommended_candidate_id": rec_id,
                    "title": rec.get("title") if rec else None,
                    "size": rec.get("size") if rec else None,
                    "seeders": rec.get("seeders") if rec else None,
                    "is_bundle": rec.get("is_bundle") if rec else None,
                    "bundle_scope": rec.get("bundle_scope") if rec else None,
                    "candidate_count": group.get("candidate_count"),
                    "unit_descriptor": group.get("unit_descriptor") or {},
                    "coordinates": group.get("coordinates") or {},
                })
            res["batch_recommendation"] = {
                "intent": batch_recommendation.get("intent"),
                "reason": batch_recommendation.get("reason"),
                "result_set_id": result_set_id,
                "candidate_ids": batch_recommendation.get("candidate_ids"),
                "groups": clean_groups,
                "queue_download_arguments": batch_recommendation.get("queue_download_arguments"),
            }
            res["llm_next_action"] = (
                "The user asked for a multi-unit download. Queue every recommended candidate by calling "
                "queue_download with batch_recommendation.queue_download_arguments. Do not queue only the first episode."
            )
        return res


def _search_result_next_actions(*, candidates: list[dict[str, Any]], search_scope: str | None, result_set_id: str, has_batch: bool) -> list[dict[str, Any]]:
    """Return prompt-safe affordances for a cached torrent result set.

    These are not commands that the executor blindly follows; they are valid
    next moves the LLM can choose from in the natural tool loop.  Keeping them
    explicit prevents the model from guessing internal result paths while still
    letting it reason flexibly across categories.
    """
    scope = str(search_scope or "").lower()
    actions: list[dict[str, Any]] = []
    if not candidates:
        actions.append({
            "action": "broaden_search",
            "tool": "search_media_torrents",
            "reason": "No usable candidates were returned for this scope.",
            "args_hint": {"search_scope": "season_pack_preferred" if "pack_only" in scope else "broad"},
        })
        if "pack" in scope:
            actions.append({
                "action": "fallback_to_individual_units",
                "tool": "search_media_torrents",
                "reason": "A pack was preferred but not found; per-unit fallback may be necessary unless the user asked for pack-only.",
                "args_hint": {"search_scope": "individual_units"},
            })
        return actions

    if has_batch:
        actions.append({
            "action": "queue_batch_recommendation",
            "tool": "queue_download",
            "reason": "The category produced one recommended candidate per requested unit.",
            "args_source": "batch_recommendation.queue_download_arguments",
        })

    bundle_candidates = [c for c in candidates if c.get("is_bundle") or c.get("bundle_scope") or c.get("pack_type")]
    if bundle_candidates:
        actions.append({
            "action": "inspect_bundle_files",
            "tool": "inspect_torrent_candidate",
            "reason": "Bundle/full-series candidates may contain multiple seasons or folders; inspect the file list/summary if coverage is ambiguous before queueing.",
            "args_hint": {"result_set_id": result_set_id, "candidate_id": bundle_candidates[0].get("candidate_id"), "detail": "file_list"},
        })

    actions.append({
        "action": "queue_clear_candidate",
        "tool": "queue_download",
        "reason": "Use this only when the candidate clearly matches the user's target and constraints.",
        "args_hint": {"result_set_id": result_set_id, "candidate_id": candidates[0].get("candidate_id")},
    })
    actions.append({
        "action": "show_or_request_choice",
        "tool": None,
        "reason": "If multiple plausible candidates remain, summarize the best few by candidate_id/title/size/seeders and ask the user to choose.",
    })
    return actions


def _candidate_picker_rows(candidates: list[dict[str, Any]], limit: int = 60) -> list[dict[str, Any]]:
    """Return a dense candidate workspace for the LLM.

    Full candidate records and magnets stay in the result-set cache.  The model
    sees stable IDs plus the tiny facts needed to decide what to inspect or
    queue: filename/title, size, seeders, and category bundle hints.
    """
    rows: list[dict[str, Any]] = []
    for c in candidates[: max(0, int(limit))]:
        row = {
            "id": c.get("candidate_id"),
            "index": c.get("index"),
            "title": c.get("title"),
            "size": c.get("size"),
            "size_bytes": c.get("size_bytes"),
            "seeders": c.get("seeders"),
        }
        if c.get("is_bundle"):
            row.update({
                "is_bundle": True,
                "bundle_scope": c.get("bundle_scope"),
                "pack_type": c.get("pack_type"),
                "bundle_unit_count": c.get("bundle_unit_count"),
            })
        descriptor = c.get("unit_descriptor") or {}
        if descriptor:
            row["unit"] = descriptor.get("label") or descriptor.get("stable_key")
        rows.append({k: v for k, v in row.items() if v not in (None, "", [], {})})
    return rows


def _candidate_ids_for_estimate(candidates: list[dict[str, Any]], *, batch_recommendation: dict[str, Any] | None, search_scope: str | None) -> list[str]:
    """Choose which candidate sizes represent the planned download footprint."""
    if batch_recommendation and batch_recommendation.get("candidate_ids"):
        return [str(cid) for cid in batch_recommendation.get("candidate_ids") or [] if cid]
    if str(search_scope or "").startswith("season_pack"):
        for c in candidates:
            if c.get("is_bundle") and c.get("candidate_id"):
                return [str(c.get("candidate_id"))]
    if candidates and candidates[0].get("candidate_id"):
        return [str(candidates[0].get("candidate_id"))]
    return []


def _estimated_total_size_bytes(candidates: list[dict[str, Any]], selected_ids: list[str]) -> int:
    """Sum selected candidate sizes for storage preflight placeholders."""
    if not selected_ids:
        return 0
    wanted = {str(cid) for cid in selected_ids}
    total = 0
    for c in candidates:
        if str(c.get("candidate_id")) not in wanted:
            continue
        try:
            total += int(c.get("size_bytes") or 0)
        except (TypeError, ValueError):
            pass
    return total


def _build_batch_recommendation(*, name: str, category_id: str | None, season: int | None, episode: int | None, result_set_id: str, candidates: list[dict[str, Any]], category: object | None = None, preferred_language: str | None = None) -> dict[str, Any] | None:
    """Build a category-owned multi-unit batch recommendation.

    Shared tooling no longer groups candidates by hard-coded ``season``/
    ``episode`` fields. It asks the category to expose batch grouping metadata
    from the candidate's unit descriptor. Categories that do not publish
    concrete sub-unit descriptors simply get no auto-batch recommendation.

    Within each category-owned unit group, availability is a first-class
    signal: after language and usable quality fit, higher seeder counts beat
    otherwise equivalent candidates. This prevents the assistant from marking a
    lower-seeded duplicate as the recommended candidate merely because an
    indexer returned it first.
    """
    if episode is not None:
        return None
    if not category or not hasattr(category, "batch_group_for_candidate"):
        return None

    unit_groups: dict[str, dict[str, Any]] = {}
    request_context = {"season": season, "episode": episode, "category_id": category_id}
    for c in candidates or []:
        group = category.batch_group_for_candidate(c, request_context)
        if not group:
            continue
        key = str(group.get("key") or "")
        if not key:
            continue
        unit_groups.setdefault(key, {"group": group, "candidates": []})["candidates"].append(c)

    if len(unit_groups) <= 1:
        return None

    ordered = sorted(unit_groups.values(), key=lambda data: data["group"].get("sort_key") or [data["group"].get("label") or ""])
    groups: list[dict[str, Any]] = []
    candidate_ids: list[str] = []
    for data in ordered:
        ranked = sorted(
            data["candidates"],
            key=lambda candidate: _batch_candidate_score(candidate, preferred_language),
            reverse=True,
        )
        recommended = ranked[0]
        cid = recommended.get("candidate_id")
        if not cid:
            continue
        candidate_ids.append(cid)
        descriptor = data["group"].get("descriptor") or {}
        groups.append({
            "unit": data["group"].get("label") or data["group"].get("key"),
            "unit_descriptor": descriptor,
            "coordinates": descriptor.get("coordinates") or {},
            "recommended_candidate_id": cid,
            "candidate_count": len(ranked),
        })

    if len(candidate_ids) <= 1:
        return None

    queue_args = {
        "name": name,
        "category_id": category_id,
        "result_set_id": result_set_id,
        "candidate_ids": candidate_ids,
    }
    if season is not None:
        queue_args["season"] = season

    return {
        "intent": "multi_unit_download",
        "reason": "Multiple distinct category units have eligible ranked candidates.",
        "auto_expand_single_selection": False,
        "result_set_id": result_set_id,
        "candidate_ids": candidate_ids,
        "groups": groups,
        "queue_download_arguments": queue_args,
    }


def _batch_candidate_score(candidate: dict[str, Any], preferred_language: str | None = None) -> tuple:
    """Return deterministic per-unit recommendation score for cached candidates.

    The score is deliberately narrow and explainable. The LLM still handles
    semantic torrent decisions in the conversation loop, but batch recommendations
    must not depend on arbitrary provider order. For candidates that already pass
    category validation, choose by:

    1. confirmed preferred/multi language,
    2. usable resolution,
    3. seeder availability,
    4. category/search quality score,
    5. codec/release hints,
    6. modest size sanity as final tie-breaker.
    """
    languages = candidate.get("languages") or []
    if isinstance(languages, str):
        languages = [languages]
    normalized_languages = {_canonical_language_token(lang) for lang in languages}
    preferred = _canonical_language_token(preferred_language) if preferred_language else ""
    title_lower = str(candidate.get("title") or "").lower()
    if preferred and preferred in normalized_languages:
        lang_score = 3
    elif "multi" in normalized_languages or "dual" in title_lower or "multi" in title_lower:
        lang_score = 2
    elif not normalized_languages:
        lang_score = 1
    else:
        lang_score = 0

    resolution = str(candidate.get("resolution") or "").lower()
    if "2160" in resolution or "4k" in resolution:
        resolution_score = 1
    elif "1080" in resolution:
        resolution_score = 4
    elif "720" in resolution:
        resolution_score = 3
    elif resolution:
        resolution_score = 2
    else:
        resolution_score = 0

    seeders = _safe_int(candidate.get("seeders"))
    quality_score = _safe_float(candidate.get("quality_score"))
    codec = str(candidate.get("codec") or "").lower()
    codec_score = 1 if codec in {"h265", "x265", "hevc", "av1", "h264", "x264"} else 0
    size_bytes = _safe_int(candidate.get("per_episode_size_bytes") or candidate.get("size_bytes"))
    size_tie = -size_bytes if size_bytes > 0 else 0
    return (lang_score, resolution_score, seeders, quality_score, codec_score, size_tie)


def _canonical_language_token(value: object) -> str:
    token = str(value or "").strip().lower()
    aliases = {
        "italian": "italian", "italiano": "italian", "ita": "italian", "it": "italian",
        "english": "english", "eng": "english", "en": "english",
        "spanish": "spanish", "spa": "spanish", "es": "spanish",
        "multi": "multi", "multilanguage": "multi", "multi-language": "multi", "dual": "multi",
    }
    return aliases.get(token, token)


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


class SchedulingToolProvider:
    """Provides scheduling and task management agent tools.

    Aggregates all AgentTool implementations from the scheduling domain
    and returns instantiated instances via get_tools().
    """

    def __init__(
        self,
        prompt_scheduler: Optional[PromptScheduler] = None,
        scheduler: Optional[MediaScheduler] = None,
        settings_manager: Optional[SettingsManager] = None,
        supervisor: Optional[TaskSupervisor] = None,
    ) -> None:
        """Initialize with optional dependencies.

        Args:
            prompt_scheduler: PromptScheduler instance.
            scheduler: MediaScheduler instance.
            settings_manager: SettingsManager instance.
            supervisor: TaskSupervisor instance.
        """
        self._prompt_scheduler = prompt_scheduler
        self._scheduler = scheduler
        self._settings_manager = settings_manager
        self._supervisor = supervisor

    def get_tools(self) -> list:
        """Return instantiated scheduling tool instances.

        Returns:
            List of AgentTool-compatible tool instances.
        """
        return [
            CreateScheduledTaskTool(prompt_scheduler=self._prompt_scheduler),
            ListScheduledTasksTool(prompt_scheduler=self._prompt_scheduler),
            RemoveScheduledTaskTool(prompt_scheduler=self._prompt_scheduler),
            ListMediaTool(scheduler=self._scheduler),
            ListMediaItemsTool(scheduler=self._scheduler),
            SearchMediaTorrentsTool(scheduler=self._scheduler),
        ]
