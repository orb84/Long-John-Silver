"""
Queue manager for LJS downloads.
Handles concurrency, priority promotion, and preemption.
"""

import asyncio
import time
from typing import TYPE_CHECKING, Any, Callable
from loguru import logger
from src.core.models import DownloadStatus, DownloadPriority

if TYPE_CHECKING:
    from src.core.database import Database
    from src.core.torrent_engine import TorrentEngine
    from src.core.models import DownloadItem


class QueueManager:
    """Manages the download queue, enforcing concurrency limits and priorities."""

    def __init__(self, db: "Database", engine: "TorrentEngine", max_concurrent: int = 3) -> None:
        self._db = db
        self._engine = engine
        self._max_concurrent = max_concurrent
        self._active_downloads: set[str] = set()
        self._poll_seconds = 10
        self._last_gate_log: dict[str, float] = {}
        self._gate_log_interval_seconds = 60.0

    def register_active(self, download_id: str) -> None:
        """Register a download as actively consuming a slot."""
        self._active_downloads.add(download_id)

    def deregister_active(self, download_id: str) -> None:
        """Deregister a download, freeing a slot."""
        self._active_downloads.discard(download_id)

    def active_count(self) -> int:
        """Return the number of active downloads."""
        return len(self._active_downloads)

    def sync_active(self, download_ids: list[str] | set[str]) -> None:
        """Replace the in-memory active slot set from persisted state.

        This is used after settings changes or restart recovery so concurrency
        enforcement does not depend on stale in-memory bookkeeping.
        """
        self._active_downloads = {str(i) for i in download_ids if i}

    def set_max_concurrent(self, max_concurrent: int) -> None:
        """Update the active-download concurrency limit at runtime."""
        self._max_concurrent = max(1, int(max_concurrent or 1))
        logger.info(f"Queue concurrency limit set to {self._max_concurrent}")

    async def run_loop(self, start_callback: Callable, pause_callback: Callable, can_start_callback: Callable | None = None) -> None:
        """Main queue management loop.

        ``can_start_callback`` lets the application distinguish explicit
        user-queued work from background/auto-discovery work.  This keeps
        ``auto_download=False`` authoritative even after a restart with stale
        queued items in the database.
        """
        try:
            while True:
                await asyncio.sleep(self._poll_seconds)
                
                # Check for promotion.  Rebuild active-slot bookkeeping from
                # persisted state each pass so UI/settings changes and monitor
                # restarts cannot leave stale slot counts behind.
                all_active = await self._db.downloads.get_active_downloads()
                self.sync_active([d.id for d in all_active if d.status == DownloadStatus.DOWNLOADING])
                queued = [d for d in all_active if d.status == DownloadStatus.QUEUED]
                if not queued:
                    continue

                priority_order = {
                    DownloadPriority.HIGH: 0,
                    DownloadPriority.NORMAL: 1,
                    DownloadPriority.LOW: 2,
                }
                queued.sort(key=lambda i: (
                    priority_order.get(i.priority, 1),
                    i.created_at if hasattr(i, 'created_at') and i.created_at else "",
                ))

                next_item = queued[0]
                if can_start_callback is not None:
                    try:
                        allowed = can_start_callback(next_item)
                        if hasattr(allowed, "__await__"):
                            allowed = await allowed
                    except Exception as exc:
                        logger.warning(f"Queue start gate failed for {next_item.id}: {exc}")
                        allowed = False
                    if not allowed:
                        self._log_gate_hold(next_item)
                        continue
                
                if self.active_count() < self._max_concurrent:
                    logger.info(f"Promoting: {next_item.item_name} ({next_item.id})")
                    try:
                        await start_callback(next_item)
                    except Exception as e:
                        logger.error(f"Failed to start promoted download {next_item.id}: {e}")
                    continue

                # Preemption
                if next_item.priority == DownloadPriority.HIGH:
                    victim = await self._find_preempt_victim(all_active)
                    if victim:
                        logger.info(f"Preempting {victim.id} for {next_item.id}")
                        try:
                            await pause_callback(victim.id, requeue=True, keep_start_allowed=True)
                        except TypeError:
                            await pause_callback(victim.id, requeue=True)
                        try:
                            await start_callback(next_item)
                        except Exception as e:
                            logger.error(f"Failed to start preempted download {next_item.id}: {e}")

        except asyncio.CancelledError:
            pass


    def _log_gate_hold(self, item: "DownloadItem") -> None:
        """Throttle repeated queue-gate messages for the same queued item."""
        item_id = getattr(item, "id", "unknown")
        now = time.monotonic()
        last = self._last_gate_log.get(item_id, 0.0)
        if now - last >= self._gate_log_interval_seconds:
            logger.debug(f"Queue promotion held for {getattr(item, 'item_name', item_id)} ({item_id})")
            self._last_gate_log[item_id] = now

    async def _find_preempt_victim(self, all_active):
        """Find a LOW-priority item to preempt."""
        downloading = [
            d for d in all_active
            if d.status == DownloadStatus.DOWNLOADING and d.priority == DownloadPriority.LOW
        ]
        if not downloading:
            return None
        downloading.sort(key=lambda i: i.progress)
        return downloading[0]
