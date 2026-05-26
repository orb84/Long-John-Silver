#!/usr/bin/env python3
"""Round 109 checks for user-created reminders and scheduled assistant tasks."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    """Read a repository file as UTF-8 text."""
    return (ROOT / rel).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    """Raise an assertion with a clear Round 109 failure message."""
    if not condition:
        raise AssertionError(message)


def require_all(text: str, needles: tuple[str, ...], label: str) -> None:
    """Require all substrings in one source file."""
    missing = [needle for needle in needles if needle not in text]
    require(not missing, f"Missing {label}: " + ", ".join(missing))


def test_prompt_scheduler_supports_one_off_and_condition_checks() -> None:
    """PromptScheduler must support precise due times, not only intervals."""
    source = read("src/core/prompt_scheduler.py")
    require_all(source, (
        '"reminder"',
        '"scheduled_prompt"',
        '"condition_check"',
        '"one_off"',
        '"recurring"',
        "due_at",
        "next_run_at",
        "max_runs",
        "last_error",
        "def _resolve_first_run",
        "def _mark_success",
        "def _mark_failure",
        "task.enabled = False",
        "Do not queue downloads unless the original prompt explicitly asks you to queue them",
    ), "prompt scheduler one-off/condition-check contract")


def test_scheduled_task_storage_has_timing_columns() -> None:
    """Fresh schema and migration must store one-off and recurring task state."""
    database = read("src/core/database.py")
    migration = read("migrations/107_scheduled_task_timing.sql")
    repository = read("src/core/repositories/system.py")
    model = read("src/core/domain_models/settings.py")
    for source, label in ((database, "base schema"), (migration, "migration"), (repository, "repository"), (model, "model")):
        require_all(source, (
            "task_type",
            "schedule_type",
            "due_at",
            "next_run_at",
            "run_count",
            "max_runs",
            "session_id",
            "last_error",
        ), label)


def test_llm_can_access_scheduling_tools() -> None:
    """The tools must be registered and exposed to the CONFIG intent."""
    tools = read("src/ai/tools/scheduling.py")
    policy = read("src/ai/tool_policy.py")
    router = read("src/ai/intent_router.py")
    prompt_builder = read("src/ai/prompt_builder.py")
    require_all(tools, (
        "class CreateScheduledTaskTool",
        "delay_minutes",
        "due_at",
        "schedule_type",
        "task_type",
        "condition_check",
        "list_scheduled_tasks",
        "remove_scheduled_task",
    ), "agent scheduling tool schema")
    require_all(policy, (
        '"create_scheduled_task"',
        '"list_scheduled_tasks"',
        '"remove_scheduled_task"',
    ), "tool policy allow-list")
    require("scheduled reminders" in router and "recurring checks" in router, "intent router should classify scheduling requests as CONFIG")
    require("task_type=condition_check" in prompt_builder, "CONFIG prompt should tell the LLM how to schedule future checks")


def test_prompt_scheduler_polling_is_not_hourly() -> None:
    """User reminders should not wait up to an hour after the due time."""
    scheduler = read("src/core/scheduler.py")
    require('id="prompt_scheduler"' in scheduler, "prompt scheduler job should be registered")
    require("self._run_scheduled_prompts, interval_seconds=60" in scheduler, "prompt scheduler should poll every minute")
    require("initial_delay_seconds=60" in scheduler, "prompt scheduler should start checking after one minute")


def test_architecture_documents_user_scheduled_tasks() -> None:
    """The architecture contract must distinguish user tasks from category schedules."""
    architecture = read("architecture.md")
    readme = read("README.md")
    require_all(architecture, (
        "## User Scheduled Assistant Tasks",
        "separate from category lifecycle schedules",
        "task_type",
        "schedule_type",
        "one-off tasks disable themselves",
    ), "architecture scheduling contract")
    require("one-off reminders" in readme and "recurring assistant prompts" in readme, "README should mention user scheduling capability")


def main() -> None:
    """Run Round 109 checks as a standalone script."""
    for test in (
        test_prompt_scheduler_supports_one_off_and_condition_checks,
        test_scheduled_task_storage_has_timing_columns,
        test_llm_can_access_scheduling_tools,
        test_prompt_scheduler_polling_is_not_hourly,
        test_architecture_documents_user_scheduled_tasks,
    ):
        test()
        print(f"PASS {test.__name__}")
    print("Round 109 user scheduling checks passed.")


if __name__ == "__main__":
    main()
