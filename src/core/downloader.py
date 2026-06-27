"""Download manager for LJS.

Coordinates torrent queue, engine integration, progress tracking,
file renaming, and seeding lifecycle management.
"""

import asyncio
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from loguru import logger
from typing import Any, Callable, Optional
from types import SimpleNamespace
from src.core.models import DownloadImportContext, DownloadItem, DownloadStatus, DownloadPriority, TaskCriticality
from src.core.task_supervisor import TaskSupervisor
from src.core.downloader_lifecycle import SeedingPolicy, TorrentRuntimePriorityController
from src.core.downloader_monitor_registry import DownloadMonitorRegistry
from src.core.downloader_progress_cache import DownloadFileProgressCache
from src.core.downloader_start_coordinator import DownloadStartCoordinator
from src.core.download_storage_recovery import DownloadStorageRecoveryService
from src.core.security.path_policy import SafePathResolver, SecurityPolicyError
from src.core.download_dependencies import DownloadDependencies
from src.core.downloader_sharing_mixin import DownloadSharingMixin
from src.core.download_partial_files import PartialDownloadMarkerRepairService
from src.core.storage_path_availability import StoragePathGuard
from src.core.download_import_identity import (
    _apply_import_context_defaults,
    _find_duplicate_import_context,
    _normalize_import_context,
)


