"""
Media scheduler for LJS.

Orchestrates category-driven periodic checks, discovery, downloads, organization, and notifications.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from loguru import logger
from typing import Any, Optional, Callable, TYPE_CHECKING
from src.utils.scheduler import IntervalScheduler

from src.core.categories.identity import canonical_item_key, clean_category_item_name, clean_display_title
from src.core.models import (
    Settings,
    CategoryItem,
    GenericMediaItem,
    ItemList,
    ShowLifecycleState,
    DownloadStatus,
    DownloadPriority,
    DownloadReason,
    QualityProfile,
    SearchResult,
    SuggestedActionRecord,
    EpisodeRecord,
    UpgradeRecord,
    LibraryScanResult,
    ScannedLibraryItem,
)
from src.core.config import SettingsManager
from src.core.database import Database
from src.core.downloader import DownloadManager
from src.core.librarian import Librarian
from src.core.content_cleanup import ContentCleanup
from src.core.notifications import NotificationService
from src.core.smart_quality import SmartQualityInferrer
from src.search.aggregator import SearchAggregator
from src.utils.blacklist import BlacklistManager
from src.utils.library_scanner import LibraryScanner
from src.utils.quality import QualityAnalyzer
from src.core.release_groups import ReleaseGroupTracker
from src.utils.torrent_knowledge import TorrentKnowledge
from src.core.task_supervisor import TaskSupervisor
from src.core.torrent_racer import TorrentRacer
from src.core.suggestion_compiler import SuggestionCompiler
from src.core.category_lifecycle import CategoryLifecycleEngine
from src.core.download_health import DownloadHealthSupervisor

from src.core.search_pipeline import SearchPipeline
from src.core.scheduler_services import SchedulerCatalogService, SchedulerServiceContext, SchedulerTorrentSearchService

if TYPE_CHECKING:
    from src.ai.assistant import AIAssistant

SECONDS_PER_DAY = 86400
_LIBRARY_SIGNATURE_MAX_DIRS = 5000

_REASON_PRIORITY: dict[str, DownloadPriority] = {
    "new_episode": DownloadPriority.HIGH,
    "manual": DownloadPriority.HIGH,
    "bundle": DownloadPriority.NORMAL,
    "upgrade_quality": DownloadPriority.LOW,
    "retry": DownloadPriority.LOW,
    "race": DownloadPriority.LOW,
}

_MANUAL_REASONS = {"manual", "user_requested"}


def _priority_for_reason(reason: str, _item_airing: bool = False) -> DownloadPriority:
    """Return the queue priority for a given download reason."""
    if reason in _MANUAL_REASONS:
        return DownloadPriority.HIGH
    return _REASON_PRIORITY.get(reason, DownloadPriority.NORMAL)


@dataclass
class SchedulerDependencies:
    """All dependencies for MediaScheduler, injected at construction.

    All sub-coordinators (SearchPipeline, category schedule hooks, UpgradeDetector)
    are constructed internally from the provided services.
    """

    settings_manager: SettingsManager
    db: Database
    downloader: DownloadManager
    aggregator: SearchAggregator
    librarian: Librarian
    notifications: NotificationService
    scanner: LibraryScanner
    quality_inferrer: SmartQualityInferrer
    content_cleanup: Optional[ContentCleanup] = None
    recommender: Optional[object] = None
    prompt_scheduler: Optional[object] = None
    tvmaze: Optional[object] = None
    assistant: Optional[AIAssistant] = None
    blacklist: Optional[BlacklistManager] = None
    supervisor: Optional[TaskSupervisor] = None
    release_group_tracker: Optional[ReleaseGroupTracker] = None
    torrent_racer: Optional[TorrentRacer] = None
    metadata_enricher: Optional[object] = None
    artwork_manager: Optional[object] = None
    category_registry: Optional[object] = None
    torrent_selection: Optional[object] = None
    suggestion_compiler: Optional[SuggestionCompiler] = None
    lifecycle_engine: Optional[CategoryLifecycleEngine] = None
    event_bus: Optional[object] = None


class MediaScheduler:
    """Orchestrates periodic background jobs for library management."""

    def __init__(self, dependencies: SchedulerDependencies) -> None:
        """Initialize scheduler with all injected dependencies.

        Args:
            dependencies: All services and sub-coordinators for the scheduler.
        """
        self._settings_manager = dependencies.settings_manager
        self._db = dependencies.db
        self._downloader = dependencies.downloader
        self._aggregator = dependencies.aggregator
        self._librarian = dependencies.librarian
        self._content_cleanup = dependencies.content_cleanup
        self._notifications = dependencies.notifications
        self._scanner = dependencies.scanner
        self._quality_inferrer = dependencies.quality_inferrer
        self._recommender = dependencies.recommender
        self._prompt_scheduler = dependencies.prompt_scheduler
        self._assistant = dependencies.assistant
        self._blacklist = dependencies.blacklist
        self._supervisor = dependencies.supervisor
        self._release_group_tracker = dependencies.release_group_tracker
        self._torrent_racer = dependencies.torrent_racer
        self._tvmaze = dependencies.tvmaze
        self._metadata_enricher = dependencies.metadata_enricher
        self._artwork_manager = dependencies.artwork_manager
        self._categories = dependencies.category_registry
        self._suggestion_compiler = dependencies.suggestion_compiler
        # Keep the scheduler category-neutral: it asks the lifecycle engine
        # whether an item is due instead of embedding TV/movie cadence rules
        # or blindly rebuilding suggestions at every boot.
        self._lifecycle = dependencies.lifecycle_engine or CategoryLifecycleEngine(
            db=dependencies.db,
            category_registry=dependencies.category_registry,
            settings_manager=dependencies.settings_manager,
        )
        self._event_bus = dependencies.event_bus
        
        # Sub-coordinators
        self._pipeline = SearchPipeline(
            dependencies.aggregator, dependencies.downloader,
            dependencies.db, dependencies.librarian,
            dependencies.category_registry,
            dependencies.torrent_selection,
            settings_manager=dependencies.settings_manager,
        )
        self._pipeline.set_scheduler(self)
        self._download_health = DownloadHealthSupervisor(
            settings_manager=dependencies.settings_manager,
            db=dependencies.db,
            downloader=dependencies.downloader,
            pipeline=self._pipeline,
            categories=dependencies.category_registry,
            notifications=dependencies.notifications,
            event_bus=dependencies.event_bus,
        )
        
        self._scheduler = IntervalScheduler()
        self._last_scan_result = None
        self._library_scan_status: dict[str, object] = {
            "state": "idle",
            "message": "Library scanner idle",
            "phase": "idle",
            "scan_in_progress": False,
            "reason": None,
        }
        self._last_library_fs_signature: str | None = None
        self._library_scan_lock = asyncio.Lock()
        self._library_scan_task: asyncio.Task | None = None
        self._metadata_refresh_task: asyncio.Task | None = None
        self._library_item_reconcile_lock = asyncio.Lock()
        self._managed_library_mutation_count = 0
        self._check_semaphore = asyncio.Semaphore(2)

    def set_event_bus(self, event_bus: object | None) -> None:
        """Attach the web event bus once the FastAPI app has been composed."""
        self._event_bus = event_bus
        if hasattr(self, "_download_health") and self._download_health:
            self._download_health.set_event_bus(event_bus)

    def _emit_status(self, message: str, *, phase: str = "info", item: str | None = None) -> None:
        """Broadcast a lightweight background-processing status to the UI."""
        if not self._event_bus:
            return
        try:
            self._event_bus.emit_system("background_status", {"message": message, "phase": phase, "item": item})
        except Exception:
            pass

    def _set_library_scan_status(self, *, state: str, message: str, phase: str, reason: str | None = None) -> None:
        """Store and broadcast the user-visible library scan state."""
        self._library_scan_status = {
            "state": state,
            "message": message,
            "phase": phase,
            "scan_in_progress": state in {"queued", "running"},
            "reason": reason,
        }
        self._emit_status(message, phase=phase)

    def get_library_scan_status(self) -> dict[str, object]:
        """Return current scanner state for initial UI hydration."""
        running = self._library_scan_lock.locked() or (self._library_scan_task is not None and not self._library_scan_task.done())
        status = dict(getattr(self, "_library_scan_status", {}) or {})
        if running and not status.get("scan_in_progress"):
            status.update({"state": "running", "phase": "running", "scan_in_progress": True, "message": "Library scan running"})
        return status

    @property
    def settings_manager(self) -> SettingsManager:
        """Get the SettingsManager instance."""
        return self._settings_manager

    @property
    def database(self) -> Database:
        """Return the database repository gateway used by scheduler services.

        Queueing and tool-layer collaborators should use this read-only public
        accessor rather than reaching into scheduler internals such as ``_db``.
        """
        return self._db

    async def initialize(self) -> None:
        """Register all jobs and start the scheduler."""
        self._scheduler.add_job(
            self._update_tracked_items_job, interval_seconds=3600,
            id="tracked_items_update",
            initial_delay_seconds=3600,
        )
        
        if self._categories:
            self._scheduler.add_job(
                self.sync_air_schedules, interval_seconds=SECONDS_PER_DAY,
                id="category_schedule_sync",
                initial_delay_seconds=6 * 3600,
            )

        if self._recommender:
            self._scheduler.add_job(
                self._send_recommendations, interval_seconds=7 * SECONDS_PER_DAY,
                id="recommendations",
                initial_delay_seconds=7 * SECONDS_PER_DAY,
            )

        if self._content_cleanup:
            self._scheduler.add_job(
                self._auto_cleanup_watched, interval_seconds=SECONDS_PER_DAY,
                id="auto_cleanup",
                initial_delay_seconds=SECONDS_PER_DAY,
            )

        self._scheduler.add_job(
            self.scan_library, interval_seconds=3600, kwargs={"force": True, "refresh_metadata": False},
            id="library_scan",
            initial_delay_seconds=3600,
        )
        self._scheduler.add_job(
            self._repair_stale_media_metadata_job, interval_seconds=6 * 3600,
            id="media_metadata_repair",
            initial_delay_seconds=90,
        )
        self._scheduler.add_job(
            self._watch_library_filesystem_job, interval_seconds=120,
            id="library_filesystem_watch",
            initial_delay_seconds=120,
        )
        if self._suggestion_compiler:
            self._scheduler.add_job(
                self.compile_suggestions, interval_seconds=3600,
                id="suggestion_compilation",
                initial_delay_seconds=3600,
            )
        if getattr(self._db, "release_watches", None):
            self._scheduler.add_job(
                self.process_release_watches, interval_seconds=2 * 3600,
                id="release_watch_retry",
                initial_delay_seconds=120,
            )
        
        if self._prompt_scheduler:
            self._scheduler.add_job(
                self._run_scheduled_prompts, interval_seconds=60,
                id="prompt_scheduler",
                initial_delay_seconds=60,
            )

        settings = self._settings_manager.settings
        self._scheduler.add_job(
            self._check_stalled_downloads_job, interval_seconds=settings.stall_check_interval_minutes * 60,
            id="download_health_supervisor",
            initial_delay_seconds=60,
        )

        self._scheduler.start()
        logger.info("Media scheduler started.")

    async def scan_library(self, force: bool = False, *, refresh_metadata: bool = True) -> None:
        """Scan category library directories and reconcile database state with disk.

        The expensive filesystem crawl runs off the FastAPI event loop.  The
        scan itself updates repository state and emits a completion event;
        artwork/provider refresh is then launched as a separate best-effort
        background job so the UI does not have to wait for slow TMDB/artwork
        calls before becoming usable again.
        """
        async with self._library_scan_lock:
            settings = self._settings_manager.settings

            if not settings.setup_complete and not force:
                logger.debug("Skipping automated library scan — setup not complete")
                return

            if not force and settings.last_library_scan_at:
                try:
                    last = datetime.fromisoformat(settings.last_library_scan_at)
                    if (datetime.now(timezone.utc) - last).total_seconds() < 3600:
                        logger.info("Library scan skipped — last scan was < 1 hour ago")
                        await self.cleanup_category_boundary_leaks()
                        if refresh_metadata:
                            # A recent scan means the filesystem ledger is fresh, but the UI
                            # may still be missing artwork after a restart.  Start a separate
                            # repository-backed artwork repair pass instead of forcing another
                            # disk crawl or blocking the browser response.
                            self._start_metadata_refresh(None)
                        return
                except (ValueError, TypeError):
                    pass

            logger.info("Starting library scan...")
            self._set_library_scan_status(state="running", message="Scanning library", phase="running", reason="scheduled" if not force else "requested")
            result = await self._scanner.full_scan(settings)
            self._last_scan_result = result

            settings.last_library_scan_at = datetime.now(timezone.utc).isoformat()
            await asyncio.to_thread(self._settings_manager.save, settings)

            if settings.auto_discover:
                await self._discover_new_items(result)

            await self._remove_cross_category_scan_leaks(result)
            await self._ensure_scanned_items_exist(result)
            await self._mark_scanned_items_present(result)
            await self._sync_scanned_units(result)
            await self._reconcile_removed_library_entries(result)
            await self._invalidate_scanned_lifecycle_state(result)
            self._last_library_fs_signature = await self._library_roots_signature_async(settings)
            await self._downloader.apply_speed_limits(settings.default_quality)
            self._set_library_scan_status(state="complete", message="Library scan complete", phase="done")
            if self._event_bus:
                try:
                    self._event_bus.emit_system("library_scan_completed", {
                        "items_found": len(result.items),
                        "total_files": result.total_files,
                        "metadata_refresh_pending": bool(refresh_metadata),
                    })
                except Exception:
                    pass

        if refresh_metadata:
            self._start_metadata_refresh(result)

    def request_library_scan(self, force: bool = True, *, refresh_metadata: bool = True, reason: str = "manual") -> dict:
        """Start a library scan without blocking the API caller.

        Manual UI scans used to await the complete filesystem crawl and artwork
        refresh inside the request handler.  On large libraries that made the
        frontend appear frozen.  This method coalesces duplicate requests and
        launches the scan through the supervisor so HTTP/WebSocket traffic keeps
        flowing.
        """
        running = self._library_scan_lock.locked() or (self._library_scan_task is not None and not self._library_scan_task.done())
        if running:
            return {
                "status": "already_running",
                "scan_in_progress": True,
                "message": "Library scan is already running in the background.",
            }

        coro = self.scan_library(force=force, refresh_metadata=refresh_metadata)
        if self._supervisor:
            task = self._supervisor.spawn_one_shot(f"library_scan_{reason}", coro)
        else:
            task = asyncio.create_task(coro)
        self._library_scan_task = task

        def _clear_scan_task(done_task: asyncio.Task) -> None:
            if self._library_scan_task is done_task:
                self._library_scan_task = None

        task.add_done_callback(_clear_scan_task)
        self._set_library_scan_status(state="queued", message="Library scan queued", phase="running", reason=reason)
        return {
            "status": "queued",
            "scan_in_progress": True,
            "message": "Library scan started in the background.",
        }

    def begin_managed_library_mutation(self, *, category_id: str = "", item_id: str = "") -> None:
        """Mark an app-owned library write so the watcher does not full-scan mid-copy."""
        self._managed_library_mutation_count += 1
        logger.debug(
            f"Managed library mutation started for {category_id or 'unknown'}/{item_id or 'unknown'}; "
            "filesystem watcher will wait for item reconciliation."
        )

    def end_managed_library_mutation(self, *, category_id: str = "", item_id: str = "") -> None:
        """Clear one app-owned library write marker."""
        self._managed_library_mutation_count = max(0, self._managed_library_mutation_count - 1)
        logger.debug(f"Managed library mutation finished for {category_id or 'unknown'}/{item_id or 'unknown'}.")

    async def reconcile_library_item_from_path(
        self,
        *,
        category_id: str,
        item_id: str,
        changed_path: str,
        reason: str = "item_changed",
    ) -> dict:
        """Reconcile one category item after an app-owned local file change.

        This is the targeted counterpart to ``scan_library``.  It is used after
        completed-download imports and can also back future item refresh buttons.
        It must not call full-scan-only cleanup such as removed-library-entry
        reconciliation because the result intentionally contains only one item.
        """
        if not category_id or not item_id:
            return {"status": "skipped", "reason": "missing_identity"}

        async with self._library_item_reconcile_lock:
            settings = self._settings_manager.settings
            self._emit_status("Reconciling changed library item", phase="running", item=item_id)
            result = await self._scanner.item_scan(
                settings,
                category_id=category_id,
                item_id=item_id,
                changed_path=changed_path,
            )
            if not result.items:
                # The changed path may have been deleted or may belong to an
                # unsupported/custom category. Update the signature so the next
                # watcher pass does not repeatedly launch a full scan for the
                # already-observed app-owned mutation. Manual scans remain
                # authoritative for deletion reconciliation.
                self._last_library_fs_signature = await self._library_roots_signature_async(settings)
                logger.warning(
                    f"Item-scoped library reconciliation found no scan rows for "
                    f"{category_id}/{item_id} at {changed_path}"
                )
                return {"status": "not_found", "items": 0}

            if settings.auto_discover:
                await self._discover_new_items(result)
            await self._ensure_scanned_items_exist(result)
            await self._mark_scanned_items_present(result)
            synced = await self._sync_scanned_units(result)
            invalidated = await self._invalidate_scanned_lifecycle_state(result)

            suggestion_count = 0
            if self._suggestion_compiler:
                try:
                    configured = self._settings_items_by_identity()
                    clean_name = clean_category_item_name(result.items[0].name, category_id)
                    identity = (category_id, canonical_item_key(clean_name))
                    item = configured.get(identity)
                    if item is None:
                        category = self._categories.get(category_id) if self._categories else None
                        item = category.create_item(clean_name) if category else GenericMediaItem(key=clean_name, category_id=category_id)
                    suggestion_count = await self._suggestion_compiler.compile_for_item(item, force=True)
                except Exception as exc:
                    logger.debug(f"Item suggestion refresh skipped for {category_id}/{item_id}: {exc}")

            self._last_library_fs_signature = await self._library_roots_signature_async(settings)
            if self._event_bus:
                try:
                    self._event_bus.emit_system(
                        "library_item_reconciled",
                        {
                            "category_id": category_id,
                            "item_id": item_id,
                            "changed_path": changed_path,
                            "reason": reason,
                            "synced_units": synced,
                            "invalidated": invalidated,
                            "suggestions": suggestion_count,
                        },
                    )
                except Exception:
                    pass
            self._emit_status("Library item reconciled", phase="done", item=item_id)
            return {
                "status": "reconciled",
                "items": len(result.items),
                "synced_units": synced,
                "invalidated": invalidated,
                "suggestions": suggestion_count,
            }

    async def cleanup_category_boundary_leaks(self) -> int:
        """Remove repository items whose stored files live under another category root.

        This is intentionally provider-free.  It repairs databases produced by
        older builds where a file under one category root could be attached
        to an item owned by another category.  Category roots are the source of truth; metadata providers
        may enrich an item inside its category, but never move files across
        categories.
        """
        if not self._db or not getattr(self._db, "media", None) or not self._categories:
            return 0
        settings = self._settings_manager.settings
        roots = self._category_roots(settings)
        if len(roots) < 2:
            return 0

        removed = 0
        for item in await self._db.media.list_category_items():
            category_id = str(item.get("category_id") or item.get("item_type") or "")
            item_id = str(item.get("item_id") or item.get("key") or "")
            if not category_id or not item_id:
                continue
            actual_owner = await self._category_owner_from_item_units(category_id, item_id, roots)
            if not actual_owner or actual_owner == category_id:
                continue
            await self._db.media.delete_category_item(category_id, item_id)
            self._remove_settings_item_by_identity(category_id, item_id, discovered_only=False)
            removed += 1
            logger.warning(
                f"Removed category-boundary leak {category_id}:{item_id}; "
                f"stored files are under {actual_owner} root"
            )
        if removed:
            self._emit_status(f"Library boundary cleanup removed {removed} stale item(s)", phase="done")
        return removed

    def _category_roots(self, settings: Settings) -> dict[str, Path]:
        """Return configured category roots as resolved paths."""
        roots: dict[str, Path] = {}
        if not self._categories:
            return roots
        for category in self._categories.list_all():
            try:
                roots[category.category_id] = Path(category.get_root_path(settings)).expanduser().resolve(strict=False)
            except Exception:
                continue
        return roots

    async def _category_owner_from_item_units(
        self,
        category_id: str,
        item_id: str,
        roots: dict[str, Path],
    ) -> str | None:
        """Infer the category root that actually owns an item's stored files."""
        try:
            units = await self._db.media.list_category_units(category_id, item_id)
        except Exception:
            return None
        own_root = roots.get(category_id)
        for unit in units:
            raw_path = str(unit.get("file_path") or unit.get("path") or "")
            if not raw_path:
                continue
            try:
                file_path = Path(raw_path).expanduser().resolve(strict=False)
            except Exception:
                continue
            if own_root and file_path.is_relative_to(own_root):
                continue
            for other_category, root in roots.items():
                if other_category == category_id:
                    continue
                try:
                    if file_path.is_relative_to(root):
                        return other_category
                except Exception:
                    continue
        return None

    def _start_metadata_refresh(self, result: LibraryScanResult | None) -> None:
        """Launch missing-artwork/provider refresh outside the scan lock.

        ``result`` is present after a real filesystem scan.  When startup skips
        the scan because the previous one is still fresh, ``None`` asks the
        refresh job to derive targets from the repository/settings instead.
        That keeps first paint fast while still allowing missing cover art to
        materialize shortly after launch.
        """
        if self._metadata_refresh_task is not None and not self._metadata_refresh_task.done():
            logger.debug("Metadata/artwork refresh already running; skipping duplicate launch.")
            return

        async def _job() -> None:
            try:
                refreshed = await self._refresh_missing_metadata_and_artwork(result)
                if self._event_bus:
                    try:
                        self._event_bus.emit_system("library_metadata_refresh_completed", {"refreshed": refreshed})
                    except Exception:
                        pass
            except Exception as exc:
                logger.warning(f"Metadata/artwork refresh failed: {exc}")
                self._emit_status("Artwork refresh failed", phase="error")
                if self._event_bus:
                    try:
                        self._event_bus.emit_system("library_metadata_refresh_completed", {"refreshed": 0, "error": str(exc)})
                    except Exception:
                        pass

        if self._supervisor:
            task = self._supervisor.spawn_one_shot("library_metadata_refresh", _job())
        else:
            task = asyncio.create_task(_job())
        self._metadata_refresh_task = task

        def _clear_metadata_task(done_task: asyncio.Task) -> None:
            if self._metadata_refresh_task is done_task:
                self._metadata_refresh_task = None

        task.add_done_callback(_clear_metadata_task)

    async def _repair_stale_media_metadata_job(self) -> None:
        """Repair old file units that lack real stream metadata.

        Round 81 restored ffprobe-backed audio/subtitle/resolution extraction,
        but already-persisted canonical units are not rewritten until a scan
        revisits the files.  This job detects stale downloaded file units and
        triggers one normal scan.  Probing remains serialized/cached in
        ``media_probe.py`` so this does not launch a concurrent disk sweep.
        """
        settings = self._settings_manager.settings
        if not settings.setup_complete:
            return
        if self._library_scan_lock.locked():
            return
        # Do not force a full-library crawl on every boot.  Older rows without
        # ffprobe metadata are useful to repair, but that repair is opportunistic
        # and should never make startup feel like LJS is re-reading the whole
        # collection.  Run it at most weekly unless the user manually scans.
        now = datetime.now(timezone.utc)
        last_repair_text = str(getattr(settings, "last_media_metadata_repair_at", "") or "")
        if last_repair_text:
            try:
                last_repair = datetime.fromisoformat(last_repair_text)
                if (now - last_repair).total_seconds() < 7 * SECONDS_PER_DAY:
                    logger.debug("Stale media metadata repair skipped — last repair scan was < 7 days ago")
                    return
            except (TypeError, ValueError):
                pass
        if settings.last_library_scan_at:
            try:
                last_scan = datetime.fromisoformat(settings.last_library_scan_at)
                if (now - last_scan).total_seconds() < 24 * 3600:
                    logger.info("Stale media metadata repair deferred — recent library scan exists; no startup full scan needed.")
                    return
            except (TypeError, ValueError):
                pass
        try:
            stale = await self._has_stale_media_file_metadata(limit_items=40)
        except Exception as exc:
            logger.debug(f"Stale media metadata repair check skipped: {exc}")
            return
        if not stale:
            return
        logger.info(
            "Detected downloaded media files with missing stream metadata; "
            "running serialized metadata repair scan (weekly repair throttle)."
        )
        settings.last_media_metadata_repair_at = now.isoformat()
        await asyncio.to_thread(self._settings_manager.save, settings)
        self._emit_status("Repairing media language/resolution metadata", phase="running")
        await self.scan_library(force=True, refresh_metadata=False)

    async def _has_stale_media_file_metadata(self, *, limit_items: int = 40) -> bool:
        """Return true if persisted downloaded file units need stream re-probing."""
        if not self._categories:
            return False
        checked = 0
        for item in self._settings_manager.settings.tracked_items:
            category_id = getattr(item, "category_id", getattr(item, "item_type", "media")) or "media"
            item_id = getattr(item, "key", "") or ""
            if not item_id or category_id not in self._categories.list_ids():
                continue
            try:
                units = await self._db.media.list_category_units(category_id, item_id, status="downloaded")
            except Exception:
                continue
            for unit in units:
                checked += 1
                if checked > limit_items:
                    return False
                role = str(unit.get("role") or "")
                unit_type = str(unit.get("unit_type") or "")
                file_path = str(unit.get("file_path") or unit.get("path") or "")
                if not file_path or unit_type not in {"file", "media_file"}:
                    continue
                probe = unit.get("media_probe") if isinstance(unit.get("media_probe"), dict) else {}
                has_audio = bool(unit.get("audio_languages") or probe.get("audio_languages"))
                has_probe_dimensions = bool(
                    probe.get("height") or probe.get("width") or probe.get("video_height") or probe.get("video_width")
                )
                resolution_source = str(unit.get("resolution_source") or probe.get("resolution_source") or "")
                has_stream_resolution = has_probe_dimensions and resolution_source == "ffprobe_video_stream"
                # Local video payloads should carry stream metadata and a
                # resolution sourced from ffprobe video dimensions. A filename
                # resolution is only a fallback display hint, so older repaired
                # rows with filename-only resolution must be rebuilt. File size
                # is not accepted as resolution evidence; it only estimates
                # bitrate when ffprobe bit_rate is unavailable.
                if role in {"episode_payload", "movie_payload", ""} and (not probe or not has_audio or not has_stream_resolution):
                    return True
        return False

    async def _watch_library_filesystem_job(self) -> None:
        """Trigger a reconciliation scan when library folders change on disk."""
        settings = self._settings_manager.settings
        if not settings.setup_complete:
            return
        signature = await self._library_roots_signature_async(settings)
        if not signature:
            return
        if self._last_library_fs_signature is None:
            self._last_library_fs_signature = signature
            return
        if signature == self._last_library_fs_signature:
            return
        if self._managed_library_mutation_count > 0:
            logger.debug(
                "Library filesystem changed during a managed import; deferring full scan until item reconciliation finishes."
            )
            return
        logger.info("Library filesystem changed; forcing reconciliation scan.")
        self._emit_status("Library folders changed; rescanning", phase="running")
        self.request_library_scan(force=True, refresh_metadata=False, reason="filesystem_watch")

    async def _library_roots_signature_async(self, settings: Settings) -> str:
        """Return the library root signature without blocking the main event loop."""
        return await asyncio.to_thread(self._library_roots_signature, settings)

    def _library_roots_signature(self, settings: Settings) -> str:
        """Return a bounded signature of category library directory structure.

        This is a polling fallback, not an exhaustive scanner.  It watches root
        and directory mtimes so common OS-level folder/file mutations trigger a
        real scan, while avoiding a full sorted rglob on the FastAPI event loop
        every couple of minutes.
        """
        if not self._categories:
            return ""
        digest = hashlib.sha1()
        for category in self._categories.list_all():
            try:
                root = Path(category.get_root_path(settings)).expanduser()
            except Exception:
                continue
            digest.update(str(category.category_id).encode("utf-8", errors="ignore"))
            digest.update(str(root).encode("utf-8", errors="ignore"))
            if not root.exists():
                digest.update(b"missing")
                continue
            seen = 0
            try:
                for current_root, dirnames, _filenames in os.walk(root):
                    dirnames[:] = sorted(name for name in dirnames if not name.startswith("."))
                    current = Path(current_root)
                    try:
                        rel = current.relative_to(root).as_posix()
                    except ValueError:
                        rel = str(current)
                    try:
                        stat = current.stat()
                        digest.update(f"{rel}:{stat.st_mtime_ns};".encode("utf-8", errors="ignore"))
                    except OSError:
                        digest.update(f"{rel}:unreadable;".encode("utf-8", errors="ignore"))
                    seen += 1
                    if seen >= _LIBRARY_SIGNATURE_MAX_DIRS:
                        digest.update(b"truncated")
                        dirnames[:] = []
                        break
            except OSError:
                digest.update(b"unreadable")
        return digest.hexdigest()


    async def _metadata_refresh_targets(self, result: LibraryScanResult | None) -> set[tuple[str, str]]:
        """Return category/item targets for a missing-artwork refresh pass.

        Post-scan refreshes should use the exact scan result.  Startup refreshes
        often skip the disk crawl for speed, so they fall back to repository and
        settings rows.  Keeping this target selection here prevents web routers
        from doing provider work just to render library cards.
        """
        targets: set[tuple[str, str]] = set()
        if result is not None:
            for item in result.items or []:
                item_id = clean_category_item_name(item.name, item.category_id)
                if item.category_id and item_id:
                    targets.add((item.category_id, item_id))
            return targets

        if self._db and getattr(self._db, "media", None):
            try:
                for row in await self._db.media.list_category_items():
                    category_id = str(row.get("category_id") or row.get("item_type") or "").strip()
                    item_id = str(row.get("item_id") or row.get("key") or "").strip()
                    if category_id and item_id:
                        targets.add((category_id, item_id))
            except Exception as exc:
                logger.debug(f"Repository artwork target enumeration skipped: {exc}")

        settings = self._settings_manager.settings
        for item in getattr(settings, "tracked_items", []) or []:
            category_id = str(getattr(item, "category_id", None) or getattr(item, "item_type", "") or "").strip()
            item_id = str(getattr(item, "key", "") or "").strip()
            if category_id and item_id:
                targets.add((category_id, item_id))
        return targets


    async def _refresh_missing_metadata_and_artwork(self, result: LibraryScanResult | None) -> int:
        """Refresh metadata/artwork for library items that do not yet have posters.

        The scheduler only orchestrates.  Category subclasses own the actual
        metadata/artwork workflows via ``refresh_metadata`` / ``resolve_metadata``.
        A real scan result is preferred, but startup can pass ``None`` to repair
        artwork from already-known repository/settings items after the API is up.
        """
        if not self._categories or not self._db or not getattr(self._db, "media", None):
            return 0
        settings = self._settings_manager.settings
        refreshed = 0
        from src.core.categories.base import CategoryWorkflowContext
        context = CategoryWorkflowContext(
            db=self._db,
            pipeline=self._pipeline,
            aggregator=self._aggregator,
            settings=settings,
            downloader=self._downloader,
            metadata_enricher=self._metadata_enricher,
            artwork_manager=self._artwork_manager,
            metadata_clients={"tvmaze": self._tvmaze} if self._tvmaze else {},
        )
        targets = await self._metadata_refresh_targets(result)
        for category_id, item_id in sorted(targets):
            category = self._categories.get(category_id)
            if not category:
                continue
            try:
                rows = await self._db.media.get_category_metadata(category_id, item_id)
                metadata = (rows[0].get("metadata") if rows else {}) or {}
                refresh_policy = self._category_metadata_refresh_policy(category, rows[0] if rows else None)
                if rows and not refresh_policy.get("due"):
                    # A stable library metadata snapshot should not be re-queried on
                    # every boot. If it contains provider artwork paths, the category
                    # may still cache local artwork without performing a live search.
                    if not (metadata.get("local_poster_url") or metadata.get("poster_url")) and metadata.get("poster_path") and hasattr(category, "cache_metadata_artwork"):
                        cached = await category.cache_metadata_artwork(
                            category.create_item(item_id), metadata, context, provider=metadata.get("provider", "metadata"),
                        )
                        if cached != metadata:
                            await self._db.media.upsert_category_metadata(
                                category_id, item_id, cached.get("provider", rows[0].get("provider") or "metadata"), cached,
                                str(cached.get("external_id") or cached.get("tmdb_id") or rows[0].get("external_id") or ""),
                            )
                            refreshed += 1
                    continue
                if metadata.get("local_poster_url") or metadata.get("poster_url"):
                    continue
                workflows = {workflow.name for workflow in category.declare_workflows()}
                workflow_name = "refresh_metadata" if "refresh_metadata" in workflows else "resolve_metadata" if "resolve_metadata" in workflows else "resolve_show" if "resolve_show" in workflows else None
                if not workflow_name:
                    continue
                self._emit_status(f"Downloading artwork for {item_id}", phase="running", item=item_id)
                await category.execute_workflow(workflow_name, {"item_id": item_id, "title": item_id}, context)
                refreshed += 1
            except Exception as exc:
                logger.debug(f"Metadata/artwork refresh skipped for {category_id}/{item_id}: {exc}")
        if refreshed:
            self._emit_status(f"Updated artwork for {refreshed} library items", phase="done")
        return refreshed

    def _category_metadata_refresh_policy(self, category: object, row: dict[str, Any] | None) -> dict[str, Any]:
        """Return whether one library metadata row is due for live refresh.

        Category-owned metadata workflows can persist ``metadata_refresh_policy``
        alongside stable provider IDs. The scheduler only interprets that generic
        policy envelope; it does not know that MusicBrainz, Open Library, or
        LibriVox have category-specific semantics.
        """
        if not row:
            return {"due": True, "reason": "missing_metadata"}
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        policy = metadata.get("metadata_refresh_policy") if isinstance(metadata.get("metadata_refresh_policy"), dict) else {}
        if not policy and hasattr(category, "metadata_refresh_policy"):
            try:
                policy = category.metadata_refresh_policy(provider=str(row.get("provider") or metadata.get("provider") or ""))
            except Exception:
                policy = {}
        try:
            refresh_days = int(policy.get("refresh_after_days") or policy.get("default_check_interval_days") or 90)
        except (TypeError, ValueError):
            refresh_days = 90
        refresh_days = max(1, min(refresh_days, 3650))
        refreshed_at = self._parse_refresh_timestamp(str(row.get("refreshed_at") or ""))
        if refreshed_at is None:
            return {"due": True, "reason": "unknown_last_refresh", "refresh_after_days": refresh_days}
        due_at = refreshed_at + timedelta(days=refresh_days)
        due = datetime.now(timezone.utc) >= due_at
        return {
            "due": due,
            "reason": "due" if due else "fresh_stable_snapshot",
            "refresh_after_days": refresh_days,
            "last_refreshed_at": refreshed_at.isoformat(),
            "next_refresh_at": due_at.isoformat(),
            "stable_id": metadata.get("stable_id") or row.get("external_id") or "",
        }

    @staticmethod
    def _parse_refresh_timestamp(value: str) -> datetime | None:
        """Parse SQLite/ISO metadata timestamps as timezone-aware UTC datetimes."""
        text = str(value or "").strip()
        if not text:
            return None
        for candidate in (text, text.replace("Z", "+00:00")):
            try:
                parsed = datetime.fromisoformat(candidate)
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    async def _ensure_scanned_items_exist(self, result: LibraryScanResult) -> int:
        """Ensure scanned category items exist before inserting scanned units.

        The category-unit table has a foreign key to ``category_items``.  A
        first library scan may discover files before the startup state sync has
        seen those newly discovered settings entries, so the scheduler persists
        parent item envelopes from the scan result before writing units.
        """
        ensured = 0
        settings = self._settings_manager.settings
        configured = {
            (
                getattr(item, "category_id", getattr(item, "item_type", "media")) or "media",
                canonical_item_key(clean_category_item_name(item.key, getattr(item, "category_id", getattr(item, "item_type", "media")) or "media")),
            ): item
            for item in settings.tracked_items
        }
        for scanned in result.items:
            canonical_name = clean_category_item_name(scanned.name, scanned.category_id)
            key = (scanned.category_id, canonical_item_key(canonical_name))
            configured_item = configured.get(key)
            item_id = getattr(configured_item, "key", None) or canonical_name
            if await self._db.media.get_category_item(scanned.category_id, item_id):
                continue
            item = configured_item or self._tracked_item_from_scan(scanned)
            payload = item.model_dump(mode="json")
            payload.setdefault("category_id", scanned.category_id)
            payload.setdefault("item_id", item_id)
            payload.setdefault("key", item_id)
            payload.setdefault("display_name", getattr(item, "display_name", None) or item_id)
            payload.setdefault("item_type", scanned.category_id)
            await self._db.media.upsert_category_item(scanned.category_id, item_id, payload)
            ensured += 1
        if ensured:
            logger.info(f"Library scan initialized {ensured} category item database records.")
        return ensured


    async def _mark_scanned_items_present(self, result: LibraryScanResult) -> int:
        """Mark scanned items as present so previous missing state is cleared."""
        if not self._db or not getattr(self._db, "media", None):
            return 0
        marked = 0
        for scanned in result.items:
            scanned_name = clean_category_item_name(scanned.name, scanned.category_id)
            item = await self._db.media.get_category_item(scanned.category_id, scanned_name)
            if not item:
                continue
            state = dict(item.get("state") or {})
            state.update({
                "library_present": True,
                "library_file_count": scanned.file_count,
                "library_total_size_bytes": scanned.total_size_bytes,
                "last_library_scan_at": datetime.now(timezone.utc).isoformat(),
            })
            item["state"] = state
            if item.get("status") == "missing_from_library":
                item["status"] = "available"
            await self._db.media.upsert_category_item(scanned.category_id, scanned_name, item)
            marked += 1
        return marked

    async def _remove_cross_category_scan_leaks(self, result: LibraryScanResult) -> int:
        """Remove stale items created by previous cross-category repair passes.

        Category roots are authoritative.  If the current scan finds an item under one category root, a stale item
        with the same loose identity under another category and no valid files
        inside that category root is a boundary leak, not a missing library item.  This
        cleanup repairs existing databases/settings created by older builds
        without ever moving scanned files between categories.
        """
        if not self._db or not getattr(self._db, "media", None):
            return 0

        scanned_by_identity: dict[str, set[str]] = {}
        for scanned in result.items:
            identity = canonical_item_key(clean_category_item_name(scanned.name, scanned.category_id))
            if identity:
                scanned_by_identity.setdefault(identity, set()).add(scanned.category_id)

        settings_items = self._settings_items_by_identity()
        removed = 0
        for item in await self._db.media.list_category_items():
            category_id = str(item.get("category_id") or item.get("item_type") or "")
            item_id = str(item.get("item_id") or item.get("key") or "")
            if not category_id or not item_id:
                continue
            identity = canonical_item_key(clean_category_item_name(item_id, category_id))
            categories_on_disk = scanned_by_identity.get(identity, set())
            if not categories_on_disk or category_id in categories_on_disk:
                continue

            settings_item = settings_items.get((category_id, identity))
            discovered = bool(getattr(settings_item, "discovered", False)) or bool(item.get("discovered"))
            status = str(item.get("status") or "").lower()
            units_outside_root = await self._item_has_units_outside_category_root(category_id, item_id)
            if not (discovered or status == "discovered" or units_outside_root):
                continue

            await self._db.media.delete_category_item(category_id, item_id)
            self._remove_settings_item_by_identity(category_id, item_id, discovered_only=False)
            removed += 1
            logger.warning(
                f"Removed cross-category library leak {category_id}:{item_id}; "
                f"matching disk item belongs to {', '.join(sorted(categories_on_disk))}"
            )

        if removed:
            self._emit_status(f"Library boundary cleanup removed {removed} stale item(s)", phase="done")
        return removed

    async def _item_has_units_outside_category_root(self, category_id: str, item_id: str) -> bool:
        """Return true when stored file paths for an item are outside its root."""
        if not self._categories:
            return False
        category = self._categories.get(category_id)
        if not category:
            return False
        try:
            root = Path(category.get_root_path(self._settings_manager.settings)).expanduser().resolve(strict=False)
        except Exception:
            return False
        try:
            units = await self._db.media.list_category_units(category_id, item_id)
        except Exception:
            return False
        for unit in units:
            file_path = str(unit.get("file_path") or unit.get("path") or "")
            if not file_path:
                continue
            try:
                current = Path(file_path).expanduser().resolve(strict=False)
                if not current.is_relative_to(root):
                    return True
            except Exception:
                continue
        return False

    async def _reconcile_removed_library_entries(self, result: LibraryScanResult) -> dict[str, int]:
        """Remove or mark repository rows whose library folders vanished from disk.

        A scan used to reconcile only child units inside folders that still existed.
        When a user deleted an entire category item folder with the OS, stale
        category rows and progress units could remain until a later explicit
        mutation.  This pass compares repository rows to the fresh disk scan:
        discovered-only items disappear, explicitly tracked items stay tracked
        but lose downloaded/progress units so the UI and agent see them as
        missing again.
        """
        if not self._db or not getattr(self._db, "media", None):
            return {"removed_items": 0, "cleared_units": 0}

        scanned_keys = {
            (item.category_id, canonical_item_key(clean_category_item_name(item.name, item.category_id)))
            for item in result.items
        }
        settings_items = self._settings_items_by_identity()
        removed_items = 0
        cleared_units = 0

        for item in await self._db.media.list_category_items():
            category_id = str(item.get("category_id") or item.get("item_type") or "")
            item_id = str(item.get("item_id") or item.get("key") or "")
            if not category_id or not item_id:
                continue
            clean_id = clean_category_item_name(item_id, category_id)
            identity = (category_id, canonical_item_key(clean_id))

            # Delete or merge dirty release aliases such as
            # "Silicon.Valley.S01-06.ITA.DLMUX.x264" once a clean scanned or
            # settings identity exists.  Otherwise the UI shows one clean
            # present card and one old MISSING_FROM_LIBRARY card.
            if clean_id != item_id and (identity in scanned_keys or identity in settings_items):
                await self._merge_repository_item(category_id, item_id, clean_id)
                self._remove_discovered_settings_item(category_id, item_id)
                removed_items += 1
                logger.info(f"Merged stale library identity alias {category_id}:{item_id} -> {clean_id}")
                continue

            if identity in scanned_keys:
                continue

            settings_item = settings_items.get(identity)
            if settings_item is not None:
                discovered_only = bool(getattr(settings_item, "discovered", False))
            else:
                discovered_only = bool(item.get("discovered") or item.get("status") == "discovered")

            if discovered_only:
                await self._db.media.delete_category_item(category_id, item_id)
                self._remove_discovered_settings_item(category_id, item_id)
                removed_items += 1
                logger.info(f"Removed vanished discovered library item {category_id}:{item_id}")
                continue

            cleared_units += await self._db.media.remove_category_units(category_id, item_id, status="downloaded")
            cleared_units += await self._db.media.remove_category_units(category_id, item_id, unit_type="progress")
            state = dict(item.get("state") or {})
            state.update({
                "library_present": False,
                "last_missing_scan_at": datetime.now(timezone.utc).isoformat(),
            })
            item["state"] = state
            item["status"] = "missing_from_library"
            await self._db.media.upsert_category_item(category_id, item_id, item)
            logger.info(f"Marked tracked item missing from library after scan: {category_id}:{item_id}")

        if removed_items or cleared_units:
            self._emit_status(
                f"Library reconciled: removed {removed_items} stale item(s), cleared {cleared_units} stale unit(s)",
                phase="done",
            )
            if self._event_bus:
                try:
                    self._event_bus.emit_system("library_reconciled", {
                        "removed_items": removed_items,
                        "cleared_units": cleared_units,
                    })
                except Exception:
                    pass
        return {"removed_items": removed_items, "cleared_units": cleared_units}

    async def _merge_repository_item(self, category_id: str, old_item_id: str, new_item_id: str) -> None:
        """Move runtime rows from a dirty alias to the canonical item id."""
        if not old_item_id or old_item_id == new_item_id:
            return
        if await self._db.media.get_category_item(category_id, new_item_id) is None:
            old_payload = await self._db.media.get_category_item(category_id, old_item_id) or {}
            payload = dict(old_payload)
            payload.update({
                "category_id": category_id,
                "item_id": new_item_id,
                "key": new_item_id,
                "display_name": clean_category_item_name(new_item_id, category_id),
                "item_type": category_id,
            })
            await self._db.media.upsert_category_item(category_id, new_item_id, payload)

        conn = await self._db.get_connection() if hasattr(self._db, "get_connection") else None
        if conn is None:
            await self._db.media.delete_category_item(category_id, old_item_id)
            return
        await conn.execute(
            """UPDATE OR IGNORE category_item_units
               SET item_id = ? WHERE category_id = ? AND item_id = ?""",
            (new_item_id, category_id, old_item_id),
        )
        await conn.execute(
            """UPDATE OR IGNORE category_item_metadata
               SET item_id = ? WHERE category_id = ? AND item_id = ?""",
            (new_item_id, category_id, old_item_id),
        )
        await conn.execute(
            """UPDATE OR IGNORE category_property_index
               SET item_id = ? WHERE category_id = ? AND item_id = ?""",
            (new_item_id, category_id, old_item_id),
        )
        await conn.execute(
            """UPDATE OR IGNORE downloads
               SET item_id = ?, item_name = ?
               WHERE category_id = ? AND (item_id = ? OR item_name = ?)""",
            (new_item_id, new_item_id, category_id, old_item_id, old_item_id),
        )
        await conn.execute(
            "DELETE FROM category_items WHERE category_id = ? AND item_id = ?",
            (category_id, old_item_id),
        )
        await conn.commit()

    def _settings_items_by_identity(self) -> dict[tuple[str, str], CategoryItem]:
        """Return tracked settings items keyed by category and canonical title."""
        settings = self._settings_manager.settings
        indexed: dict[tuple[str, str], CategoryItem] = {}
        for item in getattr(settings, "tracked_items", []):
            category_id = getattr(item, "category_id", getattr(item, "item_type", "media")) or "media"
            key = clean_category_item_name(getattr(item, "key", ""), category_id)
            if key:
                indexed[(category_id, canonical_item_key(key))] = item
        return indexed

    def _remove_discovered_settings_item(self, category_id: str, item_id: str) -> None:
        """Remove a vanished auto-discovered item from settings, preserving manual targets."""
        self._remove_settings_item_by_identity(category_id, item_id, discovered_only=True)

    def _remove_settings_item_by_identity(self, category_id: str, item_id: str, *, discovered_only: bool) -> None:
        """Remove a tracked settings item by category-local identity."""
        settings = self._settings_manager.settings
        tracked = getattr(settings, "tracked_items", None)
        if not tracked or not hasattr(tracked, "items"):
            return
        target_key = canonical_item_key(clean_category_item_name(item_id, category_id))
        before = len(tracked.items)
        tracked.items = [
            item for item in tracked.items
            if not (
                (not discovered_only or bool(getattr(item, "discovered", False)))
                and (getattr(item, "category_id", getattr(item, "item_type", category_id)) or category_id) == category_id
                and canonical_item_key(clean_category_item_name(getattr(item, "key", ""), category_id)) == target_key
            )
        ]
        if len(tracked.items) != before:
            self._settings_manager.save(settings)

    async def _sync_scanned_units(self, result: LibraryScanResult) -> int:
        """Sync scanned files through category-owned canonical unit builders.

        The scheduler deliberately does not know whether a category stores
        episodes, files, chapters, tracks, versions, or DLC.  It asks the owning
        category to convert a scanned item into the canonical item envelope, unit
        envelopes, and optional progress payload, then persists those envelopes
        generically.  Any category-specific missing/quality logic must live in
        the category's canonical object builder, not here.
        """
        if self._categories is None:
            return 0
        synced = 0
        for scanned in result.items or []:
            category = self._categories.get(scanned.category_id)
            if not category:
                continue
            scanned_name = clean_category_item_name(scanned.name, scanned.category_id)
            scanned.name = scanned_name
            item_payload = category.library_item_from_scan(scanned)
            await self._db.media.upsert_category_item(scanned.category_id, scanned_name, item_payload)

            units = category.library_units_from_scan(scanned)
            current_keys = {str(unit.get("unit_key") or "") for unit in units if unit.get("unit_key")}

            existing = await self._db.media.list_category_units(scanned.category_id, scanned_name, status="downloaded")
            for unit in existing:
                unit_key = str(unit.get("unit_key") or "")
                if unit_key and unit_key not in current_keys:
                    await self._db.media.remove_category_unit(scanned.category_id, scanned_name, unit_key)
                    logger.debug(f"Removed stale canonical unit: {scanned.category_id}/{scanned_name}/{unit_key}")

            for unit in units:
                unit_key = str(unit.get("unit_key") or "")
                if not unit_key:
                    continue
                await self._db.media.record_unit_downloaded(scanned.category_id, scanned_name, unit_key, unit)
                synced += 1

            progress = category.library_progress_from_scan(scanned, units)
            if progress:
                await self._db.media.update_item_progress(scanned.category_id, scanned_name, progress)
        return synced


    async def _invalidate_scanned_lifecycle_state(self, result: LibraryScanResult) -> int:
        """Mark lifecycle ledgers due when a scan changed local library facts.

        Library scans update the category-unit tables before this method runs.
        The lifecycle engine can therefore compare the freshly persisted unit
        fingerprint with the last successful processing row.  Only items whose
        local-library fingerprint changed are invalidated; stable items remain
        dormant until their category policy says they are due.
        """
        if not self._lifecycle or not result:
            return 0
        configured = self._settings_items_by_identity()
        invalidated = 0
        for scanned in result.items or []:
            category_id = scanned.category_id
            clean_name = clean_category_item_name(scanned.name, category_id)
            identity = (category_id, canonical_item_key(clean_name))
            item = configured.get(identity)
            if item is None:
                category = self._categories.get(category_id) if self._categories else None
                if category:
                    item = category.create_item(clean_name)
                else:
                    item = GenericMediaItem(key=clean_name, category_id=category_id)
            try:
                decision = await self._lifecycle.should_process_item(item, purpose="library_scan", force=False)
                if "library_changed" not in decision.invalidated_by and decision.previous_state is not None:
                    continue
                await self._lifecycle.invalidate_item(
                    category_id,
                    getattr(item, "key", clean_name),
                    reason="library_scan_changed" if decision.previous_state is not None else "library_scan_new_item",
                    payload={
                        "file_count": scanned.file_count,
                        "total_size_bytes": scanned.total_size_bytes,
                        "scan_item_name": scanned.name,
                    },
                )
                invalidated += 1
            except Exception as exc:
                logger.debug(f"Lifecycle scan invalidation skipped for {category_id}/{clean_name}: {exc}")
        if invalidated:
            logger.info(f"Library scan invalidated lifecycle state for {invalidated} changed item(s).")
        return invalidated


    async def _discover_new_items(self, result: LibraryScanResult) -> None:
        """Add newly scanned category items to tracked settings."""
        settings = self._settings_manager.settings
        existing_by_category_key = {
            (
                getattr(item, "category_id", getattr(item, "item_type", "media")) or "media",
                canonical_item_key(clean_category_item_name(item.key, getattr(item, "category_id", getattr(item, "item_type", "media")) or "media")),
            ): item
            for item in settings.tracked_items
        }
        for scanned in result.items:
            canonical_name = clean_category_item_name(scanned.name, scanned.category_id)
            scan_key = (scanned.category_id, canonical_item_key(canonical_name))
            if scan_key not in existing_by_category_key:
                scanned.name = canonical_name
                item = self._tracked_item_from_scan(scanned)
                settings.tracked_items.append(item)
                existing_by_category_key[scan_key] = item
            # NOTE: Scanner does NOT overwrite existing item's language.
            # User's explicit language setting takes precedence.
            # Language is only set for newly discovered items (above).

        # Scanned category is authoritative.  Provider metadata may improve the
        # display name/poster inside that category later, but it must never move
        # a file discovered under one category root into another category.
        await asyncio.to_thread(self._settings_manager.save, settings)

    def _tracked_item_from_scan(self, scanned: ScannedLibraryItem) -> CategoryItem:
        """Create a tracked item by delegating model ownership to the category."""
        settings = self._settings_manager.settings
        quality = self._quality_inferrer.infer_for_item(scanned)
        kwargs = {
            "discovered": True,
            "language": scanned.detected_language or settings.language,
            "quality": quality,
            "auto_download": None,
        }
        if scanned.year is not None:
            kwargs["year"] = scanned.year
        if self._categories and (category := self._categories.get(scanned.category_id)):
            return category.create_item(scanned.name, **kwargs)
        return GenericMediaItem(key=scanned.name, category_id=scanned.category_id, **kwargs)
    async def _update_tracked_items_job(self):
        settings = self._settings_manager.settings
        from src.core.categories.base import CategoryUpdateContext, CategoryWorkflowContext
        
        update_context = CategoryUpdateContext(
            db=self._db,
            pipeline=self._pipeline,
            aggregator=self._aggregator,
            settings=settings
        )
        workflow_context = CategoryWorkflowContext(
            db=self._db,
            pipeline=self._pipeline,
            aggregator=self._aggregator,
            settings=settings,
            downloader=self._downloader,
            metadata_enricher=self._metadata_enricher,
            artwork_manager=self._artwork_manager,
            metadata_clients={"tvmaze": self._tvmaze} if self._tvmaze else {},
        )
        
        dormant_count = 0
        for item in settings.tracked_items:
            if not item.enabled:
                continue
            category_id = getattr(item, "category_id", getattr(item, "item_type", "media")) or "media"
            if await self._db.media.get_category_item_paused(category_id, item.key):
                continue

            category = self._categories.get(item.item_type) if self._categories else None
            if not category:
                continue
            category_config = (settings.category_settings or {}).get(category.category_id, {})
            scheduler_config = category_config.get("scheduler") if isinstance(category_config, dict) else {}
            if isinstance(scheduler_config, dict) and scheduler_config.get("enabled") is False:
                logger.debug(f"Skipping scheduled checks for disabled category scheduler: {category.category_id}")
                continue
            try:
                workflow_names = {workflow.name for workflow in category.declare_workflows()}

                async def _run_category_update() -> None:
                    if "scheduled_check" in workflow_names:
                        await category.execute_workflow("scheduled_check", {"item_id": item.key}, workflow_context)
                    elif item.needs_periodic_checks:
                        await category.update(item, update_context)

                ran = await self._lifecycle.run_scheduled_workflow(
                    item,
                    _run_category_update,
                    purpose="scheduled_check",
                    force=False,
                )
                if not ran:
                    dormant_count += 1
            except Exception as e:
                logger.error(f"Failed to update category item {item.key}: {e}")

        if dormant_count:
            # One debug summary keeps the lifecycle ledger visible without flooding
            # Voyage Logs with a line for every dormant item on each scheduler pass.
            logger.debug(f"Lifecycle ledger kept {dormant_count} category item(s) dormant until their next due time.")

        self._settings_manager.save(settings)

    async def _check_stalled_downloads_job(self) -> None:
        """Run the generic download-health supervisor.

        This replaces the older scheduler-local stall handler.  The supervisor
        parks idle torrents, periodically gives parked rare torrents a priority
        test window, and surfaces alternatives through the existing search and
        ranking pipeline without auto-cancelling the original torrent.
        """
        if not self._download_health:
            return
        try:
            await self._download_health.run_once()
        except Exception as exc:
            logger.error(f"Download health supervisor failed: {exc}")

    async def _cache_candidates(self, candidates: list, name: str, season: int | None, episode: int | None) -> None:
        """Cache candidates for recent sessions so selection works on any active platform."""
        import json
        
        # Build the structured cache object
        cache_candidates = []
        for i, c in enumerate(candidates):
            size_val = getattr(c, "size_bytes", getattr(c, "size", 0))
            size_str = QualityAnalyzer.format_size(size_val) if size_val else "Unknown Size"
            cache_candidates.append({
                "index": i + 1,
                "title": c.title,
                "magnet": c.magnet,
                "size": size_str,
                "seeders": getattr(c, "seeders", 0),
                "source": getattr(c, "source", "")
            })
            
        cache_data = {
            "name": name,
            "season": season,
            "episode": episode,
            "candidates": cache_candidates
        }
        serialized = json.dumps(cache_data)
        
        # Find active session IDs
        session_ids = ["default"]
        try:
            active_ids = await self._db.system.get_active_session_ids(days=7)
            session_ids.extend(active_ids)
            session_ids = list(set(session_ids))
        except Exception as e:
            logger.warning(f"Failed to fetch active session IDs: {e}")
            
        for sid in session_ids:
            key = f"last_options_{sid}"
            try:
                await self._db.system.set_preference(key, serialized)
            except Exception as e:
                logger.warning(f"Failed to cache candidates for session {sid}: {e}")

    async def sync_air_schedules(self) -> None:
        """Refresh category-owned scheduled-unit state.

        The scheduler does not interpret episodes, air dates, release versions,
        publication dates, or any other domain schedule.  It asks each category
        for optional state updates and persists whatever that category returns.
        """
        if not self._categories:
            return
        settings = self._settings_manager.settings
        changed = False
        context = {
            "tvmaze": self._tvmaze,
            "db": self._db,
            "settings": settings,
            "metadata_enricher": self._metadata_enricher,
        }
        for item in settings.tracked_items:
            category = self._categories.get(getattr(item, "item_type", "")) if self._categories else None
            if not (category and hasattr(category, "next_scheduled_unit")):
                continue
            result = await category.next_scheduled_unit(item, context)
            updates = (result or {}).get("state_updates") or {}
            if updates:
                item.state.update(updates)
                changed = True
        if changed:
            self._settings_manager.save(settings)



    async def reconcile_lifecycle_ledgers(self) -> int:
        """Cheaply ensure tracked items have lifecycle ledger rows at startup."""
        settings = self._settings_manager.settings
        raw_items = getattr(settings, "tracked_items", []) or []
        if hasattr(raw_items, "items") and not isinstance(raw_items, list):
            items = list(getattr(raw_items, "items") or [])
        else:
            items = list(raw_items)
        return await self._lifecycle.reconcile_startup_ledgers(items)

    async def compile_suggestions(self, *, force: bool = False) -> None:
        """Compile category-owned suggestions and notify the UI.

        Fresh setup can trigger suggestion compilation while the first library
        scan is still reconciling discovered files into canonical category
        units.  Compiling in that window makes populated shows look empty and
        persists bogus "download everything" suggestions.  If a scan is active,
        wait for the scan lock to release, then read the canonical objects.
        """
        if not self._suggestion_compiler:
            return
        if self._library_scan_lock.locked():
            logger.debug("Suggestion compilation waiting for active library scan reconciliation.")
            async with self._library_scan_lock:
                pass
        settings = self._settings_manager.settings
        raw_items = getattr(settings, "tracked_items", []) or []
        if hasattr(raw_items, "items") and not isinstance(raw_items, list):
            items = list(getattr(raw_items, "items") or [])
        else:
            items = list(raw_items)
        self._emit_status("Compiling suggestions", phase="running")
        try:
            total = await self._suggestion_compiler.compile_all(items, force=force)
            self._emit_status(f"Suggestions ready: {total} action(s)", phase="done")
            if self._event_bus:
                self._event_bus.emit_system("suggestions_updated", {"count": total})
        except Exception as exc:
            logger.warning(f"Suggestion compilation failed: {exc}")
            self._emit_status("Suggestion compilation failed", phase="error")

    async def _send_recommendations(self):
        if self._recommender: await self._recommender.send_recommendations()

    async def _auto_cleanup_watched(self):
        if self._content_cleanup: await self._content_cleanup.auto_cleanup_watched()

    async def _run_scheduled_prompts(self):
        if self._prompt_scheduler: await self._prompt_scheduler.run_due_tasks()

    @staticmethod
    def _best_resolution(resolutions: list[str]) -> str:
        if not resolutions: return ""
        return max(resolutions, key=QualityAnalyzer.rank_resolution)

    def stop(self) -> None:
        """Run the public lifecycle operation stop.

        This method is part of runtime orchestration.  Keep it idempotent
        where practical, clean up owned resources, and delegate specialized
        work to collaborators to preserve testability.
        """
        self._scheduler.shutdown()
        logger.info("Scheduler stopped.")

    def _service_context(self) -> SchedulerServiceContext:
        """Build an explicit collaborator context for scheduler sub-services."""
        return SchedulerServiceContext(
            settings_manager=self._settings_manager,
            db=self._db,
            downloader=self._downloader,
            pipeline=self._pipeline,
            aggregator=self._aggregator,
            categories=self._categories,
            tvmaze=self._tvmaze,
            metadata_enricher=self._metadata_enricher,
        )

    def _catalog_service(self) -> SchedulerCatalogService:
        """Return a catalog service for media list and priority operations."""
        return SchedulerCatalogService(self._service_context())

    def _torrent_search_service(self) -> SchedulerTorrentSearchService:
        """Return a torrent search service for assistant media searches."""
        return SchedulerTorrentSearchService(self._service_context())


    async def handle_release_event(
        self,
        item: CategoryItem,
        *,
        unit_label: str | None,
        source_result: SearchResult | None = None,
        trigger: str = "manual",
    ) -> dict[str, Any]:
        """Handle a concrete provider/RSS release event through the owning category.

        The scheduler is category-neutral: it identifies the category and passes
        the event/context to the category. TV decides what a frontier episode is;
        other categories may later implement equivalent unit semantics.
        """
        category_id = getattr(item, "item_type", "") or ""
        category = self._categories.get(category_id) if self._categories else None
        if not category or not hasattr(category, "handle_release_event"):
            logger.info("Release event ignored: no category handler for %s/%s", category_id, getattr(item, "key", ""))
            return {"status": "ignored", "reason": "no_category_release_handler"}
        from src.core.categories.base import CategoryWorkflowContext
        context = CategoryWorkflowContext(
            db=self._db,
            pipeline=self._pipeline,
            aggregator=self._aggregator,
            settings=self._settings_manager.settings,
            downloader=self._downloader,
            category_registry=self._categories,
            metadata_clients={"tvmaze": self._tvmaze} if self._tvmaze else {},
            metadata_enricher=self._metadata_enricher,
            artwork_manager=self._artwork_manager,
        )
        event = {
            "trigger": trigger,
            "unit_label": unit_label or "",
            "source_result": source_result.model_dump() if hasattr(source_result, "model_dump") else dict(source_result or {}),
        }
        result = await category.handle_release_event(item, event, context, notifications=self._notifications, lifecycle=self._lifecycle)
        try:
            await self._lifecycle.invalidate_item(category_id, getattr(item, "key", ""), reason="new_episode_detected", payload=result)
        except Exception as exc:
            logger.debug("Release event lifecycle invalidation failed for %s/%s: %s", category_id, getattr(item, "key", ""), exc)
        if self._event_bus:
            self._event_bus.emit_system("release_event", {"category_id": category_id, "item_id": getattr(item, "key", ""), "result": result})
        return result if isinstance(result, dict) else {"status": "ok"}

    async def process_release_watches(self) -> None:
        """Retry pending release watches until a category queues or resolves them."""
        repo = getattr(self._db, "release_watches", None)
        if not repo:
            return
        watches = await repo.due(limit=20)
        if not watches:
            return
        logger.info("Processing %s due release watch(es).", len(watches))
        for watch in watches:
            category_id = str(watch.get("category_id") or "")
            item_id = str(watch.get("item_id") or "")
            unit_key = str(watch.get("unit_key") or "")
            category = self._categories.get(category_id) if self._categories else None
            item = next(
                (tracked for tracked in getattr(self._settings_manager.settings, "tracked_items", [])
                 if getattr(tracked, "item_type", "") == category_id and getattr(tracked, "key", "") == item_id),
                None,
            )
            if not category or not item:
                await repo.record_attempt(int(watch["id"]), status="cancelled", error="tracked item disappeared")
                continue
            try:
                preferred_language = str(watch.get("preferred_language") or getattr(item, "language", "") or self._settings_manager.settings.language)
                item_auto = getattr(item, "auto_download", None)
                can_auto_download = bool(item_auto if item_auto is not None else self._settings_manager.settings.auto_download)

                if not can_auto_download:
                    # Auto-download-disabled watches are still useful: run a
                    # non-queueing category search and notify the web inbox when
                    # an acceptable candidate exists.  The action remains a
                    # category workflow so the scheduler does not learn TV/movie
                    # unit semantics.
                    candidate = await self._pipeline.run_search(
                        item, episode_label=unit_key, mode="auto", language=preferred_language,
                    )
                    if candidate and hasattr(category, "candidate_requires_user_language_confirmation"):
                        try:
                            if category.candidate_requires_user_language_confirmation(candidate, item, unit_key, preferred_language):
                                candidate = None
                        except Exception:
                            pass
                    if candidate:
                        if self._notifications:
                            from src.core.models import NotificationMessage
                            action_builder = getattr(category, "release_watch_notification_action", None)
                            actions = []
                            if callable(action_builder):
                                try:
                                    actions = [action_builder(item_id, unit_key, candidate, preferred_language)]
                                except Exception as exc:
                                    logger.debug("Category release-watch action build failed for %s/%s/%s: %s", category_id, item_id, unit_key, exc)
                            await self._notifications.notify(
                                NotificationMessage(
                                    title=f"{item_id} {unit_key}",
                                    body=f"I found an acceptable release for {item_id} {unit_key}. Download it?",
                                    level="info",
                                ),
                                category_id=category_id,
                                item_id=item_id,
                                event_type="release_watch_candidate_found",
                                actions=actions,
                                metadata={"unit_key": unit_key, "preferred_language": preferred_language, "watch_id": watch.get("id")},
                                dedupe_key=f"release_watch_found:{category_id}:{item_id}:{unit_key}",
                            )
                        await repo.record_attempt(int(watch["id"]), status="completed")
                    else:
                        await repo.record_attempt(int(watch["id"]), status="pending", error="no acceptable candidate", interval_hours=float(watch.get("interval_hours") or 2.0))
                    continue

                ok = await self._pipeline.run_discovery(
                    item,
                    episode_label=unit_key,
                    force=False,
                    language=preferred_language,
                )
                if ok:
                    await repo.record_attempt(int(watch["id"]), status="completed")
                    if self._notifications:
                        await self._notifications.send_message(
                            f"Queued {item_id} {unit_key} after a release-watch retry.",
                            title="Release Found",
                            level="success",
                        )
                else:
                    await repo.record_attempt(int(watch["id"]), status="pending", error="no acceptable candidate", interval_hours=float(watch.get("interval_hours") or 2.0))
            except Exception as exc:
                logger.warning("Release watch retry failed for %s/%s/%s: %s", category_id, item_id, unit_key, exc)
                await repo.record_attempt(int(watch["id"]), status="pending", error=str(exc), interval_hours=float(watch.get("interval_hours") or 2.0))

    async def list_media(self) -> dict:
        """Return tracked media rows with category-neutral progress fields."""
        return await self._catalog_service().list_media()

    async def list_media_items(self, name: str) -> dict:
        """Return downloaded and active units for one tracked media item."""
        return await self._catalog_service().list_media_items(name)

    async def set_download_priority(self, name: str, priority: str,
                                     season: int | None = None,
                                     episode: int | None = None) -> dict:
        """Change priority for active or queued downloads matching filters."""
        return await self._catalog_service().set_download_priority(name, priority, season, episode)

    async def search_media_torrents(self, name: str, season: int | None = None,
                                     episode: int | None = None,
                                     language: str | None = None,
                                     language_explicit: bool = False,
                                     search_scope: str | None = None,
                                     category_id: str | None = None) -> dict:
        """Search for torrents for a specific media item via focused service."""
        return await self._torrent_search_service().search_media_torrents(
            name, season, episode, language, language_explicit=language_explicit, search_scope=search_scope, category_id=category_id,
        )

    async def execute_category_workflow(self, category_id: str, workflow_name: str, arguments: dict) -> object:
        """Execute a category-owned workflow with scheduler collaborators.

        UI helpers such as suggestions should go through this hook rather than
        reimplementing category-specific search and queue logic in web handlers.
        """
        if not self._categories:
            raise RuntimeError("Category registry is not available")
        category = self._categories.get(category_id)
        if not category:
            raise RuntimeError(f"Unknown category: {category_id}")
        from src.core.categories.base import CategoryWorkflowContext
        context = CategoryWorkflowContext(
            db=self._db,
            pipeline=self._pipeline,
            aggregator=self._aggregator,
            settings=self._settings_manager.settings,
            downloader=self._downloader,
            category_registry=self._categories,
            metadata_clients={"tvmaze": self._tvmaze} if self._tvmaze else {},
            metadata_enricher=self._metadata_enricher,
            artwork_manager=self._artwork_manager,
        )
        return await category.execute_workflow(workflow_name, arguments, context)

    async def queue_download(self, name: str, magnet: str, 
                              season: int | None = None, 
                              episode: int | None = None,
                              category_id: str = "",
                              estimated_size_bytes: int | None = None,
                              priority: DownloadPriority = DownloadPriority.HIGH,
                              torrent_title: str = "",
                              source_seeders: int | None = None,
                              import_context: object | None = None) -> dict:
        """Manually queue a download from a search result with storage preflight."""
        item = await self._downloader.add_magnet(
            magnet_link=magnet, item_name=name, season=season, episode=episode,
            reason="manual", priority=priority,
            category_id=category_id, estimated_size_bytes=estimated_size_bytes,
            torrent_title=torrent_title, source_seeders=source_seeders,
            import_context=import_context,
        )
        return {"status": "queued", "download_id": item.id}

    def get_last_scan_result(self) -> object:
        """Return the last library scan result, or None if no scan has run."""
        return self._last_scan_result

    def get_metadata_enricher(self) -> object:
        """Return the metadata enricher instance, or None if not configured."""
        return self._metadata_enricher

    def get_search_pipeline(self) -> "SearchPipeline":
        """Return the search pipeline used for discovery and search."""
        return self._pipeline

    async def check_item(self, item: CategoryItem, force: bool = False,
                         episode_label: str | None = None) -> None:
        """Check one category item and queue downloads when its workflow allows it.

        Background callers default to non-forced checks. UI/assistant callers
        that intentionally approve a suggestion can pass ``force=True``.
        """
        await self._pipeline.run_discovery(item, episode_label=episode_label, force=force)
