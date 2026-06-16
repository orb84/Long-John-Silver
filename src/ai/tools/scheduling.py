"""
Scheduling and recurring task tools for LJS.

Declarative AgentTool implementations for creating, listing,
and removing scheduled tasks, as well as immediate show checking.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional, TYPE_CHECKING

from loguru import logger

from src.ai.tools.base import AgentTool
from src.ai.media_title_repair import MediaTitleRepair
from src.ai.download_candidate_adjudicator import DownloadCandidateAdjudicator
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

def _compact_soulseek_candidates(candidates: list[dict[str, Any]], *, result_set_id: str = "", limit: int = 12) -> list[dict[str, Any]]:
    """Return compact Soulseek rows for the LLM while cache keeps full filenames."""
    compact: list[dict[str, Any]] = []
    for raw in (candidates or [])[:limit]:
        if not isinstance(raw, dict):
            continue
        filenames = [str(v) for v in (raw.get("filenames") or []) if str(v).strip()]
        audio = [str(v) for v in (raw.get("audio_filenames") or []) if str(v).strip()]
        support = [str(v) for v in (raw.get("supporting_filenames") or []) if str(v).strip()]
        row = {
            "index": raw.get("index"),
            "candidate_id": raw.get("candidate_id"),
            "result_set_id": result_set_id,
            "source": "slskd",
            "candidate_type": raw.get("candidate_type") or ("folder" if filenames else "file"),
            "username": raw.get("username"),
            "folder": raw.get("folder"),
            "filename": raw.get("filename"),
            "file_count": raw.get("file_count") or (len(filenames) if filenames else None),
            "audio_file_count": raw.get("audio_file_count") or (len(audio) if audio else None),
            "supporting_file_count": raw.get("supporting_file_count") or (len(support) if support else None),
            "size_bytes": raw.get("size_bytes"),
            "size": _format_size(raw.get("size_bytes") if isinstance(raw.get("size_bytes"), int) else None),
            "bitrate": raw.get("bitrate"),
            "extension": raw.get("extension"),
            "has_free_upload_slot": raw.get("has_free_upload_slot"),
            "queue_length": raw.get("queue_length"),
            "folder_relevance": raw.get("folder_relevance"),
            "folder_query_match_score": raw.get("folder_query_match_score"),
            "sample_filenames": (audio or filenames or ([raw.get("filename")] if raw.get("filename") else []))[:6],
            "enqueue_hint": {
                "tool": "enqueue_soulseek_download",
                "candidate_id": raw.get("candidate_id"),
                "result_set_id": result_set_id,
            },
        }
        compact.append({k: v for k, v in row.items() if v not in (None, "", [], {})})
    return compact


def _best_soulseek_candidate_id(candidates: list[dict[str, Any]]) -> str:
    """Return the most likely Soulseek candidate id for a clear album/folder result."""
    if not candidates:
        return ""
    folders = [c for c in candidates if isinstance(c, dict) and c.get("candidate_type") == "folder"]
    strong = [c for c in folders if str(c.get("folder_relevance") or "").lower() in {"strong", "partial"}]
    chosen = (strong or folders or candidates)[0]
    return str(chosen.get("candidate_id") or "")


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

    async def _maybe_schedule_unmatched_retry(
        self,
        *,
        res: dict[str, Any],
        name: str,
        category_id: str | None,
        search_scope: str | None,
        context: ToolExecutionContext,
    ) -> None:
        """Create one automatic recurring retry when both backends return no match.

        Soulseek is peer-to-peer, so a query that returns nothing now can return
        matches later in the day.  This schedules a normal assistant condition
        check, deduplicated by search/category/scope, instead of adding a new
        bespoke scheduler subsystem.
        """
        if int(res.get("candidate_count") or 0) > 0:
            return
        companion = res.get("companion_soulseek") if isinstance(res.get("companion_soulseek"), dict) else {}
        if int(companion.get("candidate_count") or 0) > 0:
            return
        scheduler = self._scheduler
        prompt_scheduler = getattr(scheduler, "_prompt_scheduler", None) if scheduler is not None else None
        settings = getattr(getattr(scheduler, "_settings_manager", None), "settings", None) if scheduler is not None else None
        cfg = getattr(settings, "soulseek", None) if settings is not None else None
        if not prompt_scheduler or not cfg or not getattr(cfg, "auto_retry_unmatched_searches", True):
            return
        marker_src = f"{category_id or ''}:{name}:{search_scope or 'default'}"
        marker = "ljs:auto-retry-search:" + hashlib.sha256(marker_src.encode("utf-8")).hexdigest()[:16]
        try:
            existing = await prompt_scheduler.list_tasks(user_id=context.user_id)
            for task in existing:
                if getattr(task, "enabled", False) and marker in str(getattr(task, "prompt", "")):
                    res["deferred_search_retry"] = {
                        "scheduled": True,
                        "existing": True,
                        "task_id": getattr(task, "id", ""),
                        "interval_minutes": getattr(task, "interval_minutes", None),
                        "reason": "A recurring retry already exists for this missed search.",
                    }
                    return
            prompt = (
                f"[{marker}] Search again for {name!r} in category {category_id or 'auto'} using both torrents and Soulseek if configured. "
                f"Use concise Soulseek queries without words like album/track/download unless part of the title. "
                f"If a clear safe match appears and auto-download is enabled, queue it with the correct backend; otherwise notify me with the best candidates. "
                f"Original search_scope={search_scope or 'default'}."
            )
            task = await prompt_scheduler.create_task(
                prompt=prompt,
                interval_minutes=int(getattr(cfg, "retry_search_interval_minutes", 360) or 360),
                user_id=context.user_id,
                channel=context.source or "web",
                title=f"Retry search: {name}",
                task_type="condition_check",
                schedule_type="recurring",
                delay_minutes=int(getattr(cfg, "retry_search_interval_minutes", 360) or 360),
                max_runs=int(getattr(cfg, "retry_search_max_runs", 12) or 12),
                session_id=context.session_id,
            )
            res["deferred_search_retry"] = {
                "scheduled": True,
                "existing": False,
                "task_id": task.id,
                "interval_minutes": task.interval_minutes,
                "max_runs": task.max_runs,
                "reason": "No torrent or Soulseek candidates were found; LJS will retry automatically because P2P availability changes over time.",
            }
        except Exception as exc:
            logger.warning(f"Failed to schedule unmatched-search retry for {name!r}: {exc}")
            res["deferred_search_retry"] = {"scheduled": False, "error": str(exc)}

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

    async def _maybe_schedule_unmatched_retry(
        self,
        *,
        res: dict[str, Any],
        name: str,
        category_id: str | None,
        search_scope: str | None,
        context: ToolExecutionContext,
    ) -> None:
        """Create one automatic recurring retry when both backends return no match.

        Soulseek is peer-to-peer, so a query that returns nothing now can return
        matches later in the day.  This schedules a normal assistant condition
        check, deduplicated by search/category/scope, instead of adding a new
        bespoke scheduler subsystem.
        """
        if int(res.get("candidate_count") or 0) > 0:
            return
        companion = res.get("companion_soulseek") if isinstance(res.get("companion_soulseek"), dict) else {}
        if int(companion.get("candidate_count") or 0) > 0:
            return
        scheduler = self._scheduler
        prompt_scheduler = getattr(scheduler, "_prompt_scheduler", None) if scheduler is not None else None
        settings = getattr(getattr(scheduler, "_settings_manager", None), "settings", None) if scheduler is not None else None
        cfg = getattr(settings, "soulseek", None) if settings is not None else None
        if not prompt_scheduler or not cfg or not getattr(cfg, "auto_retry_unmatched_searches", True):
            return
        marker_src = f"{category_id or ''}:{name}:{search_scope or 'default'}"
        marker = "ljs:auto-retry-search:" + hashlib.sha256(marker_src.encode("utf-8")).hexdigest()[:16]
        try:
            existing = await prompt_scheduler.list_tasks(user_id=context.user_id)
            for task in existing:
                if getattr(task, "enabled", False) and marker in str(getattr(task, "prompt", "")):
                    res["deferred_search_retry"] = {
                        "scheduled": True,
                        "existing": True,
                        "task_id": getattr(task, "id", ""),
                        "interval_minutes": getattr(task, "interval_minutes", None),
                        "reason": "A recurring retry already exists for this missed search.",
                    }
                    return
            prompt = (
                f"[{marker}] Search again for {name!r} in category {category_id or 'auto'} using both torrents and Soulseek if configured. "
                f"Use concise Soulseek queries without words like album/track/download unless part of the title. "
                f"If a clear safe match appears and auto-download is enabled, queue it with the correct backend; otherwise notify me with the best candidates. "
                f"Original search_scope={search_scope or 'default'}."
            )
            task = await prompt_scheduler.create_task(
                prompt=prompt,
                interval_minutes=int(getattr(cfg, "retry_search_interval_minutes", 360) or 360),
                user_id=context.user_id,
                channel=context.source or "web",
                title=f"Retry search: {name}",
                task_type="condition_check",
                schedule_type="recurring",
                delay_minutes=int(getattr(cfg, "retry_search_interval_minutes", 360) or 360),
                max_runs=int(getattr(cfg, "retry_search_max_runs", 12) or 12),
                session_id=context.session_id,
            )
            res["deferred_search_retry"] = {
                "scheduled": True,
                "existing": False,
                "task_id": task.id,
                "interval_minutes": task.interval_minutes,
                "max_runs": task.max_runs,
                "reason": "No torrent or Soulseek candidates were found; LJS will retry automatically because P2P availability changes over time.",
            }
        except Exception as exc:
            logger.warning(f"Failed to schedule unmatched-search retry for {name!r}: {exc}")
            res["deferred_search_retry"] = {"scheduled": False, "error": str(exc)}

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

    async def _maybe_schedule_unmatched_retry(
        self,
        *,
        res: dict[str, Any],
        name: str,
        category_id: str | None,
        search_scope: str | None,
        context: ToolExecutionContext,
    ) -> None:
        """Create one automatic recurring retry when both backends return no match.

        Soulseek is peer-to-peer, so a query that returns nothing now can return
        matches later in the day.  This schedules a normal assistant condition
        check, deduplicated by search/category/scope, instead of adding a new
        bespoke scheduler subsystem.
        """
        if int(res.get("candidate_count") or 0) > 0:
            return
        companion = res.get("companion_soulseek") if isinstance(res.get("companion_soulseek"), dict) else {}
        if int(companion.get("candidate_count") or 0) > 0:
            return
        scheduler = self._scheduler
        prompt_scheduler = getattr(scheduler, "_prompt_scheduler", None) if scheduler is not None else None
        settings = getattr(getattr(scheduler, "_settings_manager", None), "settings", None) if scheduler is not None else None
        cfg = getattr(settings, "soulseek", None) if settings is not None else None
        if not prompt_scheduler or not cfg or not getattr(cfg, "auto_retry_unmatched_searches", True):
            return
        marker_src = f"{category_id or ''}:{name}:{search_scope or 'default'}"
        marker = "ljs:auto-retry-search:" + hashlib.sha256(marker_src.encode("utf-8")).hexdigest()[:16]
        try:
            existing = await prompt_scheduler.list_tasks(user_id=context.user_id)
            for task in existing:
                if getattr(task, "enabled", False) and marker in str(getattr(task, "prompt", "")):
                    res["deferred_search_retry"] = {
                        "scheduled": True,
                        "existing": True,
                        "task_id": getattr(task, "id", ""),
                        "interval_minutes": getattr(task, "interval_minutes", None),
                        "reason": "A recurring retry already exists for this missed search.",
                    }
                    return
            prompt = (
                f"[{marker}] Search again for {name!r} in category {category_id or 'auto'} using both torrents and Soulseek if configured. "
                f"Use concise Soulseek queries without words like album/track/download unless part of the title. "
                f"If a clear safe match appears and auto-download is enabled, queue it with the correct backend; otherwise notify me with the best candidates. "
                f"Original search_scope={search_scope or 'default'}."
            )
            task = await prompt_scheduler.create_task(
                prompt=prompt,
                interval_minutes=int(getattr(cfg, "retry_search_interval_minutes", 360) or 360),
                user_id=context.user_id,
                channel=context.source or "web",
                title=f"Retry search: {name}",
                task_type="condition_check",
                schedule_type="recurring",
                delay_minutes=int(getattr(cfg, "retry_search_interval_minutes", 360) or 360),
                max_runs=int(getattr(cfg, "retry_search_max_runs", 12) or 12),
                session_id=context.session_id,
            )
            res["deferred_search_retry"] = {
                "scheduled": True,
                "existing": False,
                "task_id": task.id,
                "interval_minutes": task.interval_minutes,
                "max_runs": task.max_runs,
                "reason": "No torrent or Soulseek candidates were found; LJS will retry automatically because P2P availability changes over time.",
            }
        except Exception as exc:
            logger.warning(f"Failed to schedule unmatched-search retry for {name!r}: {exc}")
            res["deferred_search_retry"] = {"scheduled": False, "error": str(exc)}

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

    async def _maybe_schedule_unmatched_retry(
        self,
        *,
        res: dict[str, Any],
        name: str,
        category_id: str | None,
        search_scope: str | None,
        context: ToolExecutionContext,
    ) -> None:
        """Create one automatic recurring retry when both backends return no match.

        Soulseek is peer-to-peer, so a query that returns nothing now can return
        matches later in the day.  This schedules a normal assistant condition
        check, deduplicated by search/category/scope, instead of adding a new
        bespoke scheduler subsystem.
        """
        if int(res.get("candidate_count") or 0) > 0:
            return
        companion = res.get("companion_soulseek") if isinstance(res.get("companion_soulseek"), dict) else {}
        if int(companion.get("candidate_count") or 0) > 0:
            return
        scheduler = self._scheduler
        prompt_scheduler = getattr(scheduler, "_prompt_scheduler", None) if scheduler is not None else None
        settings = getattr(getattr(scheduler, "_settings_manager", None), "settings", None) if scheduler is not None else None
        cfg = getattr(settings, "soulseek", None) if settings is not None else None
        if not prompt_scheduler or not cfg or not getattr(cfg, "auto_retry_unmatched_searches", True):
            return
        marker_src = f"{category_id or ''}:{name}:{search_scope or 'default'}"
        marker = "ljs:auto-retry-search:" + hashlib.sha256(marker_src.encode("utf-8")).hexdigest()[:16]
        try:
            existing = await prompt_scheduler.list_tasks(user_id=context.user_id)
            for task in existing:
                if getattr(task, "enabled", False) and marker in str(getattr(task, "prompt", "")):
                    res["deferred_search_retry"] = {
                        "scheduled": True,
                        "existing": True,
                        "task_id": getattr(task, "id", ""),
                        "interval_minutes": getattr(task, "interval_minutes", None),
                        "reason": "A recurring retry already exists for this missed search.",
                    }
                    return
            prompt = (
                f"[{marker}] Search again for {name!r} in category {category_id or 'auto'} using both torrents and Soulseek if configured. "
                f"Use concise Soulseek queries without words like album/track/download unless part of the title. "
                f"If a clear safe match appears and auto-download is enabled, queue it with the correct backend; otherwise notify me with the best candidates. "
                f"Original search_scope={search_scope or 'default'}."
            )
            task = await prompt_scheduler.create_task(
                prompt=prompt,
                interval_minutes=int(getattr(cfg, "retry_search_interval_minutes", 360) or 360),
                user_id=context.user_id,
                channel=context.source or "web",
                title=f"Retry search: {name}",
                task_type="condition_check",
                schedule_type="recurring",
                delay_minutes=int(getattr(cfg, "retry_search_interval_minutes", 360) or 360),
                max_runs=int(getattr(cfg, "retry_search_max_runs", 12) or 12),
                session_id=context.session_id,
            )
            res["deferred_search_retry"] = {
                "scheduled": True,
                "existing": False,
                "task_id": task.id,
                "interval_minutes": task.interval_minutes,
                "max_runs": task.max_runs,
                "reason": "No torrent or Soulseek candidates were found; LJS will retry automatically because P2P availability changes over time.",
            }
        except Exception as exc:
            logger.warning(f"Failed to schedule unmatched-search retry for {name!r}: {exc}")
            res["deferred_search_retry"] = {"scheduled": False, "error": str(exc)}

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

    async def _maybe_schedule_unmatched_retry(
        self,
        *,
        res: dict[str, Any],
        name: str,
        category_id: str | None,
        search_scope: str | None,
        context: ToolExecutionContext,
    ) -> None:
        """Create one automatic recurring retry when both backends return no match.

        Soulseek is peer-to-peer, so a query that returns nothing now can return
        matches later in the day.  This schedules a normal assistant condition
        check, deduplicated by search/category/scope, instead of adding a new
        bespoke scheduler subsystem.
        """
        if int(res.get("candidate_count") or 0) > 0:
            return
        companion = res.get("companion_soulseek") if isinstance(res.get("companion_soulseek"), dict) else {}
        if int(companion.get("candidate_count") or 0) > 0:
            return
        scheduler = self._scheduler
        prompt_scheduler = getattr(scheduler, "_prompt_scheduler", None) if scheduler is not None else None
        settings = getattr(getattr(scheduler, "_settings_manager", None), "settings", None) if scheduler is not None else None
        cfg = getattr(settings, "soulseek", None) if settings is not None else None
        if not prompt_scheduler or not cfg or not getattr(cfg, "auto_retry_unmatched_searches", True):
            return
        marker_src = f"{category_id or ''}:{name}:{search_scope or 'default'}"
        marker = "ljs:auto-retry-search:" + hashlib.sha256(marker_src.encode("utf-8")).hexdigest()[:16]
        try:
            existing = await prompt_scheduler.list_tasks(user_id=context.user_id)
            for task in existing:
                if getattr(task, "enabled", False) and marker in str(getattr(task, "prompt", "")):
                    res["deferred_search_retry"] = {
                        "scheduled": True,
                        "existing": True,
                        "task_id": getattr(task, "id", ""),
                        "interval_minutes": getattr(task, "interval_minutes", None),
                        "reason": "A recurring retry already exists for this missed search.",
                    }
                    return
            prompt = (
                f"[{marker}] Search again for {name!r} in category {category_id or 'auto'} using both torrents and Soulseek if configured. "
                f"Use concise Soulseek queries without words like album/track/download unless part of the title. "
                f"If a clear safe match appears and auto-download is enabled, queue it with the correct backend; otherwise notify me with the best candidates. "
                f"Original search_scope={search_scope or 'default'}."
            )
            task = await prompt_scheduler.create_task(
                prompt=prompt,
                interval_minutes=int(getattr(cfg, "retry_search_interval_minutes", 360) or 360),
                user_id=context.user_id,
                channel=context.source or "web",
                title=f"Retry search: {name}",
                task_type="condition_check",
                schedule_type="recurring",
                delay_minutes=int(getattr(cfg, "retry_search_interval_minutes", 360) or 360),
                max_runs=int(getattr(cfg, "retry_search_max_runs", 12) or 12),
                session_id=context.session_id,
            )
            res["deferred_search_retry"] = {
                "scheduled": True,
                "existing": False,
                "task_id": task.id,
                "interval_minutes": task.interval_minutes,
                "max_runs": task.max_runs,
                "reason": "No torrent or Soulseek candidates were found; LJS will retry automatically because P2P availability changes over time.",
            }
        except Exception as exc:
            logger.warning(f"Failed to schedule unmatched-search retry for {name!r}: {exc}")
            res["deferred_search_retry"] = {"scheduled": False, "error": str(exc)}

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
        "episode, chapter, disc, or track, use dedicated arguments rather than appending "
        "localized phrases to the name. Evaluate language only when the active category says language is relevant; "
        "music searches must not inherit global movie/TV language preferences. "
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

    async def _maybe_schedule_unmatched_retry(
        self,
        *,
        res: dict[str, Any],
        name: str,
        category_id: str | None,
        search_scope: str | None,
        context: ToolExecutionContext,
    ) -> None:
        """Create one automatic recurring retry when both backends return no match.

        Soulseek is peer-to-peer, so a query that returns nothing now can return
        matches later in the day.  This schedules a normal assistant condition
        check, deduplicated by search/category/scope, instead of adding a new
        bespoke scheduler subsystem.
        """
        if int(res.get("candidate_count") or 0) > 0:
            return
        companion = res.get("companion_soulseek") if isinstance(res.get("companion_soulseek"), dict) else {}
        if int(companion.get("candidate_count") or 0) > 0:
            return
        scheduler = self._scheduler
        prompt_scheduler = getattr(scheduler, "_prompt_scheduler", None) if scheduler is not None else None
        settings = getattr(getattr(scheduler, "_settings_manager", None), "settings", None) if scheduler is not None else None
        cfg = getattr(settings, "soulseek", None) if settings is not None else None
        if not prompt_scheduler or not cfg or not getattr(cfg, "auto_retry_unmatched_searches", True):
            return
        marker_src = f"{category_id or ''}:{name}:{search_scope or 'default'}"
        marker = "ljs:auto-retry-search:" + hashlib.sha256(marker_src.encode("utf-8")).hexdigest()[:16]
        try:
            existing = await prompt_scheduler.list_tasks(user_id=context.user_id)
            for task in existing:
                if getattr(task, "enabled", False) and marker in str(getattr(task, "prompt", "")):
                    res["deferred_search_retry"] = {
                        "scheduled": True,
                        "existing": True,
                        "task_id": getattr(task, "id", ""),
                        "interval_minutes": getattr(task, "interval_minutes", None),
                        "reason": "A recurring retry already exists for this missed search.",
                    }
                    return
            prompt = (
                f"[{marker}] Search again for {name!r} in category {category_id or 'auto'} using both torrents and Soulseek if configured. "
                f"Use concise Soulseek queries without words like album/track/download unless part of the title. "
                f"If a clear safe match appears and auto-download is enabled, queue it with the correct backend; otherwise notify me with the best candidates. "
                f"Original search_scope={search_scope or 'default'}."
            )
            task = await prompt_scheduler.create_task(
                prompt=prompt,
                interval_minutes=int(getattr(cfg, "retry_search_interval_minutes", 360) or 360),
                user_id=context.user_id,
                channel=context.source or "web",
                title=f"Retry search: {name}",
                task_type="condition_check",
                schedule_type="recurring",
                delay_minutes=int(getattr(cfg, "retry_search_interval_minutes", 360) or 360),
                max_runs=int(getattr(cfg, "retry_search_max_runs", 12) or 12),
                session_id=context.session_id,
            )
            res["deferred_search_retry"] = {
                "scheduled": True,
                "existing": False,
                "task_id": task.id,
                "interval_minutes": task.interval_minutes,
                "max_runs": task.max_runs,
                "reason": "No torrent or Soulseek candidates were found; LJS will retry automatically because P2P availability changes over time.",
            }
        except Exception as exc:
            logger.warning(f"Failed to schedule unmatched-search retry for {name!r}: {exc}")
            res["deferred_search_retry"] = {"scheduled": False, "error": str(exc)}

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
                    "description": "Media/download language only, not chat language. Set this only when the user explicitly asks for an audio/subtitle/translation language, or when category/tracked-item context supplies a configured media language. Omit it to let LJS apply tracked/category/global media language defaults. Never set it merely because the user wrote the chat message in English/Italian/etc.",
                },
                "language_is_explicit": {
                    "type": "boolean",
                    "description": "True only when the user explicitly requested this media/download language in the current request. Omit or false for language inferred from the chat/reply language or for ordinary default preference handling.",
                },
                "search_scope": {
                    "type": "string",
                    "enum": ["default", "bundle_preferred", "bundle_only", "individual_units_only"],
                    "description": "Category-neutral search phase preference. Use bundle_preferred when the user asks for a whole TV show/season without a specific episode, or asks to prefer a complete category-owned bundle/pack but can fall back to individual units; use bundle_only only when the user explicitly wants bundle-only.",
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
        repaired_name = MediaTitleRepair.recover_literal_title(name, getattr(context, "user_prompt", None))
        if repaired_name and repaired_name != name:
            logger.info(
                "search_media_torrents: repaired lossy LLM title %r -> %r from current user prompt",
                name,
                repaired_name,
            )
            name = repaired_name
        season = arguments.get("season")
        episode = arguments.get("episode")
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
        search_scope = arguments.get("search_scope") or "default"
        category_id = str(arguments.get("category_id") or "").strip() or None
        session_id = context.session_id or "default"
        if not self._scheduler:
            return {"error": "Scheduler not available"}
        
        search_constraints = _search_constraints_from_arguments(arguments)
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
                "size_bytes": c.get("size_bytes"),
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
        batch_recommendation = _build_batch_recommendation(
            name=res.get("name") or name,
            category_id=res.get("category_id"),
            season=res.get("season", season),
            episode=res.get("episode", episode),
            search_scope=res.get("search_scope") or search_scope,
            result_set_id=result_set_id,
            candidates=cache_candidates,
            category=category,
            preferred_language=res.get("language") or language,
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
                "per_episode_size": _format_size(c.get("per_episode_size_bytes")),
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

        _annotate_selection_policy(clean_candidates, preferred_language=res.get("language") or language, language_is_explicit=language_is_explicit)
        for cache_candidate in cache_candidates:
            clean_match = next((c for c in clean_candidates if c.get("candidate_id") == cache_candidate.get("candidate_id")), None)
            if clean_match:
                cache_candidate["selection_warnings"] = clean_match.get("selection_warnings") or []
                cache_candidate["selection_blockers"] = clean_match.get("selection_blockers") or []
                cache_candidate["auto_queue_allowed"] = clean_match.get("auto_queue_allowed")
                cache_candidate["auto_queue_blocked_reason"] = clean_match.get("auto_queue_blocked_reason")

        quality_choice_policy = _quality_choice_policy(clean_candidates, search_constraints)
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

        suppress_batch_recommendation = _should_suppress_batch_recommendation(
            batch_recommendation=batch_recommendation,
            candidates=clean_candidates,
            llm_candidate_review=llm_candidate_review,
            quality_choice_policy=quality_choice_policy,
        )
        if suppress_batch_recommendation:
            logger.info(
                "search_media_torrents: suppressing deterministic batch_recommendation because a reviewed/requested season-pack or quality-choice workspace is present name=%r result_set_id=%s",
                res.get("name") or name,
                result_set_id,
            )
            res["batch_recommendation_suppressed"] = True
            batch_recommendation = None

        _log_search_media_torrents_audit(
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
            next_actions_preview=_search_result_next_actions(
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
        if quality_choice_policy:
            res["quality_choice_policy"] = quality_choice_policy
        res["llm_candidate_review_status"] = llm_candidate_review_status
        if llm_candidate_review:
            res["llm_candidate_review"] = llm_candidate_review
            recommended_ids = llm_candidate_review.get("recommended_candidate_ids") or []
            if recommended_ids:
                res["recommended_candidate_id"] = recommended_ids[0]
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
                    "Do not summarize lower-ranked alternatives as missing episodes unless the tool result explicitly says no season-pack candidate was recommended."
                )
        companion = res.get("companion_soulseek") if isinstance(res.get("companion_soulseek"), dict) else {}
        if companion:
            full_soulseek_candidates = companion.get("candidates") if isinstance(companion.get("candidates"), list) else []
            compact_soulseek_candidates = _compact_soulseek_candidates(full_soulseek_candidates, result_set_id=result_set_id, limit=12)
            companion["candidate_picker"] = compact_soulseek_candidates
            companion["recommended_candidate_id"] = companion.get("recommended_candidate_id") or _best_soulseek_candidate_id(full_soulseek_candidates)
            # The full exact filenames are already serialized in the cached result set above.
            # Keep the LLM-facing tool result compact so long album folders do not blow the token budget.
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
                    "queue_download with batch_recommendation.queue_download_arguments. Do not queue only the first episode."
                )
        return res


def _should_suppress_batch_recommendation(
    *,
    batch_recommendation: dict[str, Any] | None,
    candidates: list[dict[str, Any]],
    llm_candidate_review: dict[str, Any] | None,
    quality_choice_policy: dict[str, Any] | None,
) -> bool:
    """Avoid contradictory per-episode batch plans when a pack/choice is the real workspace."""
    if not batch_recommendation:
        return False
    if quality_choice_policy and quality_choice_policy.get("requires_user_choice"):
        return True
    recommended = {str(cid) for cid in ((llm_candidate_review or {}).get("recommended_candidate_ids") or []) if cid}
    if not recommended:
        return False
    by_id = {str(c.get("candidate_id") or ""): c for c in candidates}
    for candidate_id in recommended:
        candidate = by_id.get(candidate_id) or {}
        granularity = str((candidate.get("unit_descriptor") or {}).get("granularity") or "").lower()
        is_season_pack = bool(
            candidate.get("is_bundle")
            or candidate.get("bundle_scope")
            or candidate.get("pack_type")
            or granularity == "season"
        )
        full_coverage = (
            not candidate.get("requested_season_coverage")
            or candidate.get("requested_season_coverage") == "full_requested_season"
        )
        if is_season_pack and full_coverage:
            return True
    return False


def _search_constraints_from_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Extract optional size/resolution constraints from search_media_torrents arguments."""
    constraints: dict[str, Any] = {}
    for key in ("target_size_gb", "max_size_gb", "min_size_gb", "current_size_gb"):
        value = arguments.get(key)
        if value in (None, ""):
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            constraints[key] = parsed
    for key in ("target_bitrate_kbps", "preferred_bitrate_kbps", "max_bitrate_kbps", "current_bitrate_kbps"):
        value = arguments.get(key)
        if value in (None, ""):
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            constraints[key] = parsed
    for key in ("preferred_resolution", "required_resolution"):
        value = str(arguments.get(key) or "").strip()
        if value:
            constraints[key] = value
    for key in ("smaller_than_current", "preserve_resolution"):
        if arguments.get(key) is not None:
            constraints[key] = bool(arguments.get(key))
    if constraints.get("smaller_than_current") and not constraints.get("size_mode"):
        constraints["size_mode"] = "smaller"
    if constraints and "preserve_resolution" not in constraints:
        constraints["preserve_resolution"] = True
    return constraints

