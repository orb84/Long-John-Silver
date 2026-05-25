"""
Download monitor registry for LJS.

Manages the lifecycle of per-download monitor tasks, providing
a central place to start, stop, and query monitor tasks by
download ID. Extracted from DownloadManager to reduce its
responsibilities.
"""

import asyncio
from typing import Optional

from loguru import logger


class DownloadMonitorRegistry:
    """Registry of active download monitor tasks.

    Each active download gets a coroutine spawned via TaskSupervisor.
    This registry tracks the resulting asyncio.Task by download_id
    so it can be queried or cancelled later.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}

    def register(self, download_id: str, task: asyncio.Task) -> None:
        """Register a monitor task for a download.

        Args:
            download_id: The download to track.
            task: The asyncio.Task running the monitor coroutine.
        """
        self._tasks[download_id] = task
        logger.debug(f"Monitor registered for {download_id}")

    def unregister(self, download_id: str) -> Optional[asyncio.Task]:
        """Remove and return the monitor task for a download, if any.

        Does NOT cancel the task — the caller is responsible for
        cancellation if needed.

        Args:
            download_id: The download to unregister.

        Returns:
            The asyncio.Task if one was registered, or None.
        """
        return self._tasks.pop(download_id, None)

    def get(self, download_id: str) -> Optional[asyncio.Task]:
        """Return the monitor task for a download, if still running.

        Args:
            download_id: The download to look up.

        Returns:
            The asyncio.Task or None.
        """
        return self._tasks.get(download_id)

    def is_active(self, download_id: str) -> bool:
        """Return True if a download has a running monitor task."""
        task = self._tasks.get(download_id)
        return task is not None and not task.done()

    def stop(self, download_id: str) -> None:
        """Cancel and unregister the monitor task for a download.

        Args:
            download_id: The download to stop monitoring.
        """
        task = self._tasks.pop(download_id, None)
        if task and not task.done():
            task.cancel()
            logger.debug(f"Monitor cancelled for {download_id}")

    def stop_all(self) -> None:
        """Cancel and unregister all monitor tasks."""
        for download_id, task in list(self._tasks.items()):
            if not task.done():
                task.cancel()
        self._tasks.clear()
        logger.debug("All monitors stopped")

    @property
    def active_count(self) -> int:
        """Number of currently running monitor tasks."""
        return sum(1 for t in self._tasks.values() if not t.done())

    @property
    def active_ids(self) -> list[str]:
        """List of download IDs with running monitors."""
        return [did for did, t in self._tasks.items() if not t.done()]
