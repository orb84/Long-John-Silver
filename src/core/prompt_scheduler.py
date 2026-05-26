"""User-created prompt scheduler for LJS.

Runs one-off reminders, one-off scheduled assistant prompts, and recurring
natural-language checks through the shared assistant runtime.  Tasks are
persisted in SQLite so they survive restarts.  The scheduler intentionally stays
category-neutral: a future torrent check is stored as an assistant prompt that
can use the normal category/search tools when it runs, while category lifecycle
jobs remain owned by the media categories themselves.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from loguru import logger
from typing import Protocol, runtime_checkable

from src.core.database import Database
from src.core.models import ScheduledTask
from src.core.notifications import NotificationService

# Named constants replacing magic numbers
DEFAULT_WEEKLY_INTERVAL_MINUTES = 10080
MAX_NOTIFICATION_BODY_LENGTH = 2000
MAX_PROMPT_PREVIEW_LENGTH = 50
MAX_LOG_PROMPT_LENGTH = 80
MIN_RETRY_DELAY_MINUTES = 15

_TASK_TYPES = {"reminder", "scheduled_prompt", "condition_check"}
_SCHEDULE_TYPES = {"one_off", "recurring"}


@runtime_checkable
class AssistantRunner(Protocol):
    """Protocol for executing assistant prompts.

    Decouples PromptScheduler from AIAssistant, breaking the circular dependency
    where both need each other.
    """

    async def run(
        self,
        user_prompt: str,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> str:
        """Run an assistant prompt for a scheduled task.

        Implementations should keep the return value as user-facing text and
        avoid raising provider-specific errors across the scheduler boundary.
        """
        ...


class PromptScheduler:
    """Manages user-created reminders and assistant automations.

    The scheduler stores durable task contracts and checks whether they are due.
    Lightweight reminders are delivered directly as notifications; scheduled
    prompts and condition checks are executed by the assistant so they can use
    the same tools and category context as an ordinary chat turn.
    """

    def __init__(self, db: Database, notifications: NotificationService,
                 assistant: AssistantRunner | None = None):
        self._db = db
        self._assistant = assistant
        self._notifications = notifications
        self._running = False

    def set_assistant(self, assistant: AssistantRunner) -> None:
        """Set the assistant runner after construction.

        This breaks the circular dependency: PromptScheduler needs AIAssistant
        to run prompts, but AgentToolCatalog needs PromptScheduler before
        AIAssistant is fully wired.
        """
        self._assistant = assistant

    async def create_task(
        self,
        prompt: str,
        interval_minutes: int | None = DEFAULT_WEEKLY_INTERVAL_MINUTES,
        user_id: str | None = None,
        channel: str = "web",
        *,
        title: str | None = None,
        task_type: str = "scheduled_prompt",
        schedule_type: str = "recurring",
        due_at: datetime | str | None = None,
        delay_minutes: int | None = None,
        max_runs: int | None = None,
        session_id: str | None = None,
    ) -> ScheduledTask:
        """Create a reminder, one-off scheduled prompt, or recurring check.

        Args:
            prompt: Natural-language instruction or reminder text.
            interval_minutes: Recurrence interval. Ignored for one-off tasks.
            user_id: User who created the task, when available.
            channel: Preferred notification channel label.
            title: Optional user-facing title.
            task_type: reminder, scheduled_prompt, or condition_check.
            schedule_type: one_off or recurring.
            due_at: First run time as datetime or ISO string.
            delay_minutes: Relative delay from now for first run.
            max_runs: Optional cap for recurring tasks.
            session_id: Optional source session id for future continuity.

        Returns:
            The created ScheduledTask.

        Raises:
            ValueError: If prompt or schedule contract is invalid.
        """
        cleaned_prompt = prompt.strip()
        if not cleaned_prompt:
            raise ValueError("Task prompt cannot be empty")

        normalized_task_type = self._normalize_task_type(task_type)
        normalized_schedule = self._normalize_schedule_type(schedule_type, normalized_task_type)
        first_run = self._resolve_first_run(
            due_at=due_at,
            delay_minutes=delay_minutes,
            schedule_type=normalized_schedule,
        )
        normalized_interval = self._normalize_interval(interval_minutes, normalized_schedule)
        normalized_max_runs = self._normalize_max_runs(max_runs, normalized_schedule)

        task_id = hashlib.sha256(f"{uuid.uuid4()}:{cleaned_prompt}".encode()).hexdigest()[:12]
        task = ScheduledTask(
            id=task_id,
            prompt=cleaned_prompt,
            interval_minutes=normalized_interval,
            user_id=user_id,
            channel=channel,
            title=(title or self._default_title(normalized_task_type, cleaned_prompt)).strip(),
            task_type=normalized_task_type,
            schedule_type=normalized_schedule,
            due_at=first_run,
            next_run_at=first_run,
            max_runs=normalized_max_runs,
            session_id=session_id,
        )
        await self._db.system.upsert_scheduled_task(task)
        logger.info(
            "Created scheduled task '{}' type={} schedule={} next_run={} — {}".format(
                task_id, task.task_type, task.schedule_type,
                task.next_run_at.isoformat() if task.next_run_at else "now",
                cleaned_prompt[:MAX_LOG_PROMPT_LENGTH],
            )
        )
        return task

    async def remove_task(self, task_id: str) -> None:
        """Remove a scheduled task by its ID."""
        await self._db.system.delete_scheduled_task(task_id)
        logger.info(f"Removed scheduled task '{task_id}'")

    async def list_tasks(self, user_id: str | None = None) -> list[ScheduledTask]:
        """List all scheduled tasks, optionally filtered by user."""
        rows = await self._db.system.get_scheduled_tasks(user_id=user_id)
        return [self._row_to_task(r) for r in rows]

    async def run_due_tasks(self) -> list[str]:
        """Execute all enabled tasks whose due/next-run time has arrived.

        Returns:
            List of task IDs that completed successfully.
        """
        if self._running:
            logger.debug("Prompt scheduler tick skipped because a previous tick is still running.")
            return []
        self._running = True
        try:
            rows = await self._db.system.get_scheduled_tasks(enabled_only=True)
            return await self._run_due_rows(rows)
        finally:
            self._running = False

    async def run_task_now(self, task_id: str) -> str:
        """Execute a specific task immediately regardless of schedule."""
        rows = await self._db.system.get_scheduled_tasks()
        task_row = next((r for r in rows if r["id"] == task_id), None)
        if task_row is None:
            raise ValueError(f"Scheduled task '{task_id}' not found")

        task = self._row_to_task(task_row)
        response = await self._execute_task(task)
        await self._mark_success(task)
        return response

    async def _run_due_rows(self, rows: list[dict]) -> list[str]:
        """Run due rows sequentially to avoid multiple simultaneous LLM calls."""
        executed: list[str] = []
        for row in rows:
            task = self._row_to_task(row)
            if not self._is_due(task):
                continue
            logger.info(f"Running scheduled task '{task.id}': {task.prompt[:MAX_LOG_PROMPT_LENGTH]}")
            try:
                await self._execute_task(task)
                await self._mark_success(task)
                executed.append(task.id)
            except Exception as e:
                await self._mark_failure(task, e)
        return executed

    async def _execute_task(self, task: ScheduledTask) -> str:
        """Execute one task and return the delivered response text."""
        if task.task_type == "reminder":
            response = task.prompt
        else:
            if self._assistant is None:
                raise RuntimeError("Assistant runner is not available for scheduled prompt execution")
            response = await self._assistant.run(
                user_prompt=self._build_scheduled_prompt(task),
                session_id=task.session_id or f"scheduled_{task.id}",
                user_id=task.user_id,
            )
        await self._send_response(task, response)
        return response

    def _build_scheduled_prompt(self, task: ScheduledTask) -> str:
        """Wrap scheduled prompts with enough context for safe autonomous runs."""
        if task.task_type == "condition_check":
            prefix = (
                "This is a user-created scheduled check. Run the requested check now, "
                "use the appropriate registered tools, and report the result clearly. "
                "Do not queue downloads unless the original prompt explicitly asks you to queue them.\n\n"
            )
        else:
            prefix = (
                "This is a user-created scheduled assistant task. Execute it now and "
                "return a concise user-facing report.\n\n"
            )
        return prefix + task.prompt

    async def _mark_success(self, task: ScheduledTask) -> None:
        """Persist success state and advance or complete the task."""
        now = datetime.now(timezone.utc)
        task.last_run_at = now
        task.run_count = int(task.run_count or 0) + 1
        task.last_error = ""
        if self._should_complete(task):
            task.enabled = False
            task.next_run_at = None
        else:
            task.next_run_at = now + timedelta(minutes=max(1, int(task.interval_minutes or 1)))
        await self._db.system.upsert_scheduled_task(task)

    async def _mark_failure(self, task: ScheduledTask, error: Exception) -> None:
        """Persist failure state and delay the next retry to avoid spin loops."""
        task.last_error = str(error)
        retry_minutes = max(MIN_RETRY_DELAY_MINUTES, min(int(task.interval_minutes or 60), 60))
        task.next_run_at = datetime.now(timezone.utc) + timedelta(minutes=retry_minutes)
        await self._db.system.upsert_scheduled_task(task)
        logger.error(f"Scheduled task '{task.id}' failed: {error}")

    def _should_complete(self, task: ScheduledTask) -> bool:
        """Return whether a task should be disabled after this successful run."""
        if task.schedule_type == "one_off":
            return True
        return bool(task.max_runs is not None and task.run_count >= task.max_runs)

    @staticmethod
    def _is_due(task: ScheduledTask) -> bool:
        """Check whether a task should run at the current time."""
        now = datetime.now(timezone.utc)
        next_run = task.next_run_at or task.due_at
        if next_run is not None:
            return PromptScheduler._as_aware(next_run) <= now
        if task.last_run_at is None:
            return True
        last = PromptScheduler._as_aware(task.last_run_at)
        elapsed_minutes = (now - last).total_seconds() / 60
        return elapsed_minutes >= task.interval_minutes

    async def _send_response(self, task: ScheduledTask, response: str) -> None:
        """Deliver a task response to the user via notifications."""
        title = task.title or f"Scheduled: {task.prompt[:MAX_PROMPT_PREVIEW_LENGTH]}"
        body = response[:MAX_NOTIFICATION_BODY_LENGTH] if len(response) > MAX_NOTIFICATION_BODY_LENGTH else response
        await self._notifications.send_message(body, title=title)

    @classmethod
    def _row_to_task(cls, row) -> ScheduledTask:
        """Convert a database row to a ScheduledTask model."""
        return ScheduledTask(
            id=row["id"],
            prompt=row["prompt"],
            interval_minutes=row.get("interval_minutes", DEFAULT_WEEKLY_INTERVAL_MINUTES),
            user_id=row.get("user_id"),
            channel=row.get("channel", "web"),
            enabled=row.get("enabled", 1) == 1 if isinstance(row.get("enabled"), int) else row.get("enabled", True),
            last_run_at=cls._parse_datetime(row.get("last_run_at")),
            created_at=cls._parse_datetime(row.get("created_at")) or datetime.now(timezone.utc),
            task_type=row.get("task_type") or "scheduled_prompt",
            schedule_type=row.get("schedule_type") or "recurring",
            title=row.get("title") or "",
            due_at=cls._parse_datetime(row.get("due_at")),
            next_run_at=cls._parse_datetime(row.get("next_run_at")),
            run_count=int(row.get("run_count") or 0),
            max_runs=row.get("max_runs"),
            session_id=row.get("session_id"),
            last_error=row.get("last_error") or "",
        )

    @staticmethod
    def _parse_datetime(value: object) -> datetime | None:
        """Parse stored ISO timestamps into timezone-aware datetimes."""
        if not value:
            return None
        if isinstance(value, datetime):
            return PromptScheduler._as_aware(value)
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        return PromptScheduler._as_aware(parsed)

    @staticmethod
    def _as_aware(value: datetime) -> datetime:
        """Return a timezone-aware datetime, assuming UTC for legacy values."""
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _normalize_task_type(task_type: str) -> str:
        """Validate and normalize task type."""
        normalized = (task_type or "scheduled_prompt").strip().lower()
        if normalized not in _TASK_TYPES:
            raise ValueError(f"Unsupported task_type '{task_type}'")
        return normalized

    @staticmethod
    def _normalize_schedule_type(schedule_type: str, task_type: str) -> str:
        """Validate and normalize schedule type."""
        normalized = (schedule_type or "recurring").strip().lower()
        if task_type == "reminder" and normalized == "recurring":
            normalized = "one_off"
        if normalized not in _SCHEDULE_TYPES:
            raise ValueError(f"Unsupported schedule_type '{schedule_type}'")
        return normalized

    @staticmethod
    def _normalize_interval(interval_minutes: int | None, schedule_type: str) -> int:
        """Validate interval while preserving legacy recurring defaults."""
        if schedule_type == "one_off":
            return int(interval_minutes or DEFAULT_WEEKLY_INTERVAL_MINUTES)
        interval = int(interval_minutes or DEFAULT_WEEKLY_INTERVAL_MINUTES)
        if interval < 1:
            raise ValueError("Interval must be at least 1 minute")
        return interval

    @staticmethod
    def _normalize_max_runs(max_runs: int | None, schedule_type: str) -> int | None:
        """Return a safe max-run cap for the task contract."""
        if schedule_type == "one_off":
            return 1
        if max_runs is None:
            return None
        normalized = int(max_runs)
        if normalized < 1:
            raise ValueError("max_runs must be at least 1 when provided")
        return normalized

    @staticmethod
    def _resolve_first_run(
        *,
        due_at: datetime | str | None,
        delay_minutes: int | None,
        schedule_type: str,
    ) -> datetime | None:
        """Resolve absolute or relative first-run time."""
        now = datetime.now(timezone.utc)
        if delay_minutes is not None:
            delay = int(delay_minutes)
            if delay < 0:
                raise ValueError("delay_minutes cannot be negative")
            return now + timedelta(minutes=delay)
        parsed_due = PromptScheduler._parse_datetime(due_at)
        if parsed_due is not None:
            return parsed_due
        if schedule_type == "one_off":
            raise ValueError("One-off tasks require due_at or delay_minutes")
        return now

    @staticmethod
    def _default_title(task_type: str, prompt: str) -> str:
        """Build a compact title for task notifications."""
        prefix = {
            "reminder": "Reminder",
            "condition_check": "Scheduled check",
            "scheduled_prompt": "Scheduled task",
        }.get(task_type, "Scheduled task")
        preview = prompt[:MAX_PROMPT_PREVIEW_LENGTH].strip()
        return f"{prefix}: {preview}" if preview else prefix
