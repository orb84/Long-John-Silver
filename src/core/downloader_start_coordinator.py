"""DownloadStartCoordinator for LJS.

Handles starting new torrent downloads — engine add, monitor spawning,
selective download configuration, and per-download lock management.
"""

import asyncio
from typing import Any, Callable, Optional
from loguru import logger
from src.core.models import DownloadItem, DownloadStatus, TaskCriticality
from src.core.storage_path_availability import StoragePathUnavailableError
from src.core.download_storage_recovery import StorageUnavailableDownloadReason
from src.core.downloader_lifecycle import (
    DownloadLifecycleContext,
    DownloadLifecycleMonitor,
    SeedingPolicy,
)
from src.core.downloader_monitor_registry import DownloadMonitorRegistry


class DownloadStartCoordinator:
    """Coordinates starting a new download: engine, monitor lifecycle, selective setup.

    Owns the per-download lock map and selective-download bookkeeping,
    keeping those concerns out of the ``DownloadManager`` facade.

    Callbacks are stored as instance attributes so that ``spawn_restartable``
    factories created by ``start_download`` always read the latest values
    when a task restarts.
    """

    def __init__(
        self,
        engine: Any,
        db: Any,
        supervisor: Any,
        queue: Any,
        download_dir: str,
        category_registry: object | None,
        seed_policy: SeedingPolicy,
        monitor_registry: DownloadMonitorRegistry,
        bundle_download_handler: Any = None,
        on_file_progress: Callable | None = None,
        on_files_renamed: Callable | None = None,
        on_files_restored: Callable | None = None,
    ) -> None:
        self._engine = engine
        self._db = db
        self._supervisor = supervisor
        self._queue = queue
        self._download_dir = download_dir
        self._category_registry = category_registry
        self._seed_policy = seed_policy
        self._monitor_registry = monitor_registry
        self._bundle_download_handler = bundle_download_handler
        self._add_locks: dict[str, asyncio.Lock] = {}
        self._selective_downloads: dict[str, dict] = {}

        # Stable callbacks (bound methods, set once at construction)
        self._on_file_progress = on_file_progress
        self._on_files_renamed = on_files_renamed
        self._on_files_restored = on_files_restored

        # Mutable callbacks (user-settable, updated via setters)
        self._on_stats_callback: Optional[Callable] = None
        self._on_ready_callback: Optional[Callable] = None
        self._on_complete_callback: Optional[Callable] = None

    # ── Mutable callback setters ────────────────────────────────────

    def set_on_stats_callback(self, cb: Callable | None) -> None:
        """Set or clear the stats-update callback."""
        self._on_stats_callback = cb

    def set_on_ready_callback(self, cb: Callable | None) -> None:
        """Set or clear the seeding-started (ready) callback."""
        self._on_ready_callback = cb

    def set_on_complete_callback(self, cb: Callable | None) -> None:
        """Set or clear the download-complete callback."""
        self._on_complete_callback = cb

    # ── Per-download lock management ────────────────────────────────

    def get_add_lock(self, download_id: str) -> asyncio.Lock:
        """Get or create a per-download-ID lock for add_magnet serialization."""
        if download_id not in self._add_locks:
            self._add_locks[download_id] = asyncio.Lock()
        return self._add_locks[download_id]

    # ── Selective download bookkeeping ──────────────────────────────

    def register_selective(self, download_id: str,
                           episodes: list[int] | None = None,
                           season: int | None = None,
                           descriptors: list[dict] | None = None) -> None:
        """Store selective-download configuration for a queued item.

        ``episodes``/``season`` are transitional public fields.  New callers
        should pass category-owned unit descriptors so this coordinator does
        not need to know any category coordinate names.
        """
        self._selective_downloads[download_id] = {
            'episodes': episodes,
            'season': season,
            'descriptors': descriptors or [],
        }

    def pop_selective(self, download_id: str) -> dict | None:
        """Pop and return selective-download config for a download."""
        return self._selective_downloads.pop(download_id, None)

    def unregister_selective(self, download_id: str) -> None:
        """Remove selective-download config for a cancelled download."""
        self._selective_downloads.pop(download_id, None)

    # ── Download start ──────────────────────────────────────────────

    async def start_download(
        self,
        item: DownloadItem,
        selective: dict | None = None,
        save_path: str | None = None,
        sharing_enabled: bool = False,
    ) -> None:
        """Run the public lifecycle operation start_download.

        This method is part of runtime orchestration.  Keep it idempotent
        where practical, clean up owned resources, and delegate specialized
        work to collaborators to preserve testability. ``save_path`` allows
        seed-in-place downloads to write directly into category library roots;
        ``sharing_enabled`` is persisted on the item but handles remain in the
        download bandwidth class until the lifecycle monitor reaches seeding.
        """
        try:
            handle = await self._engine.add_magnet(
                item.magnet,
                item.id,
                save_path=save_path,
                mode="download",
            )
        except StoragePathUnavailableError as e:
            logger.warning(f"Download storage unavailable for {item.item_name} ({item.id}): {e}")
            item.status = DownloadStatus.STALLED
            item.reason = StorageUnavailableDownloadReason.from_error(e)
            await self._db.downloads.upsert_download(item)
            raise
        except Exception as e:
            logger.error(f"Failed to start download for {item.item_name} ({item.id}): {e}")
            item.status = DownloadStatus.FAILED
            item.reason = f"Startup failed: {e}"
            await self._db.downloads.upsert_download(item)
            raise

        item.status = DownloadStatus.DOWNLOADING
        await self._db.downloads.upsert_download(item)

        task = self._supervisor.spawn_restartable(
            f'dl_monitor_{item.id}',
            lambda: self._build_monitor_coro(item.id, handle),
            TaskCriticality.CRITICAL,
        )
        self._monitor_registry.register(item.id, task)
        self._queue.register_active(item.id)

        if selective:
            if self._bundle_download_handler is None:
                from src.core.bundle_download import BundleDownloadHandler
                self._bundle_download_handler = BundleDownloadHandler(self._category_registry)
            self._supervisor.spawn_one_shot(
                f'selective_dl_{item.id}',
                self._bundle_download_handler.configure_selective_download(
                    item.id, handle,
                    category_id=item.category_id or '',
                    target_descriptors=selective.get('descriptors') or [],
                    target_descriptor=item.unit_descriptor,
                    target_episodes=selective.get('episodes'),
                    target_season=selective.get('season'),
                ),
            )
        logger.info(f'Started download: {item.item_name}')

    async def build_monitor_coro(self, download_id: str, handle: Any) -> None:
        """Build and run a ``DownloadLifecycleMonitor`` for a single download.

        Public entry point — called from ``resume_download`` via a
        ``spawn_restartable`` factory.
        """
        await self._build_monitor_coro(download_id, handle)

    async def _build_monitor_coro(self, download_id: str, handle: Any) -> None:
        """Build and run a ``DownloadLifecycleMonitor`` for a single download.

        Reads the latest callback values from instance attributes so that
        ``spawn_restartable`` restarts get the most current callbacks.
        """
        ctx = DownloadLifecycleContext(
            db=self._db,
            supervisor=self._supervisor,
            engine=self._engine,
            download_dir=self._download_dir,
            bundle_download_handler=self._bundle_download_handler,
            category_registry=self._category_registry,
            seed_policy=self._seed_policy,
            on_stats_callback=self._on_stats_callback,
            on_file_progress=self._on_file_progress,
            on_files_renamed=self._on_files_renamed,
            on_files_restored=self._on_files_restored,
            on_ready_callback=self._on_ready_callback,
            on_complete_callback=self._on_complete_callback,
            queue=self._queue,
            monitor_registry=self._monitor_registry,
        )
        await DownloadLifecycleMonitor(ctx=ctx).run(download_id, handle)
