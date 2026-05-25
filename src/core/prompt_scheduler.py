"""Prompt scheduler for LJS.

Runs recurring natural-language tasks through the AI assistant on a
configurable interval. Tasks are persisted in the database so they
survive restarts. Each task is a prompt like 'give me a weekly report
of new TV series matching my tastes' that gets re-executed periodically,
with the response routed to the user's preferred channel (web,
Discord, or Telegram).
"""

import hashlib
import uuid
from datetime import datetime, timezone
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


@runtime_checkable
class AssistantRunner(Protocol):
    """Protocol for executing assistant prompts.

    Decouples PromptScheduler from AIAssistant, breaking the
    circular dependency where both need each other.
    """

    async def run(
        self,
        user_prompt: str,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> str:
        """Run an assistant prompt for a scheduled task.

        This protocol method decouples PromptScheduler from AIAssistant.
        Implementations should keep the return value as user-facing text and
        avoid raising provider-specific errors across the scheduler boundary.
        """
        ...


class PromptScheduler:
    """Manages and executes recurring natural-language tasks.

    Tasks are stored in the database and checked against their interval.
    When a task is due, it is sent through the AI assistant and the
    response is delivered via the notification service to the user's
    preferred channel.

    Uses the AssistantRunner protocol to avoid circular dependencies
    with AIAssistant.
    """

    def __init__(self, db: Database, notifications: NotificationService,
                 assistant: AssistantRunner | None = None):
        self._db = db
        self._assistant = assistant
        self._notifications = notifications

    def set_assistant(self, assistant: AssistantRunner) -> None:
        """Set the assistant runner after construction.

        This breaks the circular dependency: PromptScheduler needs
        AIAssistant to run prompts, but AgentToolCatalog needs
        PromptScheduler before AIAssistant is fully wired.
        """
        self._assistant = assistant

    async def create_task(self, prompt: str, interval_minutes: int = DEFAULT_WEEKLY_INTERVAL_MINUTES,
                          user_id: str | None = None,
                          channel: str = "web") -> ScheduledTask:
        """Create a new scheduled task.

        Args:
            prompt: Natural-language instruction for the AI.
            interval_minutes: How often to run (default 10080 = weekly).
            user_id: User who created the task (for per-user routing).
            channel: Preferred notification channel (web/discord/telegram).

        Returns:
            The created ScheduledTask.

        Raises:
            ValueError: If prompt is empty or interval is invalid.
        """
        if not prompt.strip():
            raise ValueError("Task prompt cannot be empty")
        if interval_minutes < 1:
            raise ValueError("Interval must be at least 1 minute")

        # Use UUID to avoid collision when user_id is None
        task_id = hashlib.sha256(
            f"{uuid.uuid4()}:{prompt}".encode()
        ).hexdigest()[:12]
        task = ScheduledTask(
            id=task_id,
            prompt=prompt,
            interval_minutes=interval_minutes,
            user_id=user_id,
            channel=channel,
        )
        await self._db.system.upsert_scheduled_task(task)
        logger.info(f"Created scheduled task '{task_id}': every {interval_minutes}min — {prompt[:MAX_LOG_PROMPT_LENGTH]}")
        return task

    async def remove_task(self, task_id: str) -> None:
        """Remove a scheduled task by its ID.

        Args:
            task_id: The task ID to remove.
        """
        await self._db.system.delete_scheduled_task(task_id)
        logger.info(f"Removed scheduled task '{task_id}'")

    async def list_tasks(self, user_id: str | None = None) -> list[ScheduledTask]:
        """List all scheduled tasks, optionally filtered by user.

        Args:
            user_id: Optional user ID to filter by.

        Returns:
            List of ScheduledTask matching the filter.
        """
        rows = await self._db.system.get_scheduled_tasks(user_id=user_id)
        return [self._row_to_task(r) for r in rows]

    async def run_due_tasks(self) -> list[str]:
        """Execute all tasks whose interval has elapsed since last run.

        Returns:
            List of task IDs that were executed.
        """
        rows = await self._db.system.get_scheduled_tasks(enabled_only=True)
        executed = []

        for row in rows:
            task = self._row_to_task(row)
            if not self._is_due(task):
                continue

            logger.info(f"Running scheduled task '{task.id}': {task.prompt[:80]}")
            try:
                response = await self._assistant.run(
                    user_prompt=task.prompt,
                    session_id=f"scheduled_{task.id}",
                    user_id=task.user_id,
                )
                await self._send_response(task, response)
                task.last_run_at = datetime.now(timezone.utc)
                await self._db.system.upsert_scheduled_task(task)
                executed.append(task.id)
            except Exception as e:
                logger.error(f"Scheduled task '{task.id}' failed: {e}")

        return executed

    async def run_task_now(self, task_id: str) -> str:
        """Execute a specific task immediately regardless of interval.

        Args:
            task_id: The task ID to run.

        Returns:
            The assistant's response text.

        Raises:
            ValueError: If the task ID is not found.
        """
        rows = await self._db.system.get_scheduled_tasks()
        task_row = next((r for r in rows if r["id"] == task_id), None)
        if task_row is None:
            raise ValueError(f"Scheduled task '{task_id}' not found")

        task = self._row_to_task(task_row)
        response = await self._assistant.run(
            user_prompt=task.prompt,
            session_id=f"scheduled_{task.id}",
            user_id=task.user_id,
        )
        await self._send_response(task, response)
        task.last_run_at = datetime.now(timezone.utc)
        await self._db.system.upsert_scheduled_task(task)
        return response

    @staticmethod
    def _is_due(task: ScheduledTask) -> bool:
        """Check if a task's interval has elapsed since its last run."""
        if task.last_run_at is None:
            return True
        # Ensure timezone-aware comparison
        last = task.last_run_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        elapsed_minutes = (datetime.now(timezone.utc) - last).total_seconds() / 60
        return elapsed_minutes >= task.interval_minutes

    async def _send_response(self, task: ScheduledTask, response: str) -> None:
        """Deliver a task response to the user via notifications.

        Uses NotificationService which fans out to all registered bridges.
        """
        title = f"Scheduled: {task.prompt[:MAX_PROMPT_PREVIEW_LENGTH]}"
        body = response[:MAX_NOTIFICATION_BODY_LENGTH] if len(response) > MAX_NOTIFICATION_BODY_LENGTH else response
        await self._notifications.send_message(body, title=title)

    @staticmethod
    def _row_to_task(row) -> ScheduledTask:
        """Convert a database row to a ScheduledTask model."""
        last_run = row.get("last_run_at")
        if isinstance(last_run, str):
            last_run = datetime.fromisoformat(last_run)
        created = row.get("created_at")
        if isinstance(created, str):
            created = datetime.fromisoformat(created)

        return ScheduledTask(
            id=row["id"],
            prompt=row["prompt"],
            interval_minutes=row.get("interval_minutes", 10080),
            user_id=row.get("user_id"),
            channel=row.get("channel", "web"),
            enabled=row.get("enabled", 1) == 1 if isinstance(row.get("enabled"), int) else row.get("enabled", True),
            last_run_at=last_run,
            created_at=created or datetime.now(timezone.utc),
        )