def _annotate_selection_policy(candidates: list[dict[str, Any]], *, preferred_language: str | None = None, language_is_explicit: bool = False) -> None:
    """Mark candidates that should not be queued without user confirmation.

    This is a narrow safety layer for the tool contract: it does not try to
    semantically choose the best torrent, but it prevents a low-availability or
    wrong-language row from being advertised as a clear one-click choice.
    """
    preferred = _canonical_language_token(preferred_language) if preferred_language else ""
    for candidate in candidates:
        warnings: list[str] = list(candidate.get("selection_warnings") or [])
        blockers: list[str] = list(candidate.get("selection_blockers") or [])
        seeders = _safe_int(candidate.get("seeders"))
        candidate["availability_seeders"] = seeders
        if seeders <= 0:
            blockers.append("no seeder count reported")
        elif seeders < 5:
            blockers.append(f"very low seeders ({seeders})")
        elif seeders < 10:
            warnings.append(f"low seeders ({seeders})")

        if preferred:
            languages = candidate.get("languages") or []
            if isinstance(languages, str):
                languages = [languages]
            normalized = {_canonical_language_token(lang) for lang in languages if lang}
            title = str(candidate.get("title") or "")
            title_has_preferred = _title_has_language_token(title, preferred)
            multi = "multi" in normalized or "multi" in title.lower() or "dual" in title.lower()
            if normalized and preferred not in normalized and not multi and not title_has_preferred:
                candidate["language_preference_status"] = "mismatch"
                blockers.append(f"does not advertise preferred media language {preferred_language}")
            elif preferred == "english" and normalized and preferred in normalized:
                extras = {lang for lang in normalized if lang not in {preferred, "multi"}}
                if extras or multi:
                    candidate["language_preference_status"] = "preferred_with_extra_audio"
                    warnings.append(
                        "advertises extra non-preferred audio languages; keep as fallback behind English/unknown-language candidates"
                    )
                else:
                    candidate["language_preference_status"] = "preferred_only"
            elif not normalized and not title_has_preferred:
                candidate["language_preference_status"] = "unknown_acceptable"
                message = f"language not advertised; preferred media language is {preferred_language}"
                if language_is_explicit and preferred != "english":
                    blockers.append(message)
                else:
                    warnings.append(message)
            elif title_has_preferred:
                candidate["language_preference_status"] = "preferred_by_title"
            elif multi:
                candidate["language_preference_status"] = "multi_language_fallback"
        elif "language_preference_status" not in candidate:
            candidate["language_preference_status"] = "not_applicable"

        bundle_context = candidate.get("bundle_context") or {}
        if isinstance(bundle_context, dict) and bundle_context.get("selective_download_required"):
            warnings.append("contains extra TV units; inspect/select only the requested season files before queueing")
            blockers.append("requires selective file inspection before queueing")

        candidate["selection_warnings"] = warnings
        candidate["selection_blockers"] = blockers
        if blockers:
            candidate["auto_queue_allowed"] = False
            candidate["auto_queue_blocked_reason"] = "; ".join(blockers)
        else:
            candidate["auto_queue_allowed"] = True
            candidate["auto_queue_blocked_reason"] = ""


