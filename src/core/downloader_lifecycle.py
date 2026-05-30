"""
Download lifecycle management for LJS.

Extracted from DownloadManager to provide focused classes for
torrent file metadata parsing, progress tracking, lifecycle
monitoring, and seeding policy decisions.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from loguru import logger

from src.core.models import DownloadItem, DownloadFileInfo, DownloadStatus
from src.core.release_groups import ReleaseGroupTracker
from src.core.bundle_download import BundleDownloadHandler

# Stall detection constants
STALL_THRESHOLD_BYTES = 1024
STALL_TIMEOUT_SECONDS = 300
MAX_STALL_RETRIES = 2


class DownloadLifecycleStateMachine:
    """Validates high-level download state transitions.

    The downloader still talks to libtorrent callbacks, but persistent state
    changes should pass through this tiny state machine so reconciliation, UI,
    scheduler, and library import agree on lifecycle meaning.
    """

    _ALLOWED: dict[DownloadStatus, set[DownloadStatus]] = {
        DownloadStatus.QUEUED: {DownloadStatus.DOWNLOADING, DownloadStatus.PAUSED, DownloadStatus.CANCELLED, DownloadStatus.FAILED},
        DownloadStatus.DOWNLOADING: {DownloadStatus.SEEDING, DownloadStatus.STALLED, DownloadStatus.PAUSED, DownloadStatus.CANCELLED, DownloadStatus.FAILED},
        DownloadStatus.PAUSED: {DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING, DownloadStatus.CANCELLED, DownloadStatus.FAILED},
        DownloadStatus.STALLED: {DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING, DownloadStatus.CANCELLED, DownloadStatus.FAILED},
        DownloadStatus.SEEDING: {DownloadStatus.COMPLETE, DownloadStatus.CANCELLED, DownloadStatus.FAILED},
        DownloadStatus.COMPLETE: set(),
        DownloadStatus.FAILED: {DownloadStatus.QUEUED},
        DownloadStatus.CANCELLED: set(),
    }

    def can_transition(self, current: DownloadStatus, target: DownloadStatus) -> bool:
        """Return whether the transition is accepted by the lifecycle model."""
        if current == target:
            return True
        return target in self._ALLOWED.get(current, set())

    def transition(
        self,
        item: DownloadItem,
        target: DownloadStatus,
        *,
        reason: str = "",
        force: bool = False,
    ) -> DownloadItem:
        """Move an item to a new lifecycle state or raise ValueError.

        ``force`` is reserved for repair/reconciliation jobs that intentionally
        correct stale rows after crashes or older bugs.
        """
        current = item.status if isinstance(item.status, DownloadStatus) else DownloadStatus(str(item.status))
        if not force and not self.can_transition(current, target):
            raise ValueError(f"Invalid download transition: {current.value} -> {target.value}")
        item.status = target
        if reason:
            item.reason = reason
        return item


class TorrentFileMetadataParser:
    """Parses torrent file metadata through category-owned descriptors.

    The parser records generic file facts and lets the owning category describe
    each file/unit.  It does not decide what seasons, episodes, chapters, game
    versions, books, discs, or other coordinates mean.
    """

    def parse_file_infos(
        self,
        handle: Any,
        category_registry: object | None,
        category_id: str,
    ) -> list[DownloadFileInfo]:
        """Parse all files in a torrent handle into DownloadFileInfo objects."""
        tf = handle.torrent_file()
        category = self._get_category(category_registry, category_id)
        file_infos: list[DownloadFileInfo] = []
        for i in range(tf.num_files()):
            fp = tf.files().at(i)
            parsed = self._parse_with_category(category, fp.path)
            descriptor = self._descriptor_with_category(category, fp.path, parsed)
            selected = "sample" not in str(fp.path or "").lower()
            priority = self._priority_with_category(category, fp.path, parsed, descriptor, selected)
            file_infos.append(DownloadFileInfo(
                file_index=i,
                file_path=fp.path,
                size=fp.size,
                priority=priority,
                season=getattr(parsed, "season", None) if parsed else None,
                episode=getattr(parsed, "episode", None) if parsed else None,
                unit_descriptor=descriptor,
                status='pending',
            ))

        # Category-neutral descriptor ordering: if a category exposes sort keys,
        # earlier keys get higher priority for progressive availability.
        ordered = [f for f in file_infos if f.priority > 0 and (f.unit_descriptor or {}).get("sort_key") not in (None, [], "")]
        ordered.sort(key=lambda f: self._descriptor_sort_key(f.unit_descriptor))
        prio = 7
        for f in ordered:
            f.priority = max(min(prio, 7), 1)
            prio -= 1
        return file_infos

    def build_stats_files(self, file_infos: list[DownloadFileInfo]) -> list[dict]:
        """Build the stats dict for the frontend from file infos."""
        return [
            {
                'path': f.file_path,
                'name': f.file_path.split('/')[-1],
                'size': f.size,
                'downloaded': f.size if f.status == 'complete' else 0,
                'progress': 1.0 if f.status == 'complete' else 0.0,
                'file_index': f.file_index,
                'priority': f.priority,
                'season': f.season,
                'episode': f.episode,
                'unit_descriptor': f.unit_descriptor,
                'unit_label': (f.unit_descriptor or {}).get('label') or (f.unit_descriptor or {}).get('stable_key') or '',
                'status': f.status,
            }
            for f in file_infos
        ]

    @staticmethod
    def _get_category(category_registry: object | None, category_id: str) -> Any:
        """Look up a category by ID from the registry."""
        if category_registry and category_id:
            return category_registry.get(category_id)
        return None

    @staticmethod
    def _parse_with_category(category: Any | None, file_path: str) -> Any | None:
        if category and hasattr(category, 'parse_name'):
            try:
                return category.parse_name(Path(file_path).stem)
            except Exception:
                return None
        return None

    @staticmethod
    def _descriptor_with_category(category: Any | None, file_path: str, parsed: Any | None) -> dict[str, Any]:
        if category and hasattr(category, 'unit_descriptor_from_file'):
            try:
                return category.unit_descriptor_from_file(file_path, parsed)
            except Exception as exc:
                logger.debug(f"Category file descriptor failed for {file_path}: {exc}")
        label = Path(str(file_path or '')).name
        return {'granularity': 'file', 'label': label, 'stable_key': label, 'sort_key': [label], 'coordinates': {}}

    @staticmethod
    def _priority_with_category(category: Any | None, file_path: str, parsed: Any | None, descriptor: dict[str, Any], selected: bool) -> int:
        if category and hasattr(category, 'torrent_file_priority'):
            try:
                return max(0, min(7, int(category.torrent_file_priority(
                    file_path=file_path,
                    parsed=parsed,
                    file_descriptor=descriptor,
                    selected=selected,
                ))))
            except Exception as exc:
                logger.debug(f"Category file priority failed for {file_path}: {exc}")
        return 4 if selected else 0

    @staticmethod
    def _descriptor_sort_key(descriptor: dict[str, Any]) -> tuple[tuple[int, str], ...]:
        values = descriptor.get('sort_key') if isinstance(descriptor, dict) else []
        values = values if isinstance(values, list) else [values]
        return tuple(TorrentFileMetadataParser._sort_component(value) for value in values)

    @staticmethod
    def _sort_component(value: Any) -> tuple[int, str]:
        if isinstance(value, bool):
            return (0, '1' if value else '0')
        if isinstance(value, (int, float)):
            return (0, f'{float(value):020.6f}')
        text = str(value or '').strip()
        import re
        if re.fullmatch(r'-?\d+(?:\.\d+)?', text):
            return (0, f'{float(text):020.6f}')
        return (1, text.casefold())


class DownloadProgressStore:
    """Maps libtorrent stats into DownloadItem updates.

    Extracts progress, download/upload rates, peer count, size,
    ETA, and per-file progress from a libtorrent handle.
    """

    def extract_stats(self, handle: Any) -> dict[str, Any]:
        """Extract status dict from a libtorrent handle.

        Args:
            handle: Libtorrent torrent handle with metadata.

        Returns:
            Dict with progress, download_rate, upload_rate, num_peers,
            total_size, downloaded_bytes, eta_seconds, and optional files.
        """
        s = handle.status()
        stats: dict[str, Any] = {
            "progress": s.progress,
            "download_rate": s.download_rate,
            "upload_rate": s.upload_rate,
            "num_peers": getattr(s, "num_peers", 0),
            "num_seeds": getattr(s, "num_seeds", 0),
            "num_complete": getattr(s, "num_complete", -1),
            "num_incomplete": getattr(s, "num_incomplete", -1),
            "list_seeds": getattr(s, "list_seeds", 0),
            "list_peers": getattr(s, "list_peers", 0),
            "connect_candidates": getattr(s, "connect_candidates", 0),
            "total_size": 0,
            "downloaded_bytes": s.total_done,
            "eta_seconds": 0.0,
        }
        try:
            tf = handle.torrent_file()
            if tf:
                stats["total_size"] = tf.total_size()
                if s.download_rate > 0:
                    stats["eta_seconds"] = (tf.total_size() - s.total_done) / s.download_rate
                file_progress = handle.file_progress()
                files = tf.files()
                stats["files"] = [
                    {
                        "name": files.file_at(i).path.split("/")[-1],
                        "path": files.file_at(i).path,
                        "size": files.file_at(i).size,
                        "downloaded": fp,
                        "progress": fp / files.file_at(i).size if files.file_at(i).size > 0 else 1.0,
                        "file_index": i,
                    }
                    for i, fp in enumerate(file_progress)
                ]
        except Exception:
            pass
        return stats

    def update_item(self, item: DownloadItem, stats: dict[str, Any]) -> DownloadItem:
        """Update the DownloadItem fields from extracted stats.

        Args:
            item: The current download item to update.
            stats: Stats dict from extract_stats.

        Returns:
            The updated DownloadItem.
        """
        item.status = DownloadStatus.DOWNLOADING
        item.progress = stats["progress"]
        item.download_rate = stats["download_rate"]
        item.upload_rate = stats["upload_rate"]
        item.num_peers = stats["num_peers"]
        item.num_seeds = stats.get("num_seeds", 0) or 0
        item.total_size = stats["total_size"]
        item.downloaded_bytes = stats["downloaded_bytes"]
        item.eta_seconds = stats["eta_seconds"]

        # Update per-file progress
        files_list = stats.get("files")
        if files_list and item.files:
            def _normalized_path(value: str) -> str:
                text = str(value or "")
                return text[:-12] if text.endswith(".downloading") else text

            for fi in files_list:
                matched = None
                for df in item.files:
                    if _normalized_path(df.file_path) == _normalized_path(fi.get("path", "")):
                        matched = df
                        break
                if matched is None and fi.get("file_index") is not None:
                    for df in item.files:
                        if df.file_index == int(fi.get("file_index")):
                            matched = df
                            break
                if matched is None:
                    continue
                matched.downloaded_bytes = int(fi.get("downloaded", 0) or 0)
                progress_value = float(fi.get("progress", 0.0) or 0.0)
                if progress_value >= 1.0:
                    matched.status = "complete"
                elif progress_value > 0:
                    matched.status = "downloading"
        return item

    def mark_all_complete(self, item: DownloadItem) -> DownloadItem:
        """Mark all files as complete (used when seeding starts).

        Args:
            item: The download item whose files should be marked.

        Returns:
            The updated DownloadItem.
        """
        if item.files:
            for df in item.files:
                if df.status in ('pending', 'downloading'):
                    df.status = 'complete'
        return item


class SeedingPolicy:
    """Decides when to stop seeding a completed torrent.

    A download stops seeding when:
    - Minimum seed duration is met AND either:
      - Seed ratio target is reached, OR
      - Upload rate has dropped to zero (no peers).
    """

    def __init__(self, seed_ratio_target: float, seed_duration_hours: int) -> None:
        self._seed_ratio_target = seed_ratio_target
        self._seed_duration_hours = seed_duration_hours

    def update_limits(self, seed_ratio_target: float, seed_duration_hours: int) -> None:
        """Update seed stop limits for already-running monitors."""
        self._seed_ratio_target = max(0.0, float(seed_ratio_target or 0.0))
        self._seed_duration_hours = max(0, int(seed_duration_hours or 0))

    def should_stop(self, item: DownloadItem, upload_rate: float, seed_ratio: float = 0.0) -> bool:
        """Decide whether seeding should stop for the given item.

        Args:
            item: The download item (must have completed_at set).
            upload_rate: Current upload rate in bytes/sec.
            seed_ratio: Cumulative seed ratio (upload / download).

        Returns:
            True if seeding should stop.
        """
        if not item.completed_at:
            return False
        seeded_hours = (datetime.now(timezone.utc) - item.completed_at).total_seconds() / 3600
        # Library seed-in-place items are an explicit Fair Share choice.  Honor
        # the user's minimum duration for those rows only.  Non-sharing staging
        # downloads should not leave duplicate-looking originals in Downloads for
        # a week after the library import has already succeeded.
        if item.sharing_enabled:
            if seeded_hours < self._seed_duration_hours:
                return False
            if self._seed_ratio_target <= 0:
                return False
            return seed_ratio >= self._seed_ratio_target

        if self._seed_ratio_target <= 0:
            return upload_rate == 0
        return seed_ratio >= self._seed_ratio_target or upload_rate == 0


@dataclass
class DownloadLifecycleContext:
    """Shared context for download lifecycle monitoring.

    Bundles the services and callbacks that the DownloadLifecycleMonitor
    needs to operate. No mutable state from the manager is shared —
    the monitor tracks stall state internally and reports file progress
    and rename changes via callbacks.
    """

    db: Any
    """Database instance for persisting DownloadItem state."""
    supervisor: Any
    """TaskSupervisor for spawning/cancelling background tasks."""
    engine: Any
    """TorrentEngine for low-level torrent operations."""
    download_dir: str
    """Filesystem path where torrent data is stored."""
    bundle_download_handler: BundleDownloadHandler | None = None
    """Optional handler for selective bundle downloads."""
    release_group_tracker: ReleaseGroupTracker | None = None
    """Optional tracker for release group reputation feedback."""
    category_registry: object | None = None
    """Registry for looking up categories by ID."""
    metadata_parser: TorrentFileMetadataParser = field(default_factory=TorrentFileMetadataParser)
    """Parser for torrent file metadata."""
    progress_store: DownloadProgressStore = field(default_factory=DownloadProgressStore)
    """Store for mapping libtorrent stats to DownloadItem updates."""
    state_machine: DownloadLifecycleStateMachine = field(default_factory=DownloadLifecycleStateMachine)
    """Authoritative transition validator for persistent download states."""
    seed_policy: SeedingPolicy | None = None
    """Policy for deciding when to stop seeding."""
    on_stats_callback: Callable | None = None
    """Callback invoked with (download_id, stats_dict) for UI updates."""
    on_file_progress: Callable | None = None
    """Callback invoked with (download_id, file_progress_list) to cache file progress."""
    on_files_renamed: Callable | None = None
    """Callback invoked with (download_id) when .downloading extension is set."""
    on_files_restored: Callable | None = None
    """Callback invoked with (download_id) when .downloading extension is removed."""
    on_ready_callback: Callable | None = None
    """Callback invoked with (download_id) when seeding starts."""
    on_complete_callback: Callable | None = None
    """Callback invoked with (download_id) when seeding completes."""
    queue: Any | None = None
    """QueueManager used to release active download slots on seeding/stall."""
    monitor_registry: Any | None = None
    """Monitor registry used to unregister monitors that exit normally."""


class DownloadLifecycleMonitor:
    """Orchestrates per-download lifecycle monitoring.

    Runs the main monitoring loop: wait for metadata, rename files,
    parse file info, track progress, detect stalls, handle completion,
    and monitor seeding. Delegates to TorrentFileMetadataParser,
    DownloadProgressStore, and SeedingPolicy for focused operations.

    Stall tracking is internal (no shared mutable dicts from the
    manager). File progress and rename state are reported back via
    callbacks on DownloadLifecycleContext.
    """

    def __init__(self, ctx: DownloadLifecycleContext) -> None:
        self._ctx = ctx
        self._stall_count = 0
        self._stall_start: float | None = None
        self._last_downloaded_bytes: int = 0
        # libtorrent's instantaneous rate can legitimately report 0 between
        # piece bursts even while bytes continue moving. Persisting that raw
        # value makes the UI flicker between healthy and dead every poll. Keep
        # a tiny per-monitor EWMA based on byte deltas so visible telemetry
        # reflects recent transfer health without hiding real stalls.
        self._rate_sample_time: float | None = None
        self._rate_sample_bytes: int = 0
        self._smoothed_download_rate: float = 0.0

    async def run(self, download_id: str, handle: Any) -> None:
        """Main monitoring entry point for a single download.

        Args:
            download_id: The download ID to monitor.
            handle: The libtorrent torrent handle.

        Raises:
            asyncio.CancelledError: When the monitoring task is cancelled.
        """
        try:
            await self._wait_for_metadata(download_id, handle)
            # Persist original torrent paths before appending .downloading.
            # The organizer needs stable final paths when the torrent reaches 100%.
            await self._parse_and_persist_metadata(download_id, handle)
            await self._rename_to_downloading(download_id, handle)
            await asyncio.sleep(0.5)
            stalled = await self._progress_loop(download_id, handle)
            if stalled:
                return  # Stall handled; queue manager will restart
            await self._restore_filenames(download_id, handle)
            await self._transition_to_seeding(download_id, handle)
            if self._ctx.on_ready_callback:
                try:
                    await self._ctx.on_ready_callback(download_id)
                except Exception as e:
                    logger.exception(f'Ready callback failed for {download_id}: {e}')
            await self._monitor_seeding(download_id, handle)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Monitor error for {download_id}: {e}")
            await self._mark_failed(download_id, f"monitor error: {e}")

    async def _mark_failed(self, download_id: str, reason: str) -> None:
        """Persist monitor failures and release queue/registry ownership."""
        item: DownloadItem | None = await self._ctx.db.downloads.get_download(download_id)
        if item:
            try:
                item = self._ctx.state_machine.transition(item, DownloadStatus.FAILED, reason=reason)
            except ValueError:
                item = self._ctx.state_machine.transition(item, DownloadStatus.FAILED, reason=reason, force=True)
            await self._ctx.db.downloads.upsert_download(item)
        if self._ctx.queue:
            self._ctx.queue.deregister_active(download_id)
        if self._ctx.monitor_registry:
            self._ctx.monitor_registry.unregister(download_id)

    async def _wait_for_metadata(self, download_id: str, handle: Any) -> None:
        """Wait for libtorrent to receive metadata for the torrent."""
        while not handle.has_metadata():
            await asyncio.sleep(2)
            # Check if the download was removed from the engine
            if self._ctx.engine and not self._ctx.engine.get_handle(download_id):
                return

    async def _rename_to_downloading(self, download_id: str, handle: Any) -> None:
        """Append .downloading extension to all files in the torrent.

        This prevents media players from picking up incomplete files.
        Reports the rename via the on_files_renamed callback.
        """
        try:
            tf = handle.torrent_file()
            for i in range(tf.num_files()):
                old_path = tf.files().at(i).path
                if not old_path.endswith(".downloading"):
                    handle.rename_file(i, f"{old_path}.downloading")
            if self._ctx.on_files_renamed:
                self._ctx.on_files_renamed(download_id)
            logger.debug(f"Set temporary .downloading extension for {download_id}")
        except Exception as e:
            logger.warning(f"Failed to set temporary extension: {e}")

    async def _parse_and_persist_metadata(self, download_id: str, handle: Any) -> None:
        """Parse torrent file metadata and persist file info."""
        item: DownloadItem | None = await self._ctx.db.downloads.get_download(download_id)
        if not item or item.files:
            return
        try:
            file_infos = self._ctx.metadata_parser.parse_file_infos(
                handle,
                self._ctx.category_registry,
                item.category_id,
            )
            item.files = file_infos
            await self._ctx.db.downloads.upsert_download(item)
            # Fire stats callback so frontend receives file data immediately
            stats_files = self._ctx.metadata_parser.build_stats_files(file_infos)
            if self._ctx.on_stats_callback:
                self._ctx.on_stats_callback(download_id, {'files': stats_files})
        except Exception as e:
            logger.warning(f'Failed to parse per-file info: {e}')

    async def _progress_loop(self, download_id: str, handle: Any) -> bool:
        """Main progress monitoring loop while downloading.

        Returns:
            True if the download was stalled (caller should exit early),
            False if download completed successfully.
        """
        while True:
            await asyncio.sleep(3)
            stats = self._ctx.progress_store.extract_stats(handle)
            stats = self._stabilize_transfer_stats(stats)
            item: DownloadItem | None = await self._ctx.db.downloads.get_download(download_id)
            if item:
                item = self._ctx.progress_store.update_item(item, stats)
                await self._ctx.db.downloads.upsert_download(item)
            if self._ctx.on_stats_callback:
                self._ctx.on_stats_callback(download_id, stats)
            files_list = stats.get("files")
            if files_list and self._ctx.on_file_progress:
                self._ctx.on_file_progress(download_id, files_list)
            if self._is_content_complete(handle, stats):
                return False
            if await self._check_stall(
                download_id,
                handle,
                stats.get("download_rate", 0),
                int(stats.get("downloaded_bytes", 0) or 0),
                stats.get("progress", 0),
            ):
                return True
        return False



    def _stabilize_transfer_stats(self, stats: dict[str, Any]) -> dict[str, Any]:
        """Smooth bursty libtorrent telemetry without lying about progress.

        Public torrents often download in short piece bursts.  On some builds,
        ``torrent_status.download_rate`` drops to zero for an interval even when
        ``total_done`` advanced since the previous monitor pass.  The engine is
        not necessarily broken in that case; the UI was simply displaying a raw
        instantaneous sample as if it were a stable state.  Use byte deltas to
        compute a recent transfer rate and decay it quickly when progress stops.
        """
        now = asyncio.get_event_loop().time()
        downloaded = int(stats.get("downloaded_bytes", 0) or 0)
        raw_rate = float(stats.get("download_rate", 0.0) or 0.0)
        if self._rate_sample_time is None:
            self._rate_sample_time = now
            self._rate_sample_bytes = downloaded
            self._smoothed_download_rate = max(0.0, raw_rate)
            stats["raw_download_rate"] = raw_rate
            return stats

        elapsed = max(0.001, now - self._rate_sample_time)
        delta = max(0, downloaded - self._rate_sample_bytes)
        delta_rate = float(delta) / elapsed if delta > 0 else 0.0
        observed = max(raw_rate, delta_rate)
        if observed > 0:
            previous = self._smoothed_download_rate or observed
            self._smoothed_download_rate = (previous * 0.55) + (observed * 0.45)
        else:
            # Decay fast enough that a true stall still becomes visible and the
            # existing byte-based stall detector remains authoritative.
            self._smoothed_download_rate *= 0.50
            if self._smoothed_download_rate < 1024:
                self._smoothed_download_rate = 0.0

        stats["raw_download_rate"] = raw_rate
        stats["download_rate"] = self._smoothed_download_rate
        self._rate_sample_time = now
        self._rate_sample_bytes = downloaded
        return stats

    @staticmethod
    def _is_content_complete(handle: Any, stats: dict[str, Any]) -> bool:
        """Return True when the useful torrent payload is complete.

        Libtorrent's ``is_seed()`` is normally the canonical signal, but it can
        lag or remain false around selective files, renamed payloads, and resume
        races.  Treat byte/progress completion as an equivalent signal so the
        monitor restores filenames, exposes the payload to the library, and
        releases the queue slot instead of leaving a 100% item stranded as
        downloading/queued.
        """
        try:
            if handle.is_seed():
                return True
        except Exception:
            pass
        progress = float(stats.get("progress", 0.0) or 0.0)
        total_size = int(stats.get("total_size", 0) or 0)
        downloaded = int(stats.get("downloaded_bytes", 0) or 0)
        if progress >= 0.999:
            return True
        return total_size > 0 and downloaded >= max(total_size - 1024 * 1024, 1)

    async def _restore_filenames(self, download_id: str, handle: Any) -> None:
        """Restore original file names (remove .downloading extension).

        Reports the restoration via the on_files_restored callback.
        """
        try:
            tf = handle.torrent_file()
            for i in range(tf.num_files()):
                current_path = tf.files().at(i).path
                restored_path = current_path[:-12] if current_path.endswith(".downloading") else current_path
                handle.rename_file(i, restored_path)
            if self._ctx.on_files_restored:
                self._ctx.on_files_restored(download_id)
            await asyncio.sleep(1)
            logger.debug(f"Restored original file extensions for {download_id}")
        except Exception as e:
            logger.warning(f"Failed to restore original extensions: {e}")

    async def _transition_to_seeding(self, download_id: str, handle: Any) -> None:
        """Update item status to SEEDING and set completion fields."""
        item: DownloadItem | None = await self._ctx.db.downloads.get_download(download_id)
        if not item:
            return
        try:
            item = self._ctx.state_machine.transition(item, DownloadStatus.SEEDING, reason="content downloaded")
        except ValueError as exc:
            logger.warning(f"Repairing completion transition for {download_id}: {exc}")
            item = self._ctx.state_machine.transition(item, DownloadStatus.SEEDING, reason="content downloaded; repaired stale active state", force=True)
        item.progress = 1.0
        item.completed_at = datetime.now(timezone.utc)
        if not item.save_path:
            item.save_path = str(Path(handle.save_path()).resolve())
        item.file_path = self._find_largest_file(handle)
        item = self._ctx.progress_store.mark_all_complete(item)
        await self._ctx.db.downloads.upsert_download(item)
        # A seeding torrent is complete from the user's point of view and should
        # no longer consume a download concurrency slot.
        if self._ctx.queue:
            self._ctx.queue.deregister_active(download_id)
        if item.sharing_enabled and self._ctx.engine and hasattr(self._ctx.engine, "mark_handle_mode"):
            await self._ctx.engine.mark_handle_mode(download_id, "library_seed")

    def _find_largest_file(self, handle: Any) -> str | None:
        """Find the largest file path in the torrent."""
        try:
            tf = handle.torrent_file()
            if tf:
                largest = max(tf.files(), key=lambda f: f.size)
                return str(Path(handle.save_path()) / largest.path)
        except Exception:
            pass
        return None

    async def _monitor_seeding(self, download_id: str, handle: Any) -> None:
        """Monitor seeding until the policy says to stop."""
        try:
            while True:
                await asyncio.sleep(30)
                s = handle.status()
                total = s.all_time_download if s.all_time_download > 0 else s.total_done
                ratio = s.all_time_upload / total if total > 0 else 0.0
                item: DownloadItem | None = await self._ctx.db.downloads.get_download(download_id)
                if not item:
                    break
                item.upload_rate = s.upload_rate
                item.uploaded_bytes = int(getattr(s, "all_time_upload", 0) or 0)
                item.seed_ratio = float(ratio or 0.0)
                item.num_peers = int(getattr(s, "num_peers", 0) or 0)
                item.num_seeds = int(getattr(s, "num_seeds", 0) or 0)
                await self._ctx.db.downloads.upsert_download(item)
                policy = self._ctx.seed_policy
                if policy and policy.should_stop(item, s.upload_rate, ratio):
                    try:
                        item = self._ctx.state_machine.transition(item, DownloadStatus.COMPLETE, reason="seeding policy satisfied")
                    except ValueError as exc:
                        logger.warning(f"Repairing unexpected seeding-complete transition for {download_id}: {exc}")
                        item = self._ctx.state_machine.transition(
                            item,
                            DownloadStatus.COMPLETE,
                            reason="seeding policy satisfied; repaired stale state",
                            force=True,
                        )
                    item.upload_rate = 0.0
                    await self._ctx.db.downloads.upsert_download(item)
                    if self._ctx.on_complete_callback:
                        try:
                            await self._ctx.on_complete_callback(download_id)
                        except Exception as e:
                            logger.exception(f'Completion callback failed for {download_id}: {e}')
                    # Once the policy says seeding is complete, remove the handle
                    # from libtorrent so it cannot keep uploading invisibly after
                    # the database row leaves the Sharing/Downloads active views.
                    if self._ctx.engine and hasattr(self._ctx.engine, "remove_torrent"):
                        try:
                            await self._ctx.engine.remove_torrent(download_id)
                        except Exception as e:
                            logger.warning(f"Failed to remove completed torrent {download_id}: {e}")
                    if self._ctx.monitor_registry:
                        self._ctx.monitor_registry.unregister(download_id)
                    break
        except Exception:
            pass

    async def _check_stall(self, download_id: str, handle: Any, rate: float, downloaded_bytes: int, progress: float) -> bool:
        """Check for true no-progress stalls using byte movement, not just rate.

        A torrent can briefly report a near-zero instantaneous rate while still
        being healthy.  Restart only after downloaded bytes have stopped moving
        for the full stall window.
        """
        if progress >= 0.995:
            self._stall_start = None
            self._last_downloaded_bytes = max(self._last_downloaded_bytes, downloaded_bytes)
            return False

        now = asyncio.get_event_loop().time()
        if downloaded_bytes > self._last_downloaded_bytes + STALL_THRESHOLD_BYTES:
            self._last_downloaded_bytes = downloaded_bytes
            self._stall_start = None
            return False

        if rate < STALL_THRESHOLD_BYTES:
            if self._stall_start is None:
                self._stall_start = now
            elif (now - self._stall_start) >= STALL_TIMEOUT_SECONDS:
                return await self._handle_stall(download_id, handle)
        else:
            self._stall_start = None
        return False

    async def _handle_stall(self, download_id: str, handle: Any) -> bool:
        """Park a stalled download and release its active slot.

        Older builds repeatedly removed/requeued or eventually failed a torrent
        inside the per-download monitor.  That clogged the queue and punished
        rare torrents.  The generic download-health supervisor now owns
        periodic testing and alternatives; the monitor only detects the stall,
        parks the torrent, and exits.
        """
        item: DownloadItem | None = await self._ctx.db.downloads.get_download(download_id)
        if not item:
            return True
        logger.info(
            f"Detected stalled download {download_id}; parking for health supervisor "
            f"(peers={getattr(handle.status(), 'num_peers', 0)}, seeds={getattr(handle.status(), 'num_seeds', 0)})"
        )
        try:
            if handle:
                await asyncio.wait_for(asyncio.to_thread(handle.pause), timeout=3.0)
        except asyncio.TimeoutError:
            logger.warning(f"Timeout pausing stalled handle {download_id}")
        except Exception:
            pass
        item = self._ctx.state_machine.transition(item, DownloadStatus.STALLED, reason="no byte progress")
        await self._ctx.db.downloads.upsert_download(item)
        if self._ctx.queue:
            self._ctx.queue.deregister_active(download_id)
        if self._ctx.monitor_registry:
            self._ctx.monitor_registry.unregister(download_id)
        return True