class DownloadManager(DownloadSharingMixin):
    """Orchestrates torrent downloads by coordinating the engine and queue."""

    def __init__(self, deps: DownloadDependencies) -> None:
        self._deps = deps
        self._download_dir = deps.download_dir
        self._db = deps.db
        self._supervisor = deps.supervisor
        self._settings_manager = deps.settings_manager
        self._max_concurrent = deps.max_concurrent
        self._engine = deps.engine
        self._queue = deps.queue
        self._bandwidth = deps.bandwidth

        self._monitor_registry = DownloadMonitorRegistry()
        self._progress_cache = DownloadFileProgressCache()

        self._seed_policy = SeedingPolicy(
            seed_ratio_target=deps.seed_ratio_target,
            seed_duration_hours=deps.seed_duration_hours,
        )

        self._on_stats_callback: Optional[Callable] = None
        self._on_complete_callback: Optional[Callable] = None
        self._on_ready_callback: Optional[Callable] = None

        self._storage_recovery = DownloadStorageRecoveryService(self._db, self._download_dir)

        self._start_coordinator = DownloadStartCoordinator(
            engine=self._engine,
            db=self._db,
            supervisor=self._supervisor,
            queue=self._queue,
            download_dir=self._download_dir,
            category_registry=deps.category_registry,
            seed_policy=self._seed_policy,
            monitor_registry=self._monitor_registry,
            on_file_progress=self._progress_cache.update,
            on_files_renamed=self._progress_cache.mark_renamed,
            on_files_restored=self._progress_cache.mark_restored,
        )

        self._active_downloads: set[str] = set()
        # Explicit user starts may continue later even when auto-download is off.
        self._explicit_start_allowed: set[str] = set()
        self._queue_lock = asyncio.Lock()
        availability = StoragePathGuard.try_prepare_directory(deps.download_dir)
        if not availability.available_for_writes:
            logger.warning(f"Download directory is unavailable at startup: {availability.reason}")

    async def initialize(self) -> None:
        """Initialize the engine and start the queue manager."""
        await self._engine.initialize()
        await self._cancel_duplicate_active_media_identity_rows()
        self._supervisor.spawn_restartable(
            "download_queue_manager",
            lambda: self._queue.run_loop(self._start_download, self.pause_download, self._can_start_queued_download),
            TaskCriticality.CRITICAL,
        )
        self._supervisor.spawn_restartable(
            "bandwidth_manager",
            self._bandwidth.run_loop,
            TaskCriticality.CRITICAL,
        )
        self._supervisor.spawn_restartable(
            "download_storage_recovery",
            lambda: self._storage_recovery.run_loop(self._enforce_concurrency_limit),
            TaskCriticality.IMPORTANT,
        )
        logger.info("Download manager initialized.")


    async def _cancel_duplicate_active_media_identity_rows(self) -> None:
        """Cancel duplicate active/queued rows for the same category unit.

        This is a startup safety brake for old builds that allowed multiple
        magnets for the same episode/season to coexist.  It keeps the most useful
        row and cancels the rest without deleting payload files, preventing LJS
        from consuming disk with duplicate transfers as soon as the app boots.
        """
        try:
            from src.core.repositories.download import _import_contexts_overlap
            rows = await self._db.downloads.get_active_downloads()
        except Exception as exc:
            logger.debug("Could not scan active downloads for duplicate identities: {}", exc)
            return
        with_context = [row for row in rows if getattr(row, "import_context", None) is not None]
        duplicate_ids: set[str] = set()
        keepers: list[DownloadItem] = []

        def rank(row: DownloadItem) -> tuple[int, float, int, str]:
            status_rank = {
                DownloadStatus.DOWNLOADING: 4,
                DownloadStatus.SEEDING: 3,
                DownloadStatus.QUEUED: 2,
                DownloadStatus.PAUSED: 1,
                DownloadStatus.STALLED: 0,
            }.get(row.status, 0)
            return (
                status_rank,
                float(getattr(row, "progress", 0.0) or 0.0),
                int(getattr(row, "source_seeders", None) or getattr(row, "num_seeds", 0) or 0),
                str(getattr(row, "created_at", "") or ""),
            )

        for row in with_context:
            if row.id in duplicate_ids:
                continue
            matched_index = None
            for index, keeper in enumerate(keepers):
                try:
                    if _import_contexts_overlap(row.import_context, keeper.import_context):
                        matched_index = index
                        break
                except Exception:
                    continue
            if matched_index is None:
                keepers.append(row)
                continue
            keeper = keepers[matched_index]
            if rank(row) > rank(keeper):
                duplicate_ids.add(keeper.id)
                keepers[matched_index] = row
            else:
                duplicate_ids.add(row.id)

        for download_id in sorted(duplicate_ids):
            try:
                await self.cancel_download(download_id, cleanup_files=False)
                logger.warning("Cancelled duplicate active download identity at startup: {}", download_id)
            except Exception as exc:
                logger.warning("Failed to cancel duplicate active download identity {}: {}", download_id, exc)

    async def close(self) -> None:
        """Shut down the download subsystem without exposing partial files.

        Older builds restored ``.downloading`` suffixes during shutdown even for
        incomplete torrents.  That made paused/interrupted partial files look
        finished in Plex/file managers.  Completed torrents restore their names
        in the lifecycle monitor; shutdown now leaves incomplete payload names
        alone so the temporary suffix remains an honest safety marker.
        """
        self._supervisor.cancel_by_prefix("dl_monitor_")
        self._supervisor.cancel("download_queue_manager")
        self._progress_cache.clear_renamed()

        await self._engine.close()
        logger.info("Download manager shut down.")

    def set_stats_callback(self, callback: Callable) -> None:
        """Set stats update callback."""
        self._on_stats_callback = callback
        self._start_coordinator.set_on_stats_callback(callback)

    def set_completion_callback(self, callback: Callable) -> None:
        """Set download completion callback.

        Called when a download finishes seeding and transitions to COMPLETE.
        The callback receives the download_id as its only argument.
        """
        self._on_complete_callback = callback
        self._start_coordinator.set_on_complete_callback(callback)

    def set_ready_callback(self, callback: Callable) -> None:
        """Set ready callback — fires when seeding starts (torrent 100% downloaded).

        This is the right moment to hardlink/copy completed files to the library.
        The callback receives the download_id as its only argument.
        """
        self._on_ready_callback = callback
        self._start_coordinator.set_on_ready_callback(callback)

    async def set_file_priority(self, download_id: str, file_index: int, priority: int) -> bool:
        """Change priority of a single file within a multi-file download.

        Args:
            download_id: The download to modify.
            file_index: Index of the file within the torrent.
            priority: Libtorrent priority (0=ignore, 4=normal, 7=max).

        Returns:
            True if the priority was updated.
        """
        item = await self._db.downloads.get_download(download_id)
        if not item:
            return False

        # Update the model even if engine is not available (for queued/paused).
        # The live handle is then reconciled from the persisted model so distinct
        # per-file priorities use the same staged semantics as freshly parsed
        # torrent metadata.
        for f in item.files:
            if f.file_index == file_index:
                f.priority = priority
                break
        await self._db.downloads.upsert_download(item)

        handle = self._engine.get_handle(download_id)
        if handle and handle.has_metadata():
            TorrentRuntimePriorityController().apply(download_id, handle, item)
            logger.info(f'Set file {file_index} priority to {priority} for {download_id}')
        return True

    def get_file_progress(self, download_id: str) -> list[dict]:
        """Return cached per-file progress."""
        return self._progress_cache.get_file_progress(download_id)

    async def refresh_bandwidth_limits(self) -> None:
        """Re-evaluate schedules/defaults and reapply the effective caps now."""
        await self._bandwidth.check_and_apply()

    async def set_max_concurrent(self, max_concurrent: int) -> None:
        """Apply a new max-concurrent-download limit immediately.

        Settings are hot-editable from the UI.  Updating the saved value is
        not enough: the in-memory queue manager and libtorrent session must be
        changed, and any excess active torrents need to be pushed back to the
        queue so the new limit is actually honored.
        """
        new_limit = max(1, int(max_concurrent or 1))
        old_limit = self._max_concurrent
        self._max_concurrent = new_limit
        if hasattr(self._queue, "set_max_concurrent"):
            self._queue.set_max_concurrent(new_limit)
        if hasattr(self._engine, "set_max_concurrent"):
            await self._engine.set_max_concurrent(new_limit)
        logger.info(f"Download concurrency limit changed from {old_limit} to {new_limit}")
        await self._enforce_concurrency_limit()

    async def _enforce_concurrency_limit(self) -> None:
        """Pause/requeue excess active downloads after a concurrency change."""
        async with self._queue_lock:
            await self._sync_active_slots_from_state()
            active = [
                d for d in await self._db.downloads.get_active_downloads()
                if d.status == DownloadStatus.DOWNLOADING
            ]
            priority_rank = {
                DownloadPriority.HIGH: 0,
                DownloadPriority.NORMAL: 1,
                DownloadPriority.LOW: 2,
            }
            active.sort(key=lambda d: (priority_rank.get(d.priority, 1), self._download_unit_sort_key(d), d.created_at))
            keepers = active[:self._max_concurrent]
            if hasattr(self._queue, "sync_active"):
                self._queue.sync_active({d.id for d in keepers})
            if len(active) <= self._max_concurrent:
                return
            # Keep earlier/high-priority work active; pause later/lower-priority work.
            victims = active[self._max_concurrent:]
            for victim in victims:
                logger.info(f"Pausing {victim.id} to enforce max_concurrent={self._max_concurrent}")
                await self.pause_download(victim.id, requeue=True, keep_start_allowed=True)

    def active_count(self) -> int:
        """Return active transfer slots currently registered by the queue."""
        return self._queue.active_count()

    def max_concurrent(self) -> int:
        """Return the current active-download concurrency limit."""
        return self._max_concurrent

    async def _sync_active_slots_from_state(self) -> None:
        """Synchronize queue slots from persisted state and engine handles.

        The UI and the queue manager must not rely on stale in-memory slot
        bookkeeping.  A torrent is considered to consume a slot only when the
        database says it is DOWNLOADING.  If a libtorrent handle is still alive
        for a non-downloading row (for example after a previous failed
        concurrency enforcement), pause/remove that handle and zero the visible
        rates so it cannot appear to keep downloading while queued.
        """
        try:
            rows = await self._db.downloads.get_active_downloads()
        except Exception:
            return
        active_ids: set[str] = set()
        for item in rows:
            if item.status == DownloadStatus.DOWNLOADING:
                active_ids.add(item.id)
                continue
            if item.status in (DownloadStatus.QUEUED, DownloadStatus.PAUSED, DownloadStatus.STALLED):
                handle = self._engine.get_handle(item.id)
                if handle is not None:
                    try:
                        await asyncio.wait_for(asyncio.to_thread(handle.pause), timeout=2.0)
                    except Exception:
                        pass
                    if item.status == DownloadStatus.QUEUED:
                        try:
                            await self._engine.remove_torrent(item.id)
                        except Exception:
                            pass
                if item.download_rate or item.upload_rate or item.eta_seconds or item.num_peers or getattr(item, "num_seeds", 0):
                    item.download_rate = 0.0
                    item.upload_rate = 0.0
                    item.eta_seconds = 0.0
                    item.num_peers = 0
                    item.num_seeds = 0
                    await self._db.downloads.upsert_download(item)
        if hasattr(self._queue, "sync_active"):
            self._queue.sync_active(active_ids)

    async def park_stalled_download(self, download_id: str, reason: str = "") -> DownloadItem | None:
        """Park a stalled download without cancelling or blacklisting it.

        Parked downloads keep their torrent record and, when possible, their
        paused engine handle.  They do not consume an active slot.  The
        download-health supervisor can periodically resume them for bounded
        test windows.
        """
        item = await self._db.downloads.get_download(download_id)
        if not item or item.status in (DownloadStatus.CANCELLED, DownloadStatus.FAILED, DownloadStatus.COMPLETE):
            return None

        handle = self._engine.get_handle(download_id)
        if handle:
            try:
                await asyncio.wait_for(asyncio.to_thread(handle.pause), timeout=3.0)
            except asyncio.TimeoutError:
                logger.warning(f"Timeout calling handle.pause while parking {download_id}")
            except Exception:
                pass

        self._queue.deregister_active(download_id)
        self._active_downloads.discard(download_id)
        self._supervisor.cancel(f"dl_monitor_{download_id}")
        self._monitor_registry.unregister(download_id)

        item.status = DownloadStatus.STALLED
        item.reason = f"{item.reason}; parked: {reason}" if item.reason and reason else (reason or item.reason)
        await self._db.downloads.upsert_download(item)
        logger.info(f"Parked stalled download {download_id}: {item.item_name}")
        return item

    async def start_health_test(self, download_id: str, temporary_priority: DownloadPriority = DownloadPriority.HIGH) -> DownloadItem | None:
        """Give a parked torrent a priority test window.

        If all active slots are full, the least important later/low-priority
        transfer is requeued so the health test can actually run.
        """
        item = await self._db.downloads.get_download(download_id)
        if not item or item.status not in (DownloadStatus.STALLED, DownloadStatus.PAUSED, DownloadStatus.QUEUED):
            return None

        self._explicit_start_allowed.add(download_id)
        if temporary_priority:
            item.priority = temporary_priority
            await self._db.downloads.upsert_download(item)

        async with self._queue_lock:
            await self._sync_active_slots_from_state()
            if self._queue.active_count() >= self._max_concurrent:
                victim = await self._find_health_test_victim(exclude_id=download_id)
                if victim:
                    logger.info(f"Pausing {victim.id} to free a health-test slot for {download_id}")
                    await self.pause_download(victim.id, requeue=True, keep_start_allowed=True)

            handle = self._engine.get_handle(download_id)
            if handle:
                try:
                    await asyncio.wait_for(asyncio.to_thread(handle.resume), timeout=3.0)
                    item.status = DownloadStatus.DOWNLOADING
                    await self._db.downloads.upsert_download(item)
                    self._queue.register_active(download_id)
                    task = self._supervisor.spawn_restartable(
                        f"dl_monitor_{download_id}",
                        lambda: self._start_coordinator.build_monitor_coro(download_id, handle),
                        TaskCriticality.CRITICAL,
                    )
                    self._monitor_registry.register(download_id, task)
                    if hasattr(self._engine, "rebalance_rate_limits"):
                        await self._engine.rebalance_rate_limits()
                    return item
                except asyncio.TimeoutError:
                    logger.warning(f"Timeout calling handle.resume for health test {download_id}")
                except Exception:
                    pass

            item.status = DownloadStatus.QUEUED
            await self._db.downloads.upsert_download(item)
            if self._queue.active_count() < self._max_concurrent and await self._can_start_queued_download(item):
                await self._start_download(item)
                refreshed = await self._db.downloads.get_download(download_id)
                return refreshed or item
        return item

    @staticmethod
    def _download_unit_sort_key(item: DownloadItem) -> tuple:
        """Return descriptor-first unit ordering for generic queue decisions.

        The downloader is intentionally category-neutral: it does not inspect
        descriptor coordinates or legacy structured fields directly.  The
        model exposes a normalized key built from category-owned descriptor
        conventions, with old row coordinates as a contained fallback.
        """
        return tuple(getattr(item, "unit_sort_key", ()) or ())

    async def _find_health_test_victim(self, exclude_id: str) -> DownloadItem | None:
        """Pick the safest active transfer to pause for a health-test slot."""
        active = [
            d for d in await self._db.downloads.get_active_downloads()
            if d.id != exclude_id and d.status == DownloadStatus.DOWNLOADING
        ]
        if not active:
            return None
        priority_rank = {
            DownloadPriority.LOW: 0,
            DownloadPriority.NORMAL: 1,
            DownloadPriority.HIGH: 2,
        }
        # Prefer pausing low-priority work first.  Within that priority band,
        # pause the later category unit and least-progressed transfer. Unit
        # ordering comes from the category-owned descriptor when present; legacy
        # structured coordinates are contained inside DownloadItem's fallback.
        min_priority = min(priority_rank.get(d.priority, 1) for d in active)
        candidates = [d for d in active if priority_rank.get(d.priority, 1) == min_priority]
        candidates.sort(key=lambda d: (self._download_unit_sort_key(d), -float(d.progress or 0.0)))
        return candidates[-1]

    async def add_magnet(self, magnet_link: str, item_name: str = "",
                           torrent_title: str = "",
                           priority: DownloadPriority = DownloadPriority.NORMAL,
                           reason: str = "",
                           season: int | None = None,
                           episode: int | None = None,
                           user_id: str | None = None,
                           selective_episodes: list[int] | None = None,
                           selective_season: int | None = None,
                           selective_descriptors: list[dict[str, Any]] | None = None,
                           language: str = "",
                           category_id: str = "",
                           item_id: str = "",
                           estimated_size_bytes: int | None = None,
                           source_seeders: int | None = None,
                           import_context: DownloadImportContext | dict[str, Any] | None = None) -> DownloadItem:
        """Add a magnet link and queue it after storage-capacity preflight."""
        if not magnet_link:
            raise ValueError("Magnet link cannot be None or empty")

        if self._deps.storage_monitor is not None:
            decision = self._deps.storage_monitor.check_download_capacity(
                category_id=category_id or None,
                estimated_bytes=estimated_size_bytes,
            )
            if not decision.ok:
                raise ValueError(f"Insufficient storage for download: {decision.reason}")
            if decision.status == "warning":
                logger.warning(f"Storage warning before queueing '{item_name}': {decision.reason}")

        # Resolve HTTP/HTTPS torrent URLs on-the-fly to secure magnet links
        if magnet_link.startswith("http://") or magnet_link.startswith("https://"):
            if self._deps.torrent_resolver is not None:
                logger.info(f"Interposing HTTP/HTTPS torrent resolver to decode: {magnet_link}")
                try:
                    resolved = await self._deps.torrent_resolver.resolve_to_magnet(magnet_link)
                    magnet_link = resolved
                except Exception as e:
                    logger.error(f"Failed to resolve torrent URL {magnet_link} to magnet: {e}")
                    raise ValueError(f"Failed to resolve torrent URL: {e}") from e
            else:
                logger.warning("No torrent_resolver injected in DownloadDependencies, cannot resolve URL.")

        normalized_context = _normalize_import_context(
            import_context,
            item_name=item_name,
            category_id=category_id,
            item_id=item_id,
            season=season,
            episode=episode,
            language=language,
            torrent_title=torrent_title or item_name,
        )
        item_name, category_id, item_id, season, episode, language = _apply_import_context_defaults(
            normalized_context, item_name=item_name, category_id=category_id, item_id=item_id,
            season=season, episode=episode, language=language,
        )

        download_id = hashlib.md5(magnet_link.encode()).hexdigest()[:12]
        explicit_user_request = self._is_explicit_user_reason(reason)
        if not explicit_user_request and not await self._can_create_background_download_row(
            category_id=category_id,
            item_id=item_id or item_name,
            item_name=item_name,
            import_context=normalized_context,
            reason=reason,
        ):
            raise ValueError(
                f"Background download blocked by category policy for '{item_name}'. "
                "Use an explicit manual/user-approved action or enable the item's automation setting."
            )

        lock = self._start_coordinator.get_add_lock(download_id)
        async with lock:
            existing = await self._db.downloads.get_download(download_id)
            if existing:
                if existing.status in (
                    DownloadStatus.DOWNLOADING,
                    DownloadStatus.COMPLETE,
                ):
                    logger.info(
                        f'Skipping duplicate magnet {download_id} '
                        f"(status={existing.status.value}) for '{item_name}'"
                    )
                    return existing
                if existing.status in (DownloadStatus.QUEUED, DownloadStatus.PAUSED, DownloadStatus.STALLED):
                    auto_start_allowed = bool(
                        existing.status == DownloadStatus.QUEUED
                        and await self._can_start_queued_download(existing)
                    )
                    if explicit_user_request or auto_start_allowed:
                        if reason:
                            existing.reason = reason
                        existing.priority = priority
                        if normalized_context and not existing.import_context:
                            existing.import_context = normalized_context
                        await self._db.downloads.upsert_download(existing)
                        item = existing
                        if explicit_user_request:
                            logger.info(
                                f"User-approved duplicate magnet {download_id} is already {existing.status.value}; "
                                f"promoting it for immediate queue processing."
                            )
                        else:
                            logger.info(
                                f"Auto-download duplicate magnet {download_id} is already queued; "
                                f"promoting it because the tracked item allows automation."
                            )
                    else:
                        logger.info(
                            f'Skipping duplicate magnet {download_id} '
                            f"(status={existing.status.value}) for '{item_name}'"
                        )
                        return existing
                else:
                    logger.info(
                        f'Skipping duplicate magnet {download_id} '
                        f"(status={existing.status.value}) for '{item_name}'"
                    )
                    return existing
            else:
                duplicate = await _find_duplicate_import_context(
                    self._db.downloads, normalized_context, download_id=download_id
                )
                if duplicate:
                    auto_start_allowed = bool(
                        duplicate.status == DownloadStatus.QUEUED
                        and await self._can_start_queued_download(duplicate)
                    )
                    if duplicate.status in (DownloadStatus.QUEUED, DownloadStatus.PAUSED, DownloadStatus.STALLED) and (
                        explicit_user_request or auto_start_allowed
                    ):
                        if reason:
                            duplicate.reason = reason
                        duplicate.priority = priority
                        await self._db.downloads.upsert_download(duplicate)
                        item = duplicate
                        download_id = duplicate.id
                        if explicit_user_request:
                            logger.info(
                                f"User-approved duplicate media identity "
                                f"{normalized_context.stable_unit_key or normalized_context.stable_provider_key} "
                                f"is already {duplicate.status.value}; promoting it for immediate queue processing."
                            )
                        else:
                            logger.info(
                                f"Auto-download duplicate media identity "
                                f"{normalized_context.stable_unit_key or normalized_context.stable_provider_key} "
                                f"is already queued; promoting it because the tracked item allows automation."
                            )
                    else:
                        logger.info(
                            f"Skipping duplicate media identity "
                            f"{normalized_context.stable_unit_key or normalized_context.stable_provider_key} for '{item_name}'"
                        )
                        return duplicate
                else:
                    item = DownloadItem(
                        id=download_id, item_name=item_name, magnet=magnet_link,
                        status=DownloadStatus.QUEUED, priority=priority, reason=reason,
                        season=season, episode=episode, user_id=user_id,
                        language=language, category_id=category_id,
                        torrent_title=torrent_title or item_name,
                        item_id=item_id or item_name,
                        source_seeders=source_seeders,
                        import_context=normalized_context,
                    )
                    await self._db.downloads.upsert_download(item)

        if explicit_user_request:
            self._explicit_start_allowed.add(download_id)
            await self._maybe_enable_category_auto_download_after_user_download(
                category_id=category_id,
                item_id=item_id or item_name,
                import_context=normalized_context,
            )

        bundle_context = (normalized_context.candidate_snapshot or {}).get('bundle_context') if normalized_context else None
        descriptor = normalized_context.unit_descriptor if normalized_context else {}
        should_selectively_configure = bool(
            selective_episodes
            or selective_season is not None
            or selective_descriptors
            or (bundle_context and descriptor and descriptor.get('granularity') not in {'', 'item'})
        )
        if should_selectively_configure:
            descriptors = list(selective_descriptors or [])
            if descriptor and descriptor not in descriptors:
                descriptors.append(descriptor)
            self._start_coordinator.register_selective(
                download_id, episodes=selective_episodes, season=selective_season, descriptors=descriptors,
            )

        logger.info(f"Queued magnet for '{item_name}' (id={download_id}, reason='{reason}')")

        async with self._queue_lock:
            await self._sync_active_slots_from_state()
            if self._queue.active_count() < self._max_concurrent:
                if await self._can_start_queued_download(item):
                    await self._start_download(item)
                else:
                    logger.info(
                        f"Queued '{item_name}' but held start because auto_download is disabled "
                        f"and reason='{reason}' is not an explicit user/manual approval."
                    )
        # Starting a batch can race with stale state from previous runs.  Always
        # re-apply the slot gate after queueing so max_concurrent is authoritative.
        await self._enforce_concurrency_limit()
        return item

    async def cancel_download(self, download_id: str, cleanup_files: bool = True) -> None:
        """Cancel an active or queued download."""
        self._queue.deregister_active(download_id)
        self._active_downloads.discard(download_id)
        self._explicit_start_allowed.discard(download_id)
        await self._engine.remove_torrent(download_id)

        self._supervisor.cancel(f"dl_monitor_{download_id}")
        self._monitor_registry.unregister(download_id)
        self._start_coordinator.unregister_selective(download_id)
        self._progress_cache.clear(download_id)

        item = await self._db.downloads.get_download(download_id)
        if item:
            was_stalled = item.status == DownloadStatus.STALLED
            item.status = DownloadStatus.CANCELLED
            await self._db.downloads.upsert_download(item)
            if was_stalled and self._deps.blacklist is not None:
                pattern = item.torrent_title or item.magnet
                if pattern:
                    await self._deps.blacklist.add(pattern=pattern, reason="Stalled download automatically blacklisted upon cancellation")
            await self._cancel_matching_release_watch_for_download(item)
            if cleanup_files:
                # Clean up all known files (for multi-file torrents)
                dl_dir = Path(self._download_dir).resolve()
                resolver = SafePathResolver(
                    allowed_roots=[dl_dir],
                    config=self._deps.settings_manager.settings.security,
                )
                cleaned = 0
                if item.files:
                    for df in item.files:
                        path = (dl_dir / df.file_path).resolve()
                        try:
                            resolver.require(path, purpose="download.cancel.path", must_exist=False)
                        except SecurityPolicyError:
                            continue
                        if path.exists():
                            try:
                                resolver.safe_unlink(path, purpose="download.cancel.cleanup", move_to_trash=False)
                                cleaned += 1
                            except Exception:
                                pass
                        # Also check .downloading variant
                        dl_path = Path(str(path) + '.downloading')
                        if dl_path.exists():
                            try:
                                resolver.safe_unlink(dl_path, purpose="download.cancel.cleanup_partial", move_to_trash=False)
                                cleaned += 1
                            except Exception:
                                pass
                if item.file_path and cleaned == 0:
                    await self._cleanup_partial_files(item.file_path, download_id)
                if cleaned:
                    logger.info(f'Cleaned up {cleaned} file(s) for cancelled download {download_id}')

    async def _cancel_matching_release_watch_for_download(self, item: DownloadItem) -> None:
        """Suppress release-watch retries for a user-cancelled queued/download row.

        The download layer stays category-neutral by using the category-owned
        unit descriptor carried in the import context.  If a user cancels an
        auto-discovered episode, the matching release-watch row must not turn
        around and requeue the same unit later.
        """
        repo = getattr(self._db, "release_watches", None) if self._db is not None else None
        if repo is None or not hasattr(repo, "cancel_unit"):
            return
        context = getattr(item, "import_context", None)
        category_id = str(getattr(item, "category_id", "") or getattr(context, "category_id", "") or "").strip()
        item_id = str(getattr(item, "item_id", "") or getattr(context, "item_id", "") or getattr(item, "item_name", "") or "").strip()
        descriptor = getattr(context, "unit_descriptor", None) if context is not None else None
        unit_key = ""
        if isinstance(descriptor, dict):
            unit_key = str(descriptor.get("stable_key") or descriptor.get("label") or "").strip()
        if not unit_key:
            season = getattr(context, "season", None) if context is not None else getattr(item, "season", None)
            episode = getattr(context, "episode", None) if context is not None else getattr(item, "episode", None)
            try:
                if season and episode:
                    unit_key = f"S{int(season):02d}E{int(episode):02d}"
                elif season:
                    unit_key = f"S{int(season):02d}"
            except Exception:
                unit_key = ""
        if not category_id or not item_id or not unit_key:
            return
        try:
            await repo.cancel_unit(
                category_id,
                item_id,
                unit_key,
                error="matching download was cancelled by user",
                outcome={"status": "cancelled_by_user", "download_id": item.id},
            )
            logger.info("Cancelled matching release watch for user-cancelled download: {}/{}/{}", category_id, item_id, unit_key)
        except Exception as exc:
            logger.debug("Could not cancel matching release watch for {}: {}", item.id, exc)

    async def pause_download(self, download_id: str, requeue: bool = False, keep_start_allowed: bool = False) -> DownloadItem | None:
        """Pause a download.

        Args:
            download_id: Target download.
            requeue: Put the item back into QUEUED instead of PAUSED.
            keep_start_allowed: Preserve explicit user approval when pausing only
                because a runtime concurrency limit was lowered.
        """
        item = await self._db.downloads.get_download(download_id)
        if not item or item.status not in (DownloadStatus.DOWNLOADING, DownloadStatus.QUEUED):
            return None

        handle = self._engine.get_handle(download_id)
        if handle:
            # Do not remove .downloading while a torrent is merely paused.
            # Partial files must remain visibly partial; the lifecycle monitor
            # restores final names only after libtorrent reports the torrent is
            # a seed (100% complete).
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(handle.pause),
                    timeout=3.0,
                )
            except asyncio.TimeoutError:
                logger.warning(f"Timeout calling handle.pause for {download_id}")
            except Exception:
                pass
            if hasattr(self._engine, "rebalance_rate_limits"):
                await self._engine.rebalance_rate_limits()

        self._queue.deregister_active(download_id)
        self._active_downloads.discard(download_id)
        if not keep_start_allowed:
            self._explicit_start_allowed.discard(download_id)
        self._supervisor.cancel(f"dl_monitor_{download_id}")
        self._monitor_registry.unregister(download_id)

        item.status = DownloadStatus.QUEUED if requeue else DownloadStatus.PAUSED
        item.download_rate = 0.0
        item.upload_rate = 0.0
        item.eta_seconds = 0.0
        item.num_peers = 0
        item.num_seeds = 0
        await self._db.downloads.upsert_download(item)

        if requeue:
            await self._engine.remove_torrent(download_id)

        logger.info(f"Paused download {download_id}")
        return item

    async def resume_download(self, download_id: str) -> DownloadItem | None:
        """Resume a paused download, respecting the current concurrency limit."""
        item = await self._db.downloads.get_download(download_id)
        if not item or item.status not in (DownloadStatus.PAUSED, DownloadStatus.STALLED):
            return None

        # A direct user resume counts as explicit permission to start later even
        # when global auto-download is disabled.
        self._explicit_start_allowed.add(download_id)

        async with self._queue_lock:
            await self._sync_active_slots_from_state()
            if self._queue.active_count() >= self._max_concurrent:
                item.status = DownloadStatus.QUEUED
                await self._db.downloads.upsert_download(item)
                logger.info(f"Resume queued for {download_id}; concurrency limit is full")
                return item

            handle = self._engine.get_handle(download_id)
            if handle:
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(handle.resume),
                        timeout=3.0,
                    )
                    item.status = DownloadStatus.DOWNLOADING
                    await self._db.downloads.upsert_download(item)
                    self._queue.register_active(download_id)
                    task = self._supervisor.spawn_restartable(
                        f"dl_monitor_{download_id}",
                        lambda: self._start_coordinator.build_monitor_coro(download_id, handle),
                        TaskCriticality.CRITICAL,
                    )
                    self._monitor_registry.register(download_id, task)
                    return item
                except asyncio.TimeoutError:
                    logger.warning(f"Timeout calling handle.resume for {download_id}")
                except Exception:
                    pass

            item.status = DownloadStatus.QUEUED
            await self._db.downloads.upsert_download(item)
            try:
                await self._start_download(item)
            except Exception as e:
                logger.error(f"Failed to start resumed download {download_id}: {e}")
                # A direct resume should not strand a paused item as failed just
                # because immediate engine start was unavailable. Preserve the
                # user's intent by leaving it queued for the next scheduler /
                # recovery pass instead of requiring manual restart.
                item.status = DownloadStatus.QUEUED
                item.reason = f"Resume queued after immediate start failed: {e}"
                await self._db.downloads.upsert_download(item)
        return item

    async def get_active_downloads(self) -> list[DownloadItem]:
        """Return all currently active downloads."""
        return await self._db.downloads.get_active_downloads()

    async def get_recent_downloads(self, limit: int = 20) -> list[DownloadItem]:
        """Return recent downloads."""
        return await self._db.downloads.get_recent_downloads(limit)

    async def get_queued_downloads(self) -> list[DownloadItem]:
        """Return all queued downloads sorted by priority."""
        active = await self._db.downloads.get_active_downloads()
        queued = [d for d in active if d.status == DownloadStatus.QUEUED]
        priority_order = {
            DownloadPriority.HIGH: 0,
            DownloadPriority.NORMAL: 1,
            DownloadPriority.LOW: 2,
        }
        queued.sort(key=lambda i: priority_order.get(i.priority, 1))
        return queued

    async def get_download(self, download_id: str) -> DownloadItem | None:
        """Retrieve a single download by ID."""
        return await self._db.downloads.get_download(download_id)

    async def update_download(self, item: DownloadItem) -> None:
        """Persist changes to a DownloadItem (files list, file_path, etc.)."""
        await self._db.downloads.upsert_download(item)

    async def set_priority(self, download_id: str, priority: DownloadPriority | None) -> DownloadItem | None:
        """Change priority of a download."""
        if priority is None:
            priority = DownloadPriority.NORMAL
        elif isinstance(priority, str):
            try:
                priority = DownloadPriority(priority.lower())
            except ValueError:
                logger.warning(f"Invalid priority string '{priority}' — defaulting to NORMAL")
                priority = DownloadPriority.NORMAL
        item = await self._db.downloads.get_download(download_id)
        if not item or item.status in (DownloadStatus.CANCELLED, DownloadStatus.FAILED, DownloadStatus.COMPLETE):
            return None
        item.priority = priority
        await self._db.downloads.upsert_download(item)
        # Priority changes can affect which active downloads deserve one of the
        # limited transfer slots. Re-run the concurrency gate after saving.
        await self._enforce_concurrency_limit()
        return item

    async def restart_download(self, download_id: str) -> DownloadItem | None:
        """Restart a failed or cancelled download."""
        item = await self._db.downloads.get_download(download_id)
        if not item or item.status not in (DownloadStatus.FAILED, DownloadStatus.CANCELLED):
            return None
        await self._engine.remove_torrent(download_id)
        item.status = DownloadStatus.QUEUED
        item.progress = 0.0
        await self._db.downloads.upsert_download(item)
        return item

    async def repair_partial_file_suffixes(self) -> None:
        """Delegate startup repair of missing ``.downloading`` markers."""
        await PartialDownloadMarkerRepairService(self._db, self._download_dir).repair()

    async def reconcile_completed_downloads(self, limit: int = 200) -> int:
        """Promote 100% payloads that were stranded before ready callbacks ran.

        A completed torrent can be left in DOWNLOADING/QUEUED/PAUSED/STALLED
        when libtorrent reports byte completion but the monitor exits, restarts,
        or loses its callback before the SEEDING transition.  The visible symptom
        is exactly what the UI screenshot showed: full progress bars and final
        filenames without ``.downloading``, but no library import.  This repair
        is idempotent and fires the normal ready callback after promoting the
        row to SEEDING so library exposure uses the same path as live completion.
        """
        repaired = 0
        for item in await self._db.downloads.get_recent_downloads(limit):
            if item.status not in {
                DownloadStatus.DOWNLOADING,
                DownloadStatus.QUEUED,
                DownloadStatus.PAUSED,
                DownloadStatus.STALLED,
            }:
                continue
            handle = self._engine.get_handle(item.id)
            if not self._item_looks_complete(item, handle):
                continue
            try:
                await self._promote_completed_item(item, handle)
                repaired += 1
            except Exception as exc:
                logger.error(f"Completed-download reconciliation failed for {item.id}/{item.item_name}: {exc}")
        if repaired:
            logger.info(f"Reconciled {repaired} stranded completed download(s).")
        return repaired

    def _item_looks_complete(self, item: DownloadItem, handle: object | None = None) -> bool:
        """Return whether an active row has enough evidence to be complete."""
        if handle is not None:
            try:
                if handle.is_seed():
                    return True
                status = handle.status()
                progress = float(getattr(status, "progress", 0.0) or 0.0)
                total_done = int(getattr(status, "total_done", 0) or 0)
                total_size = 0
                try:
                    total_size = int(handle.torrent_file().total_size())
                except Exception:
                    total_size = int(getattr(item, "total_size", 0) or 0)
                if progress >= 0.999 or (total_size > 0 and total_done >= max(total_size - 1024 * 1024, 1)):
                    return True
            except Exception:
                pass
        if float(item.progress or 0.0) >= 0.999:
            return True
        if item.total_size and item.downloaded_bytes and int(item.downloaded_bytes) >= max(int(item.total_size) - 1024 * 1024, 1):
            return True
        if item.files:
            meaningful = [df for df in item.files if int(getattr(df, "priority", 4) or 0) > 0]
            if meaningful and all(self._file_info_looks_complete(item, df) for df in meaningful):
                return True
        elif item.file_path:
            path = Path(item.file_path)
            return path.exists() and not str(path).endswith(".downloading")
        return False

    def _file_info_looks_complete(self, item: DownloadItem, df: object) -> bool:
        """Return whether a persisted file row appears complete on disk/bytes."""
        status = str(getattr(df, "status", "") or "").lower()
        if status in {"complete", "organized"}:
            return True
        size = int(getattr(df, "size", 0) or 0)
        downloaded = int(getattr(df, "downloaded_bytes", 0) or 0)
        if size > 0 and downloaded >= max(size - 1024 * 1024, 1):
            return True
        rel = str(getattr(df, "file_path", "") or "")
        if not rel:
            return False
        roots = [Path(item.save_path)] if item.save_path else []
        roots.append(Path(self._download_dir))
        for root in roots:
            final_path = (root / (rel[:-12] if rel.endswith(".downloading") else rel)).resolve()
            partial_path = Path(str(final_path) + ".downloading")
            try:
                if final_path.exists() and not partial_path.exists():
                    if size <= 0 or final_path.stat().st_size >= max(size - 1024 * 1024, 1):
                        return True
            except Exception:
                continue
        return False

    async def _promote_completed_item(self, item: DownloadItem, handle: object | None = None) -> None:
        """Force a stranded completed row through the normal ready path."""
        if handle is not None:
            await self._restore_final_names_from_handle(item.id, handle)
            try:
                if not item.save_path:
                    item.save_path = str(Path(handle.save_path()).resolve())
            except Exception:
                pass
            if not item.file_path:
                item.file_path = self._largest_file_path_from_handle(handle)
        for df in item.files:
            if str(df.status).lower() not in {"organized"}:
                df.status = "complete"
                if df.size and not df.downloaded_bytes:
                    df.downloaded_bytes = df.size
        item.status = DownloadStatus.SEEDING
        item.progress = 1.0
        item.download_rate = 0.0
        item.eta_seconds = 0.0
        item.completed_at = item.completed_at or datetime.now(timezone.utc)
        await self._db.downloads.upsert_download(item)
        self._queue.deregister_active(item.id)
        self._active_downloads.discard(item.id)
        if self._on_ready_callback:
            await self._on_ready_callback(item.id)

    async def _restore_final_names_from_handle(self, download_id: str, handle: object) -> None:
        """Remove temporary suffixes from a handle during reconciliation."""
        try:
            tf = handle.torrent_file()
            for i in range(tf.num_files()):
                current_path = tf.files().at(i).path
                if current_path.endswith(".downloading"):
                    handle.rename_file(i, current_path[:-12])
            self._progress_cache.mark_restored(download_id)
        except Exception as exc:
            logger.debug(f"Could not restore final names while reconciling {download_id}: {exc}")

    def _largest_file_path_from_handle(self, handle: object) -> str | None:
        """Return the largest file path for a libtorrent handle, if known."""
        try:
            tf = handle.torrent_file()
            largest = max(tf.files(), key=lambda f: f.size)
            return str(Path(handle.save_path()) / largest.path)
        except Exception:
            return None

    async def recover_downloads(self) -> None:
        """Recover downloads on startup without violating automation settings."""
        await self.repair_partial_file_suffixes()
        active = await self._db.downloads.get_active_downloads()
        if hasattr(self._queue, "sync_active"):
            self._queue.sync_active([])
        to_start = []
        for item in active:
            original_status = item.status
            if item.status == DownloadStatus.PAUSED or not item.magnet:
                continue
            item.status = DownloadStatus.QUEUED
            item.download_rate = 0.0
            item.upload_rate = 0.0
            item.eta_seconds = 0.0
            item.num_peers = 0
            item.num_seeds = 0
            await self._db.downloads.upsert_download(item)
            # Only explicit/manual or currently-allowed category automation may
            # resume automatically after restart.  A background TV row that was
            # already DOWNLOADING in an older unsafe build must not become
            # implicitly user-approved forever.
            if self._is_explicit_user_reason(getattr(item, "reason", None)) or (
                (original_status == DownloadStatus.SEEDING and item.sharing_enabled)
                and await self._can_start_queued_download(item)
            ):
                self._explicit_start_allowed.add(item.id)
            if await self._can_start_queued_download(item):
                to_start.append(item)
            else:
                logger.info(
                    f"Recovered queued download but did not auto-start because automation is disabled "
                    f"and it was not user-approved: {item.item_name}"
                )

        started = 0
        for item in to_start:
            if started >= self._max_concurrent:
                continue
            try:
                await self._start_download(item)
                started += 1
            except Exception:
                pass

    @staticmethod
    def _is_explicit_user_reason(reason: str | None) -> bool:
        """Return whether a persisted queue reason represents user-approved work.

        ``auto_download=False`` should stop background discovery, not prevent a
        torrent the user just approved in chat, Suggestions, Notifications, or
        the library inspector from starting immediately.  Background rows keep
        using explicit auto-discovery reasons and remain held when automation is
        disabled.
        """
        text = (reason or "").strip().lower().replace("_", " ")
        explicit_reasons = {
            "manual",
            "manual user upload",
            "user requested",
            "user approved",
            "ui",
            "approved",
        }
        explicit_prefixes = (
            "manual user upload",
            "user approved",
            "user-approved",
            "suggestion approved",
            "notification approved",
        )
        return text in explicit_reasons or any(text.startswith(prefix) for prefix in explicit_prefixes)


    async def _maybe_enable_category_auto_download_after_user_download(
        self,
        *,
        category_id: str,
        item_id: str,
        import_context: DownloadImportContext | None,
    ) -> None:
        """Let the owning category react to an explicit user download.

        Generic download code does not decide what an ongoing season, book
        series, game version, or album feed means.  It only offers a hook after
        an explicit user-approved queue action so the category can opt into
        future automation when that is safe.
        """
        if not category_id or import_context is None:
            return
        try:
            category = self._deps.category_registry.get(category_id) if self._deps.category_registry else None
        except Exception:
            category = None
        hook = getattr(category, "maybe_enable_auto_download_after_user_download", None)
        if not callable(hook):
            return
        try:
            context = SimpleNamespace(
                db=self._db,
                settings=self._settings_manager.settings if self._settings_manager else None,
                settings_manager=self._settings_manager,
                category_registry=self._deps.category_registry,
            )
            await hook(
                item_id=item_id,
                import_context=import_context,
                settings_manager=self._settings_manager,
                context=context,
            )
        except Exception as exc:
            logger.debug("Category auto-download activation hook failed for %s/%s: %s", category_id, item_id, exc)

    @staticmethod
    def _normalized_item_identity(value: object) -> str:
        """Normalize generic item names/keys for envelope-level matching."""
        text = str(value or "").strip().casefold()
        text = re.sub(r"[^\w]+", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    def _download_item_identity_candidates(self, item: DownloadItem) -> set[str]:
        """Return generic item identity candidates carried by a download row."""
        candidates = {
            self._normalized_item_identity(getattr(item, "item_id", "")),
            self._normalized_item_identity(getattr(item, "item_name", "")),
        }
        context = getattr(item, "import_context", None)
        if context is not None:
            candidates.update({
                self._normalized_item_identity(getattr(context, "item_id", "")),
                self._normalized_item_identity(getattr(context, "canonical_title", "")),
                self._normalized_item_identity(getattr(context, "display_title", "")),
                self._normalized_item_identity(getattr(context, "localized_title", "")),
                self._normalized_item_identity(getattr(context, "original_title", "")),
            })
        return {candidate for candidate in candidates if candidate}

    @staticmethod
    def _iter_tracked_items(settings: Any) -> list[Any]:
        """Return tracked items from either modern ItemList or legacy plain list settings."""
        tracked = getattr(settings, "tracked_items", []) if settings is not None else []
        return list(getattr(tracked, "items", tracked) or [])

    async def _reconcile_tracked_item_download_policy(self, category: object | None, tracked: object, category_id: str) -> None:
        """Let the owning category reconcile visible state before queue decisions."""
        hook = getattr(category, "reconcile_settings_item_with_persisted_state", None)
        if not callable(hook):
            return
        media_repo = getattr(self._db, "media", None)
        item_key = str(getattr(tracked, "key", "") or "")
        persisted = None
        if media_repo and item_key:
            try:
                persisted = await media_repo.get_category_item(category_id, item_key)
            except Exception as exc:
                logger.debug("Download policy persisted-state lookup failed for {}/{}: {}", category_id, item_key, exc)
        try:
            changed = bool(hook(tracked, persisted, self._settings_manager.settings))
        except Exception as exc:
            logger.debug("Category download-policy reconciliation failed for {}/{}: {}", category_id, item_key, exc)
            return
        if not changed:
            return
        try:
            await asyncio.to_thread(self._settings_manager.save, self._settings_manager.settings)
        except Exception as exc:
            logger.debug("Could not persist reconciled download policy for {}/{}: {}", category_id, item_key, exc)
        if media_repo and item_key:
            try:
                payload = tracked.model_dump(mode="json") if hasattr(tracked, "model_dump") else dict(getattr(tracked, "__dict__", {}) or {})
                payload.setdefault("category_id", category_id)
                payload.setdefault("item_id", item_key)
                payload.setdefault("key", item_key)
                payload.setdefault("item_type", category_id)
                await media_repo.upsert_category_item(category_id, item_key, payload)
            except Exception as exc:
                logger.debug("Could not persist reconciled category item policy for {}/{}: {}", category_id, item_key, exc)
        logger.warning(
            "Reconciled queued download policy for {}/{} before background queue/start decision.",
            category_id,
            item_key,
        )

    async def _tracked_item_auto_download_policy(self, item: DownloadItem) -> tuple[bool, bool]:
        """Return whether a queued/background row has item policy and whether it may start.

        Download rows carry category/item identity but the queue manager must not
        know category semantics.  When a row maps to a tracked item, the owning
        category interprets the conventional automation field.  If a category has
        an explicit queue hook but no matching tracked item, background work is
        denied instead of inheriting a global switch.
        """
        try:
            settings = self._settings_manager.settings
            category_id = str(getattr(item, "category_id", "") or getattr(getattr(item, "import_context", None), "category_id", "") or "")
            wanted_category = category_id.strip().casefold()
            category_for_row = None
            try:
                category_for_row = self._deps.category_registry.get(category_id) if (self._deps.category_registry and category_id) else None
            except Exception:
                category_for_row = None
            row_has_category_policy = callable(getattr(category_for_row, "queued_background_start_allowed", None))
            wanted_names = self._download_item_identity_candidates(item)
            for tracked in self._iter_tracked_items(settings):
                tracked_category_id = str(getattr(tracked, "item_type", "") or category_id or "")
                tracked_category = tracked_category_id.strip().casefold()
                if wanted_category and tracked_category and tracked_category != wanted_category:
                    continue
                tracked_names = {
                    self._normalized_item_identity(getattr(tracked, "key", "")),
                    self._normalized_item_identity(getattr(tracked, "display_name", "")),
                }
                tracked_names = {name for name in tracked_names if name}
                if wanted_names and tracked_names and wanted_names.isdisjoint(tracked_names):
                    continue
                category = None
                try:
                    category = self._deps.category_registry.get(tracked_category_id) if self._deps.category_registry else None
                except Exception:
                    category = None
                await self._reconcile_tracked_item_download_policy(category, tracked, tracked_category_id)
                hook = getattr(category, "queued_background_start_allowed", None)
                if callable(hook):
                    return True, bool(hook(tracked, settings))
                value = getattr(tracked, "auto_download", None)
                if value is not None:
                    return True, bool(value)
            if row_has_category_policy:
                return True, False
        except Exception as exc:
            logger.debug("Tracked item auto-download policy lookup failed: {}", exc)
            return False, False
        return False, False

    def _tracked_item_auto_download_override(self, item: DownloadItem) -> bool | None:
        """Compatibility wrapper for older tests/callers.

        Runtime queue decisions use the async policy path so categories can read
        persisted visible state.  This wrapper preserves the previous synchronous
        inspection surface for deterministic unit tests; it is intentionally a
        no-I/O approximation.
        """
        try:
            settings = self._settings_manager.settings
            category_id = str(getattr(item, "category_id", "") or getattr(getattr(item, "import_context", None), "category_id", "") or "")
            wanted_category = category_id.strip().casefold()
            category_for_row = None
            try:
                category_for_row = self._deps.category_registry.get(category_id) if (self._deps.category_registry and category_id) else None
            except Exception:
                category_for_row = None
            row_has_category_policy = callable(getattr(category_for_row, "queued_background_start_allowed", None))
            wanted_names = self._download_item_identity_candidates(item)
            for tracked in self._iter_tracked_items(settings):
                tracked_category_id = str(getattr(tracked, "item_type", "") or category_id or "")
                tracked_category = tracked_category_id.strip().casefold()
                if wanted_category and tracked_category and tracked_category != wanted_category:
                    continue
                tracked_names = {
                    self._normalized_item_identity(getattr(tracked, "key", "")),
                    self._normalized_item_identity(getattr(tracked, "display_name", "")),
                }
                tracked_names = {name for name in tracked_names if name}
                if wanted_names and tracked_names and wanted_names.isdisjoint(tracked_names):
                    continue
                category = None
                try:
                    category = self._deps.category_registry.get(tracked_category_id) if self._deps.category_registry else None
                except Exception:
                    category = None
                hook = getattr(category, "queued_background_start_allowed", None)
                if callable(hook):
                    return bool(hook(tracked, settings))
                value = getattr(tracked, "auto_download", None)
                return None if value is None else bool(value)
            if row_has_category_policy:
                return False
        except Exception:
            return None
        return None

    async def _can_create_background_download_row(
        self,
        *,
        category_id: str,
        item_id: str,
        item_name: str,
        import_context: DownloadImportContext | None,
        reason: str,
    ) -> bool:
        """Return whether unattended code may create a new download row at all."""
        pseudo = SimpleNamespace(
            id="",
            category_id=category_id,
            item_id=item_id,
            item_name=item_name,
            import_context=import_context,
            reason=reason,
        )
        has_policy, allowed = await self._tracked_item_auto_download_policy(pseudo)  # type: ignore[arg-type]
        if has_policy:
            return allowed
        try:
            return bool(self._settings_manager.settings.auto_download)
        except Exception:
            return False

    async def _can_start_queued_download(self, item: DownloadItem) -> bool:
        """Return whether a queued item may consume a download slot now.

        Fresh user/manual work starts immediately.  Background discovery first
        honors the tracked item's own automation policy, then falls back to the
        global automation setting for older rows and categories without an
        item-level override.
        """
        if getattr(item, "id", None) in self._explicit_start_allowed:
            return True
        if self._is_explicit_user_reason(getattr(item, "reason", None)):
            return True
        if not hasattr(self, "_db"):
            legacy_override = self._tracked_item_auto_download_override(item)
            if legacy_override is not None:
                return legacy_override
        has_policy, override = await self._tracked_item_auto_download_policy(item)
        if has_policy:
            return override
        try:
            return bool(self._settings_manager.settings.auto_download)
        except Exception:
            return False

    async def _cleanup_partial_files(self, file_path, download_id):
        """Delete partial files from disk."""
        path = Path(file_path)
        if not path.exists():
            # Also check for the temporary extension
            path = Path(str(file_path) + ".downloading")
            if not path.exists(): return

        dl_dir = Path(self._download_dir).resolve()
        resolver = SafePathResolver(
            allowed_roots=[dl_dir],
            config=self._deps.settings_manager.settings.security,
        )
        try:
            resolver.safe_unlink(path, purpose="download.partial_cleanup", move_to_trash=False)
            parent = path.parent
            if parent != dl_dir and not any(parent.iterdir()):
                parent.rmdir()
        except Exception:
            return