def _title_has_language_token(title: str, preferred_token: str) -> bool:
    import re
    title_lower = str(title or "").lower()
    if preferred_token == "italian":
        return bool(re.search(r"(?:^|[\s._\-\[\]()])(?:ita|italian|italiano)(?:$|[\s._\-\[\]()])", title_lower, re.I))
    if preferred_token == "english":
        return bool(re.search(r"(?:^|[\s._\-\[\]()])(?:eng|english)(?:$|[\s._\-\[\]()])", title_lower, re.I))
    return preferred_token in title_lower


def _canonical_language_token(value: object) -> str:
    token = str(value or "").strip().lower()
    aliases = {
        "italian": "italian", "italiano": "italian", "ita": "italian", "it": "italian",
        "english": "english", "eng": "english", "en": "english",
        "multi": "multi", "multilanguage": "multi", "multi-audio": "multi", "dual": "multi",
    }
    return aliases.get(token, token)


def _candidate_logical_unit_key(candidate: dict[str, Any]) -> str:
    descriptor = candidate.get("unit_descriptor") if isinstance(candidate.get("unit_descriptor"), dict) else {}
    stable = str(descriptor.get("stable_key") or "").strip()
    if stable:
        return stable
    coords = descriptor.get("coordinates") if isinstance(descriptor.get("coordinates"), dict) else {}
    try:
        season = int(coords.get("season") or candidate.get("season") or 0)
    except (TypeError, ValueError):
        season = 0
    try:
        episode = int(coords.get("episode") or candidate.get("episode") or 0)
    except (TypeError, ValueError):
        episode = 0
    if season and episode:
        return f"S{season:02d}E{episode:02d}"
    if season:
        return f"S{season:02d}"
    return ""


