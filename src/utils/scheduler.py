"""
Lightweight async interval scheduler for LJS.

Replaces APScheduler with a tiny asyncio-based scheduler that only
supports interval-based jobs — the only pattern LJS uses. Each job
runs as an asyncio task that sleeps between invocations.

Jobs are staggered on startup by a per-job offset to prevent all
jobs from firing simultaneously and overwhelming the system.
"""

import asyncio
from typing import Any, Callable
from loguru import logger

# Seconds of stagger between each scheduled job's initial fire.
# Prevents N jobs from all waking up at the same instant.
_STAGGER_PER_JOB_SECONDS = 15


class IntervalScheduler:
    """Async interval scheduler using plain asyncio tasks.

    Each registered job runs as a long-lived asyncio task that calls
    the target coroutine, then sleeps for the specified interval.
    Jobs are identified by name for cancellation and logging.

    Jobs are staggered on startup — the first job fires after 2s,
    the second after 2+15s, the third after 2+30s, and so on.
    This prevents all show checks from bombarding search providers
    and LLM APIs simultaneously.
    """

    def __init__(self):
        self._jobs: dict[str, asyncio.Task] = {}
        self._job_count = 0

    def add_job(
        self,
        func: Callable[..., Any],
        interval_seconds: float,
        args: tuple = (),
        kwargs: dict | None = None,
        id: str = "",
        initial_delay_seconds: float | None = None,
    ) -> None:
        """Register a recurring async job.

        Args:
            func: Async callable to run on each interval.
            interval_seconds: Seconds between invocations.
            args: Positional arguments to pass to func.
            kwargs: Keyword arguments to pass to func.
            id: Unique name for this job (used for cancellation and logging).
            initial_delay_seconds: Optional first-run delay. Expensive jobs
                should pass their normal interval so provider/library work does
                not automatically run on every application launch.
        """
        if id in self._jobs:
            logger.warning(f"Job '{id}' already registered — replacing")
            self._jobs[id].cancel()

        stagger = self._job_count * _STAGGER_PER_JOB_SECONDS
        self._job_count += 1
        first_delay = float(initial_delay_seconds) if initial_delay_seconds is not None else (2 + stagger)

        task = asyncio.create_task(
            self._run_job(id, func, interval_seconds, args, kwargs or {}, first_delay),
        )
        self._jobs[id] = task
        logger.debug(
            f"Scheduled job '{id}' every {interval_seconds:.0f}s "
            f"(first run in {first_delay:.0f}s)"
        )

    def remove_job(self, job_id: str) -> None:
        """Cancel and remove a scheduled job by its id."""
        task = self._jobs.pop(job_id, None)
        if task and not task.done():
            task.cancel()

    def start(self) -> None:
        """No-op: jobs are already started as tasks in add_job."""
        logger.info(f"Scheduler started with {len(self._jobs)} job(s)")

    def shutdown(self) -> None:
        """Cancel all running jobs."""
        for job_id, task in self._jobs.items():
            if not task.done():
                task.cancel()
        self._jobs.clear()
        logger.info("Scheduler stopped")

    async def _run_job(self, job_id: str, func, interval: float,
                       args: tuple, kwargs: dict, initial_delay: float):
        """Run a job in a loop with interval-based sleeps."""
        await asyncio.sleep(max(0.0, float(initial_delay)))
        while True:
            try:
                await func(*args, **kwargs)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Job '{job_id}' failed: {e}")
            await asyncio.sleep(interval)