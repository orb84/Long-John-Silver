"""
Task supervisor for LJS.

Tracks, supervises, and auto-restarts critical async background tasks.
Prevents silent task death: if a supervised coroutine crashes with an
unhandled exception, the supervisor logs the failure and optionally
restarts it after a backoff delay. Also provides graceful shutdown
by cancelling all tracked tasks.
"""

import asyncio
from typing import Optional, Callable
from loguru import logger

from src.core.models import TaskCriticality

MAX_RESTARTS = 3
RESTART_BACKOFF_BASE_SECONDS = 5


class TaskSupervisor:
    """Supervises asyncio background tasks with crash recovery and graceful shutdown.

    Two spawn modes:
    - spawn_restartable(): For long-running loops (monitors, queue managers).
      Requires a factory function so the coroutine can be recreated on restart.
    - spawn_one_shot(): For one-shot operations (file organization, WebSocket
      broadcasts). Logged on crash but never restarted.
    """

    def __init__(self, on_critical_failure: Optional[Callable[[str, Exception], None]] = None) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._specs: dict[str, dict] = {}
        self._restart_counts: dict[str, int] = {}
        self._shutting_down = False
        self._on_critical_failure = on_critical_failure

    def set_failure_callback(self, callback: Callable[[str, Exception], None]) -> None:
        """Set a callback to be invoked when a CRITICAL task fails permanently."""
        self._on_critical_failure = callback

    def spawn_restartable(
        self, name: str, coro_factory: Callable[[], object], criticality: TaskCriticality = TaskCriticality.IMPORTANT,
    ) -> asyncio.Task:
        """Spawn a supervised, restartable background task.

        If the coroutine crashes, the supervisor can recreate it using
        coro_factory and restart it (depending on criticality).

        Args:
            name: Unique identifier for this task (for logging and tracking).
            coro_factory: Zero-arg callable returning a fresh coroutine.
                e.g., lambda: self._monitor_download(id, handle)
            criticality: How critical this task is — determines restart behavior.

        Returns:
            The asyncio.Task object.
        """
        # Cancel any existing task with the same name
        if name in self._tasks:
            self._tasks[name].cancel()

        self._specs[name] = {"criticality": criticality, "coro_factory": coro_factory}
        self._restart_counts.setdefault(name, 0)

        coro = coro_factory()
        task = asyncio.create_task(self._run_supervised(name, coro, criticality))
        self._tasks[name] = task
        logger.debug(f"Supervised task spawned: {name} ({criticality.value})")
        return task

    def spawn_one_shot(self, name: str, coro: object) -> asyncio.Task:
        """Spawn a best-effort one-shot task (no restart on crash).

        For fire-and-forget operations that should still be logged on
        failure instead of dying silently (e.g., WebSocket broadcasts,
        post-download file organization). Automatically removes itself
        from _tasks on completion to prevent unbounded accumulation.

        Args:
            name: Unique identifier for this task.
            coro: The coroutine to run.

        Returns:
            The asyncio.Task object.
        """
        task_name = f"{name}_{id(coro)}" if name in self._tasks else name

        self._specs[task_name] = {"criticality": TaskCriticality.BEST_EFFORT}
        task = asyncio.create_task(self._run_supervised(task_name, coro, TaskCriticality.BEST_EFFORT))
        self._tasks[task_name] = task

        def _cleanup(_task):
            self._tasks.pop(task_name, None)
            self._specs.pop(task_name, None)

        task.add_done_callback(_cleanup)
        return task

    def is_alive(self, name: str) -> bool:
        """Return True if the task exists and is not done."""
        task = self._tasks.get(name)
        return task is not None and not task.done()

    def cancel(self, name: str) -> None:
        """Cancel a specific supervised task by name."""
        if name in self._tasks:
            self._tasks[name].cancel()
            del self._tasks[name]
            self._specs.pop(name, None)

    def cancel_by_prefix(self, prefix: str) -> int:
        """Cancel all supervised tasks whose name starts with the given prefix.

        Returns the number of tasks cancelled.
        """
        to_cancel = [name for name in self._tasks if name.startswith(prefix)]
        for name in to_cancel:
            self._tasks[name].cancel()
            del self._tasks[name]
            self._specs.pop(name, None)
        return len(to_cancel)

    async def shutdown(self) -> None:
        """Cancel all supervised tasks and wait for them to finish.

        Sets a shutdown flag so auto-restart won't kick in during teardown.
        """
        self._shutting_down = True
        logger.info(f"TaskSupervisor: shutting down {len(self._tasks)} tasks...")
        
        # 1. Cancel everything first
        for name, task in self._tasks.items():
            if not task.done():
                logger.debug(f"TaskSupervisor: cancelling task '{name}'")
                task.cancel()
            
        if self._tasks:
            # 2. Wait for completion with a hard timeout to avoid hanging main process
            try:
                # We use a short wait to see which ones are hanging
                pending_tasks = list(self._tasks.values())
                done, pending = await asyncio.wait(
                    pending_tasks, 
                    timeout=5.0,
                    return_when=asyncio.ALL_COMPLETED
                )
                
                if pending:
                    hanging = [name for name, t in self._tasks.items() if t in pending]
                    logger.warning(f"TaskSupervisor: {len(pending)} tasks hanging during shutdown: {hanging}")
                
                # 3. Final gather with return_exceptions=True to clean up
                # We already waited 5s above, this should be fast if they responded.
                # If they didn't, we'll just move on after this turns into a result.
                await asyncio.gather(*pending_tasks, return_exceptions=True)
            except Exception as e:
                logger.error(f"TaskSupervisor: error during gather in shutdown: {e}")
                
        self._tasks.clear()
        self._specs.clear()
        logger.info("TaskSupervisor shutdown complete")

    def is_alive(self, name: str) -> bool:
        """Check if a supervised task is still running."""
        task = self._tasks.get(name)
        return task is not None and not task.done()

    @property
    def active_count(self) -> int:
        """Number of currently running supervised tasks."""
        return sum(1 for t in self._tasks.values() if not t.done())

    async def _run_supervised(self, name: str, coro, criticality: TaskCriticality) -> None:
        """Run a coroutine with supervision: catch crashes and handle restarts."""
        try:
            await coro
        except asyncio.CancelledError:
            if not self._shutting_down:
                logger.debug(f"Supervised task cancelled: {name}")
            raise
        except Exception as e:
            if self._shutting_down:
                logger.debug(f"Supervised task crashed during shutdown: {name} ({e})")
                return

            if criticality == TaskCriticality.CRITICAL:
                restarts = self._restart_counts.get(name, 0)
                logger.error(f"Supervised CRITICAL task crashed: {name} — {e}")
                if restarts < MAX_RESTARTS:
                    self._schedule_restart(name, criticality)
                elif self._on_critical_failure:
                    self._on_critical_failure(name, e)
            elif criticality == TaskCriticality.IMPORTANT:
                restarts = self._restart_counts.get(name, 0)
                if restarts < MAX_RESTARTS:
                    logger.warning(
                        f"Supervised IMPORTANT task crashed: {name} — {e} "
                        f"(restart {restarts + 1}/{MAX_RESTARTS})"
                    )
                    self._schedule_restart(name, criticality)
                else:
                    logger.error(
                        f"Supervised IMPORTANT task exhausted restarts: {name} "
                        f"({MAX_RESTARTS}/{MAX_RESTARTS})"
                    )
            else:
                logger.info(f"Supervised BEST_EFFORT task crashed: {name} — {e}")
        except SystemExit as e:
            logger.error(f"Supervised task requested exit: {name} (code: {e.code})")
            if criticality == TaskCriticality.CRITICAL and self._on_critical_failure:
                self._on_critical_failure(name, e)
            raise
        except BaseException as e:
            logger.critical(f"Supervised task encountered base exception: {name} — {type(e).__name__}: {e}")
            if criticality == TaskCriticality.CRITICAL and self._on_critical_failure:
                self._on_critical_failure(name, e)
            raise

    def _schedule_restart(self, name: str, criticality: TaskCriticality) -> None:
        """Schedule a restart with exponential backoff."""
        self._restart_counts[name] = self._restart_counts.get(name, 0) + 1
        restart_count = self._restart_counts[name]
        backoff = RESTART_BACKOFF_BASE_SECONDS * (2 ** (restart_count - 1))

        spec = self._specs.get(name, {})
        coro_factory = spec.get("coro_factory")

        if not coro_factory:
            logger.error(f"Cannot restart {name}: no coro_factory registered")
            return

        async def _restart():
            logger.info(f"Restarting supervised task '{name}' in {backoff}s (attempt {restart_count})")
            await asyncio.sleep(backoff)
            if not self._shutting_down:
                try:
                    new_coro = coro_factory()
                    task = asyncio.create_task(
                        self._run_supervised(name, new_coro, criticality)
                    )
                    self._tasks[name] = task
                except Exception as e:
                    logger.error(f"Failed to restart task '{name}': {e}")

        asyncio.create_task(_restart())