def _quality_scope_key(candidate: dict[str, Any]) -> tuple[str, str]:
    coverage = str(candidate.get("requested_season_coverage") or "")
    if coverage == "full_requested_season":
        return ("season", _candidate_logical_unit_key(candidate))
    if candidate.get("is_bundle") or candidate.get("bundle_scope") or candidate.get("pack_type"):
        return ("bundle", _candidate_logical_unit_key(candidate))
    return ("unit", _candidate_logical_unit_key(candidate))


def _quality_same_scope_group(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not candidates:
        return []
    first_key = _quality_scope_key(candidates[0])
    if not first_key[1]:
        return candidates[:1]
    return [candidate for candidate in candidates if _quality_scope_key(candidate) == first_key]


def _quality_choice_policy(candidates: list[dict[str, Any]], constraints: dict[str, Any] | None = None) -> dict[str, Any]:
    """Detect when the user must choose a quality/size profile.

    Deterministic code must not silently establish a low-bitrate preference for
    a show merely because one candidate is queueable.  Earlier versions only
    compared same-resolution rows, which missed the important real-world case:
    a compact 1080p HEVC full-season pack versus a much larger 720p/x264 pack.
    Those are distinct quality/size tradeoffs and must be shown as options when
    the user has not supplied or saved a bitrate/size target.
    """
    constraints = constraints or {}
    if any(constraints.get(k) for k in ("target_bitrate_kbps", "preferred_bitrate_kbps", "max_bitrate_kbps", "current_bitrate_kbps")):
        return {"requires_user_choice": False, "reason": "bitrate preference already supplied"}
    if any(constraints.get(k) for k in ("target_size_gb", "max_size_gb", "min_size_gb", "current_size_gb")):
        return {"requires_user_choice": False, "reason": "size preference already supplied"}

    viable = [c for c in candidates if c.get("auto_queue_allowed") is not False and c.get("estimated_bitrate_kbps") and c.get("resolution")]
    explicit_resolution = str(constraints.get("required_resolution") or constraints.get("preferred_resolution") or "").strip().lower()
    if explicit_resolution:
        viable = [c for c in viable if str(c.get("resolution") or "").strip().lower() == explicit_resolution]
    if len(viable) < 2:
        return {"requires_user_choice": False}

    # For a full-season/bundle request, compare all requested-language pack
    # candidates that cover the requested season, even across resolutions/codecs.
    # This is where size/bitrate tradeoffs matter most and where one wrong
    # automatic pick can set an undesired quality baseline for an entire show.
    bundle_group = [
        c for c in viable
        if (
            c.get("is_bundle")
            or c.get("bundle_scope")
            or c.get("pack_type")
            or str((c.get("unit_descriptor") or {}).get("granularity") or "").lower() == "season"
        )
        and (
            not c.get("requested_season_coverage")
            or c.get("requested_season_coverage") == "full_requested_season"
        )
    ]
    bundle_policy = _material_quality_choice_policy(
        bundle_group,
        reason="no_saved_season_pack_quality_preference",
        message=(
            "Multiple matching season-pack candidates differ materially in resolution/codec/bitrate/size; "
            "present the quality options instead of auto-queueing a compact pack as the default."
        ),
        tradeoff_type="season_pack_quality_tradeoff",
    )
    if bundle_policy.get("requires_user_choice"):
        return bundle_policy

    # Otherwise compare within the top same-resolution and same-logical-unit
    # group for exact episodes and non-bundle searches.  A season request that
    # fell back to individual files must not compare S01E01, S01E04, and S01E08
    # as if they were alternative encodes of the same thing; that creates fake
    # bitrate choices and hides the fact that the season still needs per-episode
    # coverage.
    resolution = viable[0].get("resolution")
    group = [c for c in viable if c.get("resolution") == resolution]
    group = _quality_same_scope_group(group)
    return _material_quality_choice_policy(
        group[:6],
        reason="no_saved_bitrate_preference",
        message=(
            "Multiple same-resolution candidates differ materially in bitrate/size; ask the user which "
            "quality-size tradeoff to use for this show, then store that bitrate preference when they choose."
        ),
        tradeoff_type="same_resolution_bitrate_tradeoff",
    )


def _collapse_equivalent_quality_options(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse duplicate quality choices, preferring better seeder health.

    This is only for deciding whether the user must choose a quality profile.
    It must not remove candidates from the cache/workspace; it simply prevents
    two mirrors of the same release from becoming a pointless follow-up.
    """
    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    ordered_keys: list[tuple[Any, ...]] = []
    for candidate in candidates:
        key = _quality_equivalence_key(candidate)
        existing = grouped.get(key)
        if existing is None:
            grouped[key] = candidate
            ordered_keys.append(key)
            continue
        if _quality_option_health_key(candidate) > _quality_option_health_key(existing):
            grouped[key] = candidate
    return [grouped[key] for key in ordered_keys if key in grouped]


def _quality_equivalence_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
    unit = candidate.get("unit_descriptor") or {}
    coords = unit.get("coordinates") if isinstance(unit.get("coordinates"), dict) else {}
    languages = tuple(sorted(str(lang).lower() for lang in (candidate.get("languages") or []) if lang))
    size_bucket = None
    try:
        size = int(candidate.get("size_bytes") or 0)
        if size > 0:
            size_bucket = round(size / max(size * 0 + 128 * 1024 * 1024, 1))
    except Exception:
        size_bucket = None
    bitrate_bucket = None
    try:
        bitrate = float(candidate.get("estimated_bitrate_kbps") or 0)
        if bitrate > 0:
            bitrate_bucket = round(bitrate / 200.0)
    except Exception:
        bitrate_bucket = None
    return (
        str(unit.get("stable_key") or ""),
        coords.get("season"),
        coords.get("episode"),
        str(candidate.get("resolution") or "").lower(),
        str(candidate.get("codec") or "").lower(),
        tuple(languages),
        str(candidate.get("requested_season_coverage") or ""),
        int(candidate.get("bundle_unit_count") or 0),
        size_bucket,
        bitrate_bucket,
    )


def _quality_option_health_key(candidate: dict[str, Any]) -> tuple[int, int, int]:
    seeders = _safe_int(candidate.get("seeders"))
    has_seeders = 1 if candidate.get("seeders") is not None else 0
    index = _safe_int(candidate.get("index")) or 9999
    return (has_seeders, seeders, -index)


def _quality_language_preference_rank(candidate: dict[str, Any]) -> int:
    status = str(candidate.get("language_preference_status") or "").lower()
    return {
        "preferred_only": 5,
        "preferred_by_title": 5,
        "unknown_acceptable": 4,
        "preferred_with_extra_audio": 3,
        "multi_language_fallback": 2,
        "not_applicable": 1,
        "mismatch": -100,
    }.get(status, 1)


def _quality_availability_bucket(candidate: dict[str, Any]) -> int:
    seeders = _safe_int(candidate.get("seeders"))
    if seeders >= 100:
        return 5
    if seeders >= 30:
        return 4
    if seeders >= 10:
        return 3
    if seeders >= 5:
        return 2
    if seeders > 0:
        return 1
    return 0


def _material_quality_choice_policy(candidates: list[dict[str, Any]], *, reason: str, message: str, tradeoff_type: str) -> dict[str, Any]:
    """Return a choice policy when candidates have materially different quality/size profiles.

    Functionally equivalent duplicates from different indexers are not a user
    preference question.  Collapse them first and keep the healthier swarm so
    the user is asked only about real tradeoffs: resolution, bitrate, codec,
    size, language, or coverage.
    """
    candidates = _collapse_equivalent_quality_options(candidates)
    if len(candidates) < 2:
        return {"requires_user_choice": False}
    bitrates: list[float] = []
    sizes: list[float] = []
    resolutions: set[str] = set()
    codecs: set[str] = set()
    for c in candidates:
        try:
            bitrate = float(c.get("estimated_bitrate_kbps") or 0)
            if bitrate > 0:
                bitrates.append(bitrate)
        except (TypeError, ValueError):
            pass
        try:
            size = float(c.get("size_bytes") or 0)
            if size > 0:
                sizes.append(size)
        except (TypeError, ValueError):
            pass
        if c.get("resolution"):
            resolutions.add(str(c.get("resolution")))
        if c.get("codec"):
            codecs.add(str(c.get("codec")))
    if len(bitrates) < 2 and len(sizes) < 2 and len(resolutions) < 2:
        return {"requires_user_choice": False}
    bitrate_material = len(bitrates) >= 2 and max(bitrates) >= min(bitrates) * 1.25
    size_material = len(sizes) >= 2 and max(sizes) >= min(sizes) * 1.35
    resolution_material = len(resolutions) >= 2
    codec_material = len(codecs) >= 2 and (bitrate_material or size_material)
    if not (bitrate_material or size_material or resolution_material or codec_material):
        return {"requires_user_choice": False}

    def sort_key(row: dict[str, Any]) -> tuple[int, int, int, float, int]:
        try:
            bitrate = float(row.get("estimated_bitrate_kbps") or 0)
        except (TypeError, ValueError):
            bitrate = 0.0
        return (
            -_quality_language_preference_rank(row),
            -_quality_availability_bucket(row),
            -_safe_int(row.get("seeders")),
            -bitrate,
            _safe_int(row.get("index")) or 9999,
        )

    choices = []
    for c in sorted(candidates, key=sort_key)[:8]:
        choices.append({
            "candidate_id": c.get("candidate_id"),
            "title": c.get("title"),
            "resolution": c.get("resolution"),
            "codec": c.get("codec"),
            "size": c.get("size"),
            "size_bytes": c.get("size_bytes"),
            "per_episode_size": c.get("per_episode_size"),
            "per_episode_size_mb": c.get("per_episode_size_mb"),
            "estimated_bitrate_kbps": c.get("estimated_bitrate_kbps"),
            "seeders": c.get("seeders"),
            "languages": c.get("languages"),
            "language_preference_status": c.get("language_preference_status"),
            "selection_warnings": c.get("selection_warnings") or [],
            "requested_season_coverage": c.get("requested_season_coverage"),
        })
    return {
        "requires_user_choice": True,
        "reason": reason,
        "tradeoff_type": tradeoff_type,
        "message": message,
        "candidate_ids": [c.get("candidate_id") for c in choices if c.get("candidate_id")],
        "choices": choices,
        "comparison": {
            "min_bitrate_kbps": min(bitrates) if bitrates else None,
            "max_bitrate_kbps": max(bitrates) if bitrates else None,
            "min_size_bytes": min(sizes) if sizes else None,
            "max_size_bytes": max(sizes) if sizes else None,
            "resolutions": sorted(resolutions),
            "codecs": sorted(codecs),
        },
    }


def _search_result_next_actions(*, candidates: list[dict[str, Any]], search_scope: str | None, result_set_id: str, has_batch: bool, quality_choice_policy: dict[str, Any] | None = None) -> list[dict[str, Any]]:
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
            "args_hint": {"search_scope": "bundle_preferred" if "bundle_only" in scope else "default"},
        })
        if "pack" in scope:
            actions.append({
                "action": "fallback_to_individual_units",
                "tool": "search_media_torrents",
                "reason": "A pack was preferred but not found; per-unit fallback may be necessary unless the user asked for pack-only.",
                "args_hint": {"search_scope": "individual_units_only"},
            })
        return actions

    if quality_choice_policy and quality_choice_policy.get("requires_user_choice"):
        actions.append({
            "action": "ask_user_to_choose_quality_bitrate",
            "tool": None,
            "reason": quality_choice_policy.get("message") or "Multiple viable same-resolution candidates differ materially in bitrate/size and no item bitrate preference is saved yet.",
            "candidate_ids": quality_choice_policy.get("candidate_ids") or [],
        })

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

    first = candidates[0]
    if first.get("auto_queue_allowed") is False:
        actions.append({
            "action": "do_not_auto_queue_top_candidate",
            "tool": None,
            "reason": first.get("auto_queue_blocked_reason") or "The top candidate has selection warnings; ask the user or inspect alternatives before queueing.",
        })
        if scope in {"bundle_preferred", "season_pack_preferred"}:
            actions.append({
                "action": "try_individual_units_before_queueing_weak_pack",
                "tool": "search_media_torrents",
                "reason": "A season/bundle pack was found, but the best pack is low-confidence; search individual units before accepting a weak pack.",
                "args_hint": {"search_scope": "individual_units_only"},
            })
    else:
        actions.append({
            "action": "queue_clear_candidate",
            "tool": "queue_download",
            "reason": "Use this only when the candidate clearly matches the user's target and constraints.",
            "args_hint": {"result_set_id": result_set_id, "candidate_id": first.get("candidate_id")},
        })
    actions.append({
        "action": "show_or_request_choice",
        "tool": None,
        "reason": "If multiple plausible candidates remain, summarize the best few by candidate_id/title/size/seeders and ask the user to choose.",
    })
    return actions


def _log_search_media_torrents_audit(
    *,
    name: str,
    display_name: str,
    category_id: str | None,
    season: int | None,
    episode: int | None,
    language: str | None,
    search_scope: str | None,
    query: str | None,
    result_set_id: str,
    raw_candidate_count: int,
    clean_candidates: list[dict[str, Any]],
    quality_choice_policy: dict[str, Any] | None,
    llm_candidate_review: dict[str, Any] | None,
    llm_candidate_review_status: str,
    next_actions_preview: list[dict[str, Any]],
) -> None:
    """Emit one compact, structured audit record for the final torrent workspace.

    Provider/query-level logs explain what trackers returned.  This audit
    explains what the user-facing tool handed to the final chat model: candidate
    IDs, quality facts, quality-choice policy, and the torrent-ranker review.
    It is deliberately magnet-free and JSON formatted inside ljs.log so support
    logs are usable without reconstructing state from several files.
    """
    def row(candidate: dict[str, Any]) -> dict[str, Any]:
        return {
            "candidate_id": candidate.get("candidate_id"),
            "index": candidate.get("index"),
            "title": candidate.get("title"),
            "source": candidate.get("source"),
            "size": candidate.get("size"),
            "size_bytes": candidate.get("size_bytes"),
            "seeders": candidate.get("seeders"),
            "languages": candidate.get("languages"),
            "resolution": candidate.get("resolution"),
            "codec": candidate.get("codec"),
            "per_episode_size": candidate.get("per_episode_size"),
            "per_episode_size_mb": candidate.get("per_episode_size_mb"),
            "estimated_bitrate_kbps": candidate.get("estimated_bitrate_kbps"),
            "unit_descriptor": candidate.get("unit_descriptor"),
            "is_bundle": candidate.get("is_bundle"),
            "bundle_scope": candidate.get("bundle_scope"),
            "pack_type": candidate.get("pack_type"),
            "requested_season_coverage": candidate.get("requested_season_coverage"),
            "expected_episode_count": candidate.get("expected_episode_count"),
            "auto_queue_allowed": candidate.get("auto_queue_allowed"),
            "auto_queue_blocked_reason": candidate.get("auto_queue_blocked_reason"),
            "selection_warnings": candidate.get("selection_warnings")[:5] if isinstance(candidate.get("selection_warnings"), list) else candidate.get("selection_warnings"),
            "selection_blockers": candidate.get("selection_blockers")[:5] if isinstance(candidate.get("selection_blockers"), list) else candidate.get("selection_blockers"),
            "llm_recommended": bool(candidate.get("llm_recommended")),
        }

    recommended_ids = [str(cid) for cid in ((llm_candidate_review or {}).get("recommended_candidate_ids") or []) if cid]
    keep_ids = set(recommended_ids)
    if quality_choice_policy and isinstance(quality_choice_policy.get("candidate_ids"), list):
        keep_ids.update(str(cid) for cid in quality_choice_policy.get("candidate_ids") or [] if cid)
    top_rows = clean_candidates[:30]
    extra_rows = [candidate for candidate in clean_candidates[30:] if str(candidate.get("candidate_id") or "") in keep_ids]
    payload = {
        "event": "search_media_torrents_workspace_audit",
        "name": name,
        "display_name": display_name,
        "category_id": category_id,
        "season": season,
        "episode": episode,
        "language": language,
        "search_scope": search_scope,
        "query_summary": query,
        "result_set_id": result_set_id,
        "counts": {
            "raw_candidates_before_tool_cleaning": raw_candidate_count,
            "clean_candidates": len(clean_candidates),
            "logged_top_candidates": len(top_rows),
            "logged_extra_recommended_or_quality_options": len(extra_rows),
        },
        "quality_choice_policy": quality_choice_policy or {},
        "llm_candidate_review_status": llm_candidate_review_status,
        "llm_candidate_review": {
            "recommended_candidate_ids": recommended_ids,
            "confidence": (llm_candidate_review or {}).get("confidence"),
            "needs_user_choice": (llm_candidate_review or {}).get("needs_user_choice"),
            "should_queue_now": (llm_candidate_review or {}).get("should_queue_now"),
            "reason": (llm_candidate_review or {}).get("reason"),
            "answer_hint": (llm_candidate_review or {}).get("answer_hint"),
            "candidate_count_reviewed": (llm_candidate_review or {}).get("candidate_count_reviewed"),
            "chunk_count": (llm_candidate_review or {}).get("chunk_count"),
            "context_limit_tokens": (llm_candidate_review or {}).get("context_limit_tokens"),
        },
        "next_actions_preview": next_actions_preview,
        "candidates": [row(candidate) for candidate in [*top_rows, *extra_rows]],
        "omitted_clean_candidates": max(0, len(clean_candidates) - len(top_rows) - len(extra_rows)),
    }
    logger.info("SEARCH_MEDIA_TORRENTS_WORKSPACE_AUDIT " + json.dumps(payload, ensure_ascii=False, default=str))


def _candidate_picker_rows(candidates: list[dict[str, Any]], limit: int = 60) -> list[dict[str, Any]]:
    """Return a dense candidate workspace for the LLM.

    Full candidate records and magnets stay in the result-set cache.  The model
    sees stable IDs plus the tiny facts needed to decide what to inspect or
    queue: filename/title, size, seeders, and category bundle hints.
    """
    rows: list[dict[str, Any]] = []
    for c in candidates[: max(0, int(limit))]:
        candidate_id = c.get("candidate_id")
        row = {
            "id": candidate_id,
            "candidate_id": candidate_id,
            "index": c.get("index"),
            "title": c.get("title"),
            "size": c.get("size"),
            "size_bytes": c.get("size_bytes"),
            "seeders": c.get("seeders"),
        }
        if c.get("languages"):
            row["languages"] = c.get("languages")
        if c.get("resolution"):
            row["resolution"] = c.get("resolution")
        if c.get("per_episode_size"):
            row["per_episode_size"] = c.get("per_episode_size")
        if c.get("estimated_bitrate_kbps"):
            row["estimated_bitrate_kbps"] = c.get("estimated_bitrate_kbps")
        if c.get("bitrate_basis"):
            row["bitrate_basis"] = c.get("bitrate_basis")
        if c.get("expected_episode_count"):
            row["expected_episode_count"] = c.get("expected_episode_count")
        if c.get("requested_season_coverage"):
            row["requested_season_coverage"] = c.get("requested_season_coverage")
        if c.get("coverage_note"):
            row["coverage_note"] = c.get("coverage_note")
        if c.get("source"):
            row["source"] = c.get("source")
        if c.get("llm_recommended"):
            row["llm_recommended"] = True
        if c.get("selection_warnings"):
            row["selection_warnings"] = c.get("selection_warnings")[:3]
        if c.get("selection_blockers"):
            row["selection_blockers"] = c.get("selection_blockers")[:3]
        if c.get("auto_queue_allowed") is False:
            row["auto_queue_allowed"] = False
            row["blocked_reason"] = c.get("auto_queue_blocked_reason")
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
    if str(search_scope or "") in {"bundle_preferred", "bundle_only", "season_pack_preferred", "season_pack_only"}:
        for c in candidates:
            if c.get("is_bundle") and c.get("candidate_id"):
                return [str(c.get("candidate_id"))]
    if candidates and candidates[0].get("candidate_id"):
        return [str(candidates[0].get("candidate_id"))]
    return []


def _estimated_total_size_bytes(candidates: list[dict[str, Any]], selected_ids: list[str]) -> int:
    """Estimate selected download footprint, respecting selective TV bundles."""
    if not selected_ids:
        return 0
    wanted = {str(cid) for cid in selected_ids}
    total = 0
    for c in candidates:
        if str(c.get("candidate_id")) not in wanted:
            continue
        try:
            bundle_context = c.get("bundle_context") or {}
            if isinstance(bundle_context, dict) and bundle_context.get("selective_download_required") and c.get("per_episode_size_bytes"):
                count = _safe_int(bundle_context.get("selected_unit_episode_count_hint")) or 10
                total += int(c.get("per_episode_size_bytes") or 0) * max(1, count)
            else:
                total += int(c.get("size_bytes") or 0)
        except (TypeError, ValueError):
            pass
    return total


def _build_batch_recommendation(*, name: str, category_id: str | None, season: int | None, episode: int | None, search_scope: str | None = None, result_set_id: str, candidates: list[dict[str, Any]], category: object | None = None, preferred_language: str | None = None) -> dict[str, Any] | None:
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
    # A season/full-season request that has a plausible bundle candidate should
    # not also expose a deterministic per-episode batch recommendation.  That
    # creates contradictory evidence for the final LLM (for example: one LLM
    # reviewed Italian S01E01-06 pack plus stray S01E07/S08E02 rows from a broad
    # provider result set).  Keep the bundle candidate visible and let the LLM
    # adjudicator/queue path decide whether to queue or inspect that single
    # season-pack candidate.  Only build an episode batch when the user/tool
    # explicitly requested individual units or no bundle exists.
    if season is not None and episode is None and str(search_scope or "default").lower() not in {"individual_units_only"}:
        for candidate in candidates or []:
            descriptor = candidate.get("unit_descriptor") if isinstance(candidate.get("unit_descriptor"), dict) else {}
            if candidate.get("is_bundle") or str(descriptor.get("granularity") or "").lower() == "season":
                return None
    # A broad item-title search has not declared a multi-unit scope.  Do not
    # turn unrelated S01/S02/S08 rows into a fake batch recommendation just
    # because the category can describe their unit coordinates.
    if season is None and str(search_scope or "default").lower() in {"", "default"}:
        return None
    # Bundle/season-pack searches return alternatives for one requested unit.
    # They must not be converted into a multi-unit batch merely because a broad
    # query also found S02/S03 rows or a multi-season pack.
    if str(search_scope or "").lower() in {"bundle_preferred", "bundle_only", "season_pack_preferred", "season_pack_only"}:
        return None
    if not category or not hasattr(category, "batch_group_for_candidate"):
        return None

    unit_groups: dict[str, dict[str, Any]] = {}
    request_context = {"season": season, "episode": episode, "category_id": category_id, "search_scope": search_scope}
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
        extras = {lang for lang in normalized_languages if lang not in {preferred, "multi"}}
        if preferred == "english" and (extras or "multi" in normalized_languages or "dual" in title_lower or "multi" in title_lower):
            lang_score = 1
        else:
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
