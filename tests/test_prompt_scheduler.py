"""
Tests for the prompt scheduler.

Verifies task creation, due-date checking, and removal.
"""

import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from src.core.database import Database
from src.core.prompt_scheduler import PromptScheduler
from src.core.notifications import NotificationService


@pytest_asyncio.fixture
async def db(tmp_path):
    """Create a test database for prompt scheduler tests."""
    database = Database(db_path=str(tmp_path / "test_scheduler.db"))
    await database.initialize()
    yield database
    await database.close()


@pytest_asyncio.fixture
async def scheduler(db):
    """Create a PromptScheduler with no real assistant (unit tests only)."""
    notifications = NotificationService()
    # Pass None for assistant — we only test scheduler logic, not AI execution
    return PromptScheduler(db=db, assistant=None, notifications=notifications)


class TestPromptScheduler:
    """Tests for scheduled task creation and management."""

    @pytest.mark.asyncio
    async def test_create_task_stores_in_db(self, scheduler, db):
        """Creating a task should persist it in the database."""
        task = await scheduler.create_task(
            prompt="weekly TV report", interval_minutes=10080
        )
        assert task.id is not None
        assert task.prompt == "weekly TV report"
        assert task.interval_minutes == 10080
        assert task.enabled is True

        # Verify it's in the DB
        tasks = await scheduler.list_tasks()
        assert len(tasks) == 1
        assert tasks[0].prompt == "weekly TV report"

    @pytest.mark.asyncio
    async def test_remove_task_deletes_from_db(self, scheduler, db):
        """Removing a task should delete it from the database."""
        task = await scheduler.create_task(
            prompt="daily download check", interval_minutes=1440
        )
        tasks_before = await scheduler.list_tasks()
        assert len(tasks_before) == 1

        await scheduler.remove_task(task.id)
        tasks_after = await scheduler.list_tasks()
        assert len(tasks_after) == 0

    @pytest.mark.asyncio
    async def test_task_is_due_when_never_run(self, scheduler, db):
        """A task that has never run should be due immediately."""
        task = await scheduler.create_task(
            prompt="test prompt", interval_minutes=60
        )
        assert PromptScheduler._is_due(task) is True

    @pytest.mark.asyncio
    async def test_task_not_due_when_recently_run(self, scheduler, db):
        """A task run within its interval should not be due."""
        task = await scheduler.create_task(
            prompt="test prompt", interval_minutes=10080
        )
        task.last_run_at = datetime.now(timezone.utc)
        assert PromptScheduler._is_due(task) is False

    @pytest.mark.asyncio
    async def test_task_is_due_after_interval(self, scheduler, db):
        """A task whose interval has elapsed should be due again."""
        task = await scheduler.create_task(
            prompt="test prompt", interval_minutes=60
        )
        # Set last run 2 hours ago
        task.last_run_at = datetime.now(timezone.utc) - timedelta(hours=2)
        assert PromptScheduler._is_due(task) is True

    @pytest.mark.asyncio
    async def test_list_tasks_filters_by_user(self, scheduler, db):
        """Listing tasks with user_id should filter correctly."""
        task1 = await scheduler.create_task(
            prompt="user1 task", interval_minutes=1440, user_id="user1"
        )
        task2 = await scheduler.create_task(
            prompt="user2 task", interval_minutes=1440, user_id="user2"
        )

        user1_tasks = await scheduler.list_tasks(user_id="user1")
        assert len(user1_tasks) == 1
        assert user1_tasks[0].prompt == "user1 task"