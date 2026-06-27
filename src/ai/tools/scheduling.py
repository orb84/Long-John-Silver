"""
Scheduling and recurring task tools for LJS.

Declarative AgentTool implementations for creating, listing,
and removing scheduled tasks, as well as immediate show checking.
"""

from __future__ import annotations

from typing import Any, Optional, TYPE_CHECKING

from loguru import logger

from src.ai.tools.base import AgentTool
from src.ai.media_title_repair import MediaTitleRepair
from src.ai.download_candidate_adjudicator import DownloadCandidateAdjudicator
from src.ai.tools.search_retry import UnmatchedSearchRetryScheduler
from src.ai.tools.search_workspace import (
    CandidateBundlePolicy,
    SearchArgumentConstraints,
    SearchBatchRecommendationBuilder,
    SearchQualityChoicePolicy,
    SearchWorkspaceAuditLogger,
    SearchWorkspaceFormatter,
    SearchWorkspaceNextActions,
    SelectionPolicyAnnotator,
)
from src.core.models import ToolExecutionContext
from src.core.models import Intent

if TYPE_CHECKING:
    from src.core.prompt_scheduler import PromptScheduler
    from src.core.scheduler import MediaScheduler
    from src.core.config import SettingsManager
    from src.core.task_supervisor import TaskSupervisor



class CreateScheduledTaskTool:
    """Create a reminder, scheduled prompt, or recurring assistant check."""

    name = "create_scheduled_task"
    description = (
        "Create a user reminder, one-off scheduled assistant task, or recurring "
        "condition check. Use for simple reminders and generic future checks. "
        "For recurring public news/rumour/patch/release tracking with evidence/provenance, prefer "
        "create_web_information_watch instead."
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

        The model should prefer ``delay_minutes`` for relative requests and
        ``due_at`` for absolute calendar requests.  One-off tasks must provide
        one of them; recurring tasks may also provide one as the first run time.
        """
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": (
                        "The instruction/reminder text. For condition checks, preserve the user's exact objective, "
                        "target item, source preference, success condition, and what should trigger a notification. Do not include secrets."
                    ),
                },
                "title": {
                    "type": "string",
                    "description": "Short notification title shown when the task fires.",
                },
                "task_type": {
                    "type": "string",
                    "enum": ["reminder", "scheduled_prompt", "condition_check"],
                    "description": (
                        "reminder sends the prompt as a notification without an LLM call; "
                        "scheduled_prompt runs the assistant at the due time; condition_check "
                        "runs the assistant/tools to check whether something changed or exists."
                    ),
                },
                "schedule_type": {
                    "type": "string",
                    "enum": ["one_off", "recurring"],
                    "description": "Use one_off for 'in 7 days' reminders/checks; recurring for repeated reports/checks.",
                },
                "due_at": {
                    "type": "string",
                    "description": "Optional absolute first run time as ISO-8601 datetime, with timezone when known. Use runtime date context from the prompt; do not guess past dates.",
                },
                "delay_minutes": {
                    "type": "integer",
                    "description": "Optional relative delay from now in minutes. 7 days = 10080, 3 weeks = 30240.",
                },
                "interval_minutes": {
                    "type": "integer",
                    "description": "Recurring interval in minutes. 1440=daily, 10080=weekly. Choose the least noisy cadence that still satisfies the user. Ignored for one_off.",
                },
                "max_runs": {
                    "type": "integer",
                    "description": "Optional run limit for recurring tasks. Omit for indefinite recurrence.",
                },
            },
            "required": ["prompt"],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Create a reminder, one-off scheduled task, or recurring check."""
        prompt = str(arguments["prompt"]).strip()
        logger.info(f"Tool: Creating scheduled task: {prompt[:80]}")
        if not self._prompt_scheduler:
            return {"ok": False, "error": "Scheduled tasks not available."}
        try:
            task = await self._prompt_scheduler.create_task(
                prompt=prompt,
                interval_minutes=arguments.get("interval_minutes"),
                user_id=context.user_id,
                channel=context.source or "web",
                title=arguments.get("title"),
                task_type=arguments.get("task_type", "scheduled_prompt"),
                schedule_type=arguments.get("schedule_type", "recurring"),
                due_at=arguments.get("due_at"),
                delay_minutes=arguments.get("delay_minutes"),
                max_runs=arguments.get("max_runs"),
                session_id=context.session_id,
            )
            return {
                "ok": True,
                "message": self._success_message(task),
                "task": {
                    "id": task.id,
                    "title": task.title,
                    "prompt": task.prompt,
                    "task_type": task.task_type,
                    "schedule_type": task.schedule_type,
                    "interval_minutes": task.interval_minutes,
                    "due_at": task.due_at.isoformat() if task.due_at else None,
                    "next_run_at": task.next_run_at.isoformat() if task.next_run_at else None,
                    "max_runs": task.max_runs,
                    "enabled": task.enabled,
                },
                "next_actions": ["list_scheduled_tasks", "remove_scheduled_task"],
            }
        except Exception as e:
            logger.error(f"Create scheduled task tool error: {e}")
            return {"ok": False, "error": str(e)}

    @staticmethod
    def _success_message(task: Any) -> str:
        """Return a concise LLM-facing creation receipt."""
        when = task.next_run_at.isoformat() if task.next_run_at else "now"
        if task.schedule_type == "one_off":
            return f"Created one-off {task.task_type} task (id={task.id}) for {when}."
        return (
            f"Created recurring {task.task_type} task (id={task.id}) every "
            f"{task.interval_minutes} minutes, next run {when}."
        )


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
                        "title": t.title,
                        "prompt": t.prompt,
                        "task_type": t.task_type,
                        "schedule_type": t.schedule_type,
                        "interval_minutes": t.interval_minutes,
                        "enabled": t.enabled,
                        "due_at": t.due_at.isoformat() if t.due_at else None,
                        "next_run_at": t.next_run_at.isoformat() if t.next_run_at else None,
                        "last_run": t.last_run_at.isoformat() if t.last_run_at else None,
                        "run_count": t.run_count,
                        "max_runs": t.max_runs,
                        "last_error": t.last_error or None,
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

    def __init__(self, scheduler: Optional[MediaScheduler] = None, llm_client: object | None = None) -> None:
        self._scheduler = scheduler
        self._candidate_adjudicator = DownloadCandidateAdjudicator(llm_client)


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

    def __init__(self, scheduler: Optional[MediaScheduler] = None, llm_client: object | None = None) -> None:
        self._scheduler = scheduler
        self._candidate_adjudicator = DownloadCandidateAdjudicator(llm_client)


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
        "seeders, category-owned language/quality facets, codec, magnet. Use exact tracked item keys from the "
        "CATEGORY LIBRARY CONTEXT PACKET when available. For category units such as season, "
        "category unit such as an episode, chapter, disc, or track, use dedicated arguments rather than appending "
        "localized phrases to the name. Evaluate language only when the active category says language is relevant; "
        "music searches must not inherit global other category language preferences. "
        "IF NO candidates are found for an explicit category-relevant language, DO NOT download anything; "
        "instead, ASK the user if they want to try another language or wait. For bundled "
        "payloads, pass category-owned unit arguments/descriptors rather than forcing a "
        "category-specific phrase into the title."
    )
    intents = {Intent.DOWNLOAD}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["scheduler"]

    def __init__(self, scheduler: Optional[MediaScheduler] = None, llm_client: object | None = None) -> None:
        self._scheduler = scheduler
        self._candidate_adjudicator = DownloadCandidateAdjudicator(llm_client)
        self._retry_scheduler = UnmatchedSearchRetryScheduler()

    async def _maybe_schedule_unmatched_retry(
        self,
        *,
        res: dict[str, Any],
        name: str,
        category_id: str | None,
        search_scope: str | None,
        context: ToolExecutionContext,
    ) -> None:
        """Schedule a notification-only retry for an unmatched search.

        This compatibility wrapper is intentionally present only on the torrent
        search tool because older regression tests target this seam. The actual
        policy lives in ``UnmatchedSearchRetryScheduler`` so unrelated
        scheduling/list tools do not carry hidden download retry behavior.
        """
        await self._retry_scheduler.maybe_schedule(
            scheduler=self._scheduler,
            res=res,
            name=name,
            category_id=category_id,
            search_scope=search_scope,
            context=context,
        )


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
                    "description": "Literal name/title of the category item (tracked or untracked). Preserve the user's title wording exactly, including small words inside titles such as 'of', 'the', 'a', and subtitles. Do not rewrite titles into shortened search-keyword form.",
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
                    "description": "Media/download language only, not chat language. Set this only when the user explicitly asks for an audio/subtitle/translation language, or when category/tracked-item context supplies a configured media language. Omit it to let LJS apply tracked/category/global media language defaults. Never set it merely because of the language used in the chat message.",
                },
                "language_is_explicit": {
                    "type": "boolean",
                    "description": "True only when the user explicitly requested this media/download language in the current request. Omit or false for language inferred from the chat/reply language or for ordinary default preference handling.",
                },
                "search_scope": {
                    "type": "string",
                    "enum": ["default", "bundle_preferred", "bundle_only", "individual_units_only"],
                    "description": "Category-neutral search phase preference. Use bundle_preferred when the user asks for a whole category-owned unit such as a season, volume, album, collection, or other bundle/pack and can fall back to individual units; use bundle_only only when the user explicitly wants bundle-only.",
                },
                "category_id": {
                    "type": "string",
                    "description": "Optional explicit registered category ID. Pass 'general' only for exact miscellaneous file targets that do not fit richer categories; never use it as a fallback for a failed TV/movie search without user approval.",
                },
                "target_size_gb": {
                    "type": "number",
                    "description": "Optional target payload size in GB when the user asks for a smaller/larger replacement or a size-constrained result. Use this for requests like 'around 2GB'.",
                },
                "max_size_gb": {
                    "type": "number",
                    "description": "Optional maximum payload size in GB. Use when the user says 'under XGB' or asks for a smaller replacement after inspecting the current download size.",
                },
                "min_size_gb": {
                    "type": "number",
                    "description": "Optional minimum payload size in GB. Use only when the user gives a lower bound.",
                },
                "current_size_gb": {
                    "type": "number",
                    "description": "Current active download size in GB when replacing a queued/downloading item with a smaller/larger version.",
                },
                "target_bitrate_kbps": {
                    "type": "number",
                    "description": "Target video/audio bitrate in kbps when the user has expressed a preferred bitrate/quality-size tradeoff. Prefer this over resolution downgrades for smaller replacement searches.",
                },
                "preferred_bitrate_kbps": {
                    "type": "number",
                    "description": "Known per-item or category preferred bitrate in kbps. Use when continuing a show at the user's established bitrate level.",
                },
                "max_bitrate_kbps": {
                    "type": "number",
                    "description": "Maximum acceptable bitrate in kbps when the user explicitly asks for a cap or when a saved quality profile defines one.",
                },
                "current_bitrate_kbps": {
                    "type": "number",
                    "description": "Estimated bitrate of the current active download, used when searching for a smaller same-resolution replacement.",
                },
                "preferred_resolution": {
                    "type": "string",
                    "description": "Preferred/current resolution to preserve for size-optimized replacements, e.g. 1080p. Do not downgrade to 720p unless the user explicitly asked or no same-resolution option exists.",
                },
                "required_resolution": {
                    "type": "string",
                    "description": "Hard resolution constraint when the user explicitly requires one, e.g. 1080p only.",
                },
                "smaller_than_current": {
                    "type": "boolean",
                    "description": "True when the user is replacing an active/queued download with a smaller alternative. Prefer same resolution/language first; do not start by searching lower resolution.",
                },
                "preserve_resolution": {
                    "type": "boolean",
                    "description": "True when a smaller/larger replacement should keep the current/preferred resolution unless the user explicitly approves a downgrade.",
                },
            },
            "required": ["name"],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Search torrents for a media item."""
        name = arguments["name"]
        season = arguments.get("season")
        episode = arguments.get("episode")
        search_scope = arguments.get("search_scope") or "default"
        category_id = str(arguments.get("category_id") or "").strip() or None
        category = None
        registry = getattr(self._scheduler, "_categories", None) if self._scheduler else None
        if registry and category_id:
            try:
                category = registry.get(category_id)
            except Exception:
                category = None
        normalizer = getattr(category, "normalize_agent_search_name_argument", None) if category else None
        if callable(normalizer):
            normalized_name = normalizer(
                name,
                user_prompt=getattr(context, "user_prompt", None),
                season=season,
                episode=episode,
                search_scope=search_scope,
            )
            if normalized_name and normalized_name != name:
                logger.info(
                    "search_media_torrents: category normalized search name %r -> %r",
                    name,
                    normalized_name,
                )
                name = normalized_name
        repaired_name = MediaTitleRepair.recover_literal_title(name, getattr(context, "user_prompt", None))
        if repaired_name and repaired_name != name:
            logger.info(
                "search_media_torrents: repaired lossy LLM title %r -> %r from current user prompt",
                name,
                repaired_name,
            )
            name = repaired_name
        language = arguments.get("language")
        language_is_explicit = bool(arguments.get("language_is_explicit") or arguments.get("explicit_language"))
        if language and not language_is_explicit:
            logger.warning(
                "Ignoring non-explicit search_media_torrents language argument {!r} for {!r}; "
                "tool language is media audio/subtitle language, not chat/reply language; scheduler will apply configured defaults.",
                language,
                arguments.get("name"),
            )
            language = None
        session_id = context.session_id or "default"
        if not self._scheduler:
            return {"error": "Scheduler not available"}
        
        search_constraints = SearchArgumentConstraints.from_arguments(arguments)
        res = await self._scheduler.search_media_torrents(
            name=name,
            season=season,
            episode=episode,
            language=language,
            language_explicit=language_is_explicit,
            search_scope=search_scope,
            category_id=category_id,
            search_constraints=search_constraints,
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
                "category_id": res.get("category_id"),
                "season": c.get("season"),
                "episode": c.get("episode"),
                "languages": c.get("languages"),
                "resolution": c.get("resolution"),
                "codec": c.get("codec"),
                "per_episode_size_bytes": c.get("per_episode_size_bytes"),
                "estimated_bitrate_kbps": c.get("estimated_bitrate_kbps"),
                "bitrate_basis": c.get("bitrate_basis"),
                "per_episode_size_mb": c.get("per_episode_size_mb"),
                "unit_descriptor": c.get("unit_descriptor") or {},
                "bundle_context": c.get("bundle_context") or {},
                "is_bundle": c.get("is_bundle"),
                "bundle_scope": c.get("bundle_scope"),
                "pack_type": c.get("pack_type"),
                "bundle_unit_count": c.get("bundle_unit_count"),
                "selection_warnings": c.get("selection_warnings") or [],
                "selection_blockers": c.get("selection_blockers") or [],
                "auto_queue_allowed": c.get("auto_queue_allowed"),
                "auto_queue_blocked_reason": c.get("auto_queue_blocked_reason"),
            })

        category = None
        registry = getattr(self._scheduler, "_categories", None)
        if registry and res.get("category_id"):
            try:
                category = registry.get(res.get("category_id"))
            except Exception:
                category = None
        cache_candidates = attach_candidate_ids(cache_candidates)
        category_filter = getattr(category, "filter_agent_candidate_payloads_for_request", None) if category else None
        if callable(category_filter):
            try:
                cache_candidates = list(category_filter(
                    cache_candidates,
                    season=res.get("season", season),
                    episode=res.get("episode", episode),
                    search_scope=res.get("search_scope") or search_scope,
                    language=res.get("language") or language,
                ))
                for i, candidate in enumerate(cache_candidates, 1):
                    candidate["index"] = i
            except Exception as exc:
                logger.warning(
                    "search_media_torrents category candidate payload filter failed for category=%r name=%r: %s",
                    res.get("category_id"),
                    res.get("name") or name,
                    exc,
                )
        result_set_id = stable_result_set_id(
            session_id=session_id,
            name=res.get("name") or name,
            query=res.get("query"),
            season=res.get("season", season),
            episode=res.get("episode", episode),
            candidate_ids=[c["candidate_id"] for c in cache_candidates],
        )
        category_language_relevant = True
        if category and hasattr(category, "language_is_search_relevant"):
            try:
                category_language_relevant = bool(category.language_is_search_relevant())
            except Exception:
                category_language_relevant = True
        effective_preferred_language = (res.get("language") or language) if category_language_relevant else None
        batch_recommendation = SearchBatchRecommendationBuilder.build(
            name=res.get("name") or name,
            category_id=res.get("category_id"),
            season=res.get("season", season),
            episode=res.get("episode", episode),
            search_scope=res.get("search_scope") or search_scope,
            result_set_id=result_set_id,
            candidates=cache_candidates,
            category=category,
            preferred_language=effective_preferred_language,
        )

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
                "per_episode_size": SearchWorkspaceFormatter.format_size(c.get("per_episode_size_bytes")),
                "estimated_bitrate_kbps": c.get("estimated_bitrate_kbps"),
                "bitrate_basis": c.get("bitrate_basis"),
                "per_episode_size_mb": c.get("per_episode_size_mb"),
                "unit_descriptor": c.get("unit_descriptor") or {},
                "bundle_context": c.get("bundle_context") or {},
                "is_bundle": c.get("is_bundle"),
                "bundle_scope": c.get("bundle_scope"),
                "pack_type": c.get("pack_type"),
                "bundle_unit_count": c.get("bundle_unit_count"),
                "selection_warnings": c.get("selection_warnings") or [],
                "selection_blockers": c.get("selection_blockers") or [],
                "auto_queue_allowed": c.get("auto_queue_allowed"),
                "auto_queue_blocked_reason": c.get("auto_queue_blocked_reason"),
            })

        SelectionPolicyAnnotator.annotate(clean_candidates, preferred_language=effective_preferred_language, language_is_explicit=language_is_explicit)
        for cache_candidate in cache_candidates:
            clean_match = next((c for c in clean_candidates if c.get("candidate_id") == cache_candidate.get("candidate_id")), None)
            if clean_match:
                cache_candidate["selection_warnings"] = clean_match.get("selection_warnings") or []
                cache_candidate["selection_blockers"] = clean_match.get("selection_blockers") or []
                cache_candidate["auto_queue_allowed"] = clean_match.get("auto_queue_allowed")
                cache_candidate["auto_queue_blocked_reason"] = clean_match.get("auto_queue_blocked_reason")

        category_quality_choice_relevant = True
        if category and hasattr(category, "uses_global_quality_profile"):
            try:
                category_quality_choice_relevant = bool(category.uses_global_quality_profile())
            except Exception:
                category_quality_choice_relevant = True
        quality_choice_policy = (
            SearchQualityChoicePolicy.evaluate(clean_candidates, search_constraints)
            if category_quality_choice_relevant
            else {"requires_user_choice": False, "reason": "category does not use global video quality profile"}
        )
        if quality_choice_policy.get("requires_user_choice"):
            # Do not allow the first row to be silently queued when there is no
            # saved bitrate target and the candidates represent distinct
            # quality/size choices for the same resolution/language.
            for candidate in clean_candidates:
                warnings = list(candidate.get("selection_warnings") or [])
                if quality_choice_policy.get("message") not in warnings:
                    warnings.append(quality_choice_policy.get("message"))
                candidate["selection_warnings"] = warnings
                candidate["auto_queue_allowed"] = False
                candidate["auto_queue_blocked_reason"] = "quality/bitrate preference must be chosen first"
            for cache_candidate in cache_candidates:
                cache_candidate["auto_queue_allowed"] = False
                cache_candidate["auto_queue_blocked_reason"] = "quality/bitrate preference must be chosen first"

        category_guidance = ""
        if category and hasattr(category, "build_torrent_selection_guidance"):
            try:
                category_guidance = str(category.build_torrent_selection_guidance() or "")
            except Exception:
                category_guidance = ""
        adjudication_search_result = dict(res)
        if quality_choice_policy:
            adjudication_search_result["quality_choice_policy"] = quality_choice_policy
        llm_candidate_review = await self._candidate_adjudicator.review(
            user_prompt=getattr(context, "user_prompt", None),
            tool_arguments={**arguments, "name": name, "search_scope": search_scope, "category_id": category_id},
            search_result=adjudication_search_result,
            candidates=clean_candidates,
            category_guidance=category_guidance,
        )
        if llm_candidate_review and quality_choice_policy.get("requires_user_choice"):
            policy_ids = [str(cid) for cid in (quality_choice_policy.get("candidate_ids") or []) if cid]
            recommended = [str(cid) for cid in (llm_candidate_review.get("recommended_candidate_ids") or []) if cid]
            merged: list[str] = []
            for cid in recommended + policy_ids:
                if cid and cid not in merged:
                    merged.append(cid)
            llm_candidate_review["recommended_candidate_ids"] = merged[:8]
            llm_candidate_review["should_queue_now"] = False
            llm_candidate_review["needs_user_choice"] = True
            note = quality_choice_policy.get("message") or "Multiple quality/size options need a user choice before queueing."
            reason = str(llm_candidate_review.get("reason") or "").strip()
            llm_candidate_review["reason"] = (reason + " " + note).strip()[:500]
            llm_candidate_review["answer_hint"] = (
                "Present the quality/size options from quality_choice_policy. Do not queue one candidate until the user chooses a quality profile."
            )
        llm_candidate_review_status = (
            "reviewed" if llm_candidate_review else (
                "skipped_no_task_llm" if not self._candidate_adjudicator.available else (
                    "skipped_no_candidates" if not clean_candidates else "review_unavailable_or_failed"
                )
            )
        )
        if llm_candidate_review:
            clean_candidates = DownloadCandidateAdjudicator.reorder_candidates(clean_candidates, llm_candidate_review)
            cache_candidates = DownloadCandidateAdjudicator.reorder_candidates(cache_candidates, llm_candidate_review)
            for i, candidate in enumerate(clean_candidates, 1):
                candidate["index"] = i
                if str(candidate.get("candidate_id") or "") in set(llm_candidate_review.get("recommended_candidate_ids") or []):
                    candidate["llm_recommended"] = True
            for i, candidate in enumerate(cache_candidates, 1):
                candidate["index"] = i
                if str(candidate.get("candidate_id") or "") in set(llm_candidate_review.get("recommended_candidate_ids") or []):
                    candidate["llm_recommended"] = True
            logger.info(
                "search_media_torrents LLM candidate review: name=%r recommended=%s confidence=%s reason=%r",
                res.get("name") or name,
                llm_candidate_review.get("recommended_candidate_ids"),
                llm_candidate_review.get("confidence"),
                llm_candidate_review.get("reason"),
            )

        suppress_batch_recommendation = SearchBatchRecommendationBuilder.should_suppress(
            batch_recommendation=batch_recommendation,
            candidates=clean_candidates,
            llm_candidate_review=llm_candidate_review,
            quality_choice_policy=quality_choice_policy,
        )
        if suppress_batch_recommendation:
            logger.info(
                "search_media_torrents: suppressing deterministic batch_recommendation because a reviewed/requested bundle or quality-choice workspace is present name=%r result_set_id=%s",
                res.get("name") or name,
                result_set_id,
            )
            res["batch_recommendation_suppressed"] = True
            batch_recommendation = None

        SearchWorkspaceAuditLogger.log(
            name=res.get("name") or name,
            display_name=res.get("display_name") or res.get("name") or name,
            category_id=res.get("category_id"),
            season=res.get("season", season),
            episode=res.get("episode", episode),
            language=res.get("language") or language,
            search_scope=res.get("search_scope") or search_scope,
            query=res.get("query"),
            result_set_id=result_set_id,
            raw_candidate_count=len(res.get("candidates") or []),
            clean_candidates=clean_candidates,
            quality_choice_policy=quality_choice_policy,
            llm_candidate_review=llm_candidate_review,
            llm_candidate_review_status=llm_candidate_review_status,
            next_actions_preview=SearchWorkspaceNextActions.build(
                candidates=clean_candidates,
                search_scope=res.get("search_scope") or search_scope,
                result_set_id=result_set_id,
                has_batch=bool(batch_recommendation),
                quality_choice_policy=quality_choice_policy,
            ),
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
            "quality_choice_policy": quality_choice_policy,
            "companion_soulseek": res.get("companion_soulseek") if isinstance(res.get("companion_soulseek"), dict) else {},
            "llm_candidate_review": llm_candidate_review,
            "llm_candidate_review_status": llm_candidate_review_status,
        }

        db = getattr(self._scheduler, "_db", None)
        if db:
            try:
                await store_result_set(db, session_id=session_id, cache_data=cache_data)
            except Exception as e:
                logger.warning(f"Failed to cache search_media_torrents options: {e}")

        selected_for_estimate = SearchQualityChoicePolicy.selected_candidate_ids_for_estimate(
            clean_candidates,
            batch_recommendation=batch_recommendation,
            search_scope=res.get("search_scope") or search_scope,
        )
        estimated_total_size_bytes = SearchQualityChoicePolicy.estimated_total_size_bytes(clean_candidates, selected_for_estimate)

        res["result_set_id"] = result_set_id
        res["search_scope"] = res.get("search_scope") or search_scope
        res["candidate_count"] = len(clean_candidates)
        res["estimated_total_size_bytes"] = estimated_total_size_bytes
        res["results_total_size_gb"] = round(estimated_total_size_bytes / (1024 ** 3), 3) if estimated_total_size_bytes else 0
        if quality_choice_policy:
            res["quality_choice_policy"] = quality_choice_policy
        res["llm_candidate_review_status"] = llm_candidate_review_status
        if llm_candidate_review:
            res["llm_candidate_review"] = llm_candidate_review
            recommended_ids = llm_candidate_review.get("recommended_candidate_ids") or []
            if recommended_ids:
                res["recommended_candidate_id"] = recommended_ids[0]
        res["candidate_picker"] = SearchWorkspaceFormatter.candidate_picker_rows(clean_candidates, limit=60)
        res["result_handle"] = {
            "type": "torrent_result_set",
            "result_set_id": result_set_id,
            "name": res.get("display_name") or res.get("name") or name,
            "category_id": res.get("category_id"),
            "search_scope": res.get("search_scope") or search_scope,
        }
        res["next_actions"] = SearchWorkspaceNextActions.build(
            candidates=clean_candidates,
            search_scope=res.get("search_scope") or search_scope,
            result_set_id=result_set_id,
            has_batch=bool(batch_recommendation),
            quality_choice_policy=quality_choice_policy,
        )
        if llm_candidate_review and llm_candidate_review.get("recommended_candidate_ids"):
            review_ids = [str(cid) for cid in (llm_candidate_review.get("recommended_candidate_ids") or []) if cid]
            first_review_id = review_ids[0] if review_ids else ""
            should_queue_now = bool(llm_candidate_review.get("should_queue_now")) and not bool(llm_candidate_review.get("needs_user_choice"))
            res["next_actions"].insert(0, {
                "action": "queue_llm_recommended_candidate" if should_queue_now else "review_llm_recommended_candidate",
                "tool": "queue_download" if should_queue_now else "inspect_torrent_candidate",
                "reason": llm_candidate_review.get("answer_hint") or llm_candidate_review.get("reason") or "The torrent-candidate review identified the best semantic match for the user request.",
                "candidate_ids": review_ids,
                "args_hint": {"result_set_id": result_set_id, "candidate_id": first_review_id},
            })
            if should_queue_now and first_review_id:
                res["llm_next_action"] = (
                    "The torrent-candidate review selected a clear match and said it can be queued now. "
                    f"Call queue_download with result_set_id={result_set_id!r} and candidate_id={first_review_id!r}. "
                    "Do not summarize lower-ranked alternatives as missing units unless the tool result explicitly says no requested-bundle candidate was recommended."
                )
        companion = res.get("companion_soulseek") if isinstance(res.get("companion_soulseek"), dict) else {}
        if companion:
            full_soulseek_candidates = companion.get("candidates") if isinstance(companion.get("candidates"), list) else []
            compact_soulseek_candidates = SearchWorkspaceFormatter.compact_soulseek_candidates(full_soulseek_candidates, result_set_id=result_set_id, limit=12)
            companion["candidate_picker"] = compact_soulseek_candidates
            companion["recommended_candidate_id"] = companion.get("recommended_candidate_id") or SearchWorkspaceFormatter.best_soulseek_candidate_id(full_soulseek_candidates)
            # The full exact filenames are already serialized in the cached result set above.
            # Keep the LLM-facing tool result compact so long source folders do not blow the token budget.
            companion["candidates"] = compact_soulseek_candidates
            companion["full_candidates_cached"] = True
            res["soulseek_candidate_picker"] = compact_soulseek_candidates
            res["soulseek_summary"] = {
                "enabled": companion.get("enabled"),
                "status": companion.get("status"),
                "candidate_count": companion.get("candidate_count", 0),
                "queries": companion.get("queries") or ([companion.get("query")] if companion.get("query") else []),
                "raw_response_count": companion.get("raw_response_count"),
                "raw_file_count": companion.get("raw_file_count"),
                "normalized_file_rows": ((companion.get("filtering") or {}).get("total_file_rows") if isinstance(companion.get("filtering"), dict) else None),
                "recommended_candidate_id": companion.get("recommended_candidate_id"),
                "queue_tool": "enqueue_soulseek_download",
                "error": companion.get("error"),
            }
        await self._maybe_schedule_unmatched_retry(
            res=res,
            name=res.get("display_name") or res.get("name") or name,
            category_id=res.get("category_id") or category_id,
            search_scope=res.get("search_scope") or search_scope,
            context=context,
        )
        soulseek_candidate_count = int(companion.get("candidate_count") or 0) if isinstance(companion, dict) else 0
        torrent_candidate_count = len(clean_candidates)
        res["torrent_candidate_count"] = torrent_candidate_count
        res["soulseek_candidate_count"] = soulseek_candidate_count
        res["downloadable_candidate_count"] = torrent_candidate_count + soulseek_candidate_count
        if soulseek_candidate_count and not torrent_candidate_count:
            res["source_result_status"] = "soulseek_only_candidates_found"
            res["agent_instruction"] = (
                "No torrent candidate matched, but Soulseek returned queueable candidates. "
                "Do not tell the user that nothing was found. Present the Soulseek options or, after user confirmation, "
                "call enqueue_soulseek_download with a candidate_id and result_set_id from soulseek_candidate_picker."
            )
            res["llm_next_action"] = res.get("llm_next_action") or res["agent_instruction"]
        elif soulseek_candidate_count:
            res["source_result_status"] = "torrent_and_soulseek_candidates_found"
        elif torrent_candidate_count:
            res["source_result_status"] = "torrent_candidates_found"
        else:
            res["source_result_status"] = "no_candidates_found"

        if soulseek_candidate_count:
            res["next_actions"].append({
                "action": "evaluate_soulseek_candidates",
                "tool": "enqueue_soulseek_download",
                "reason": "Soulseek companion search returned queueable candidates." + (" Torrent search returned none." if not torrent_candidate_count else ""),
                "args_hint": {"candidate_id": companion.get("recommended_candidate_id") or "<soulseek candidate_id>", "result_set_id": result_set_id},
            })
            res["llm_source_note"] = (
                "Torrent and Soulseek results are source-specific. torrent_candidate_count counts torrent rows only; "
                "soulseek_candidate_count counts queueable slskd rows. If torrent_candidate_count is 0 but soulseek_candidate_count is > 0, "
                "the search DID find Soulseek candidates. Use queue_download only for torrent candidate_id values and "
                "enqueue_soulseek_download with Soulseek candidate_id/result_set_id. Do not copy long filename arrays into tool arguments; "
                "the tool resolves them from the cached result set."
            )
        elif companion.get("status") == "account_not_ready":
            res["next_actions"].append({
                "action": "fix_soulseek_account",
                "tool": "get_soulseek_share_plan",
                "reason": companion.get("error") or "Soulseek is enabled but account login is not ready.",
            })
        res["search_summary"] = {
            "query": res.get("query"),
            "torrent_candidate_count": torrent_candidate_count,
            "soulseek_candidate_count": soulseek_candidate_count,
            "downloadable_candidate_count": torrent_candidate_count + soulseek_candidate_count,
            "source_result_status": res.get("source_result_status"),
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
            if not (llm_candidate_review and llm_candidate_review.get("recommended_candidate_ids")):
                res["llm_next_action"] = (
                    "The user asked for a multi-unit download. Queue every recommended candidate by calling "
                    "queue_download with batch_recommendation.queue_download_arguments. Do not queue only the first unit."
                )
        return res



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
        llm_client: object | None = None,
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
        self._llm_client = llm_client

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
            SearchMediaTorrentsTool(scheduler=self._scheduler, llm_client=getattr(self, "_llm_client", None)),
        ]
