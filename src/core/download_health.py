"""Download health supervisor for LJS.

Generic stalled/slow torrent management that lives in the download layer,
not in any specific media category.  It observes persisted DownloadItem
state, parks torrents that are not making real byte progress, periodically
retests parked torrents in a priority slot, and optionally surfaces better
alternatives found through the existing category/search/ranking pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger

from src.core.models import (
    CategoryItem,
    DownloadItem,
    DownloadPriority,
    DownloadStatus,
    GenericMediaItem,
    SearchResult,
)
from src.utils.quality import QualityAnalyzer


@dataclass
class DownloadHealthState:
    """In-memory rolling health state for a single download."""

    download_id: str
    last_bytes: int = 0
    last_progress_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    parked_at: datetime | None = None
    next_test_at: datetime | None = None
    testing_until: datetime | None = None
    test_baseline_bytes: int = 0
    original_priority: DownloadPriority | None = None
    alternative_checked_at: datetime | None = None


class DownloadHealthSupervisor:
    """Supervise slow/stalled downloads without clogging active slots.

    The supervisor is intentionally generic:
    - it never assumes TV/movie semantics directly;
    - categories only enter when building a search item for alternatives;
    - the existing search/ranking layer decides candidate quality;
    - rare torrents are parked and periodically tested, not failed.
    """

    def __init__(
        self,
        *,
        settings_manager: Any,
        db: Any,
        downloader: Any,
        pipeline: Any | None,
        categories: Any | None = None,
        notifications: Any | None = None,
        event_bus: Any | None = None,
    ) -> None:
        self._settings_manager = settings_manager
        self._db = db
        self._downloader = downloader
        self._pipeline = pipeline
        self._categories = categories
        self._notifications = notifications
        self._event_bus = event_bus
        self._states: dict[str, DownloadHealthState] = {}

    def set_event_bus(self, event_bus: Any | None) -> None:
        """Set the event bus dependency or configuration.

        Use this mutator for explicit dependency injection only.  Preserve
        idempotency so tests and runtime setup can call it safely.
        """
        self._event_bus = event_bus

    async def run_once(self) -> dict[str, int]:
        """Run one health pass and return counters for logs/tests."""
        now = datetime.now(timezone.utc)
        repaired_completed = 0
        if hasattr(self._downloader, "reconcile_completed_downloads"):
            try:
                repaired_completed = await self._downloader.reconcile_completed_downloads(limit=100)
            except Exception as exc:
                logger.debug(f"Completed-download reconciliation skipped: {exc}")
        active = await self._downloader.get_active_downloads()
        counters = {"observed": 0, "parked": 0, "tests_started": 0, "alternatives": 0, "completed_repaired": repaired_completed}
        live_ids = {d.id for d in active}
        for stale_id in list(self._states):
            if stale_id not in live_ids:
                self._states.pop(stale_id, None)

        for item in active:
            if item.status not in (DownloadStatus.DOWNLOADING, DownloadStatus.STALLED):
                continue
            counters["observed"] += 1
            state = self._states.setdefault(item.id, DownloadHealthState(download_id=item.id))
            if state.last_bytes == 0 and item.downloaded_bytes:
                state.last_bytes = item.downloaded_bytes
                state.last_progress_at = now

            if item.status == DownloadStatus.DOWNLOADING:
                if await self._handle_downloading(item, state, now):
                    counters["parked"] += 1
                continue

            if item.status == DownloadStatus.STALLED:
                alt_count = await self._maybe_find_alternatives(item, state, now)
                counters["alternatives"] += alt_count
                if await self._maybe_start_health_test(item, state, now):
                    counters["tests_started"] += 1

        if any(counters.values()):
            logger.debug(f"Download health pass: {counters}")
        return counters

    async def _handle_downloading(self, item: DownloadItem, state: DownloadHealthState, now: datetime) -> bool:
        """Update rolling state; park when no real byte movement is observed."""
        bytes_delta = max(0, int(item.downloaded_bytes or 0) - int(state.last_bytes or 0))
        min_delta = self._min_progress_bytes()
        if bytes_delta >= min_delta:
            state.last_bytes = int(item.downloaded_bytes or 0)
            state.last_progress_at = now
            if state.testing_until:
                await self._finish_successful_test(item, state)
            return False

        if state.testing_until:
            if now >= state.testing_until:
                await self._park(item, state, "health test window ended with no byte progress")
                return True
            return False

        if self._is_download_complete_enough(item):
            return False

        idle_for = now - state.last_progress_at
        if idle_for >= self._stall_window() and self._is_transfer_idle(item):
            await self._park(item, state, f"no byte progress for {int(idle_for.total_seconds() // 60)} minutes")
            return True
        return False

    async def _park(self, item: DownloadItem, state: DownloadHealthState, reason: str) -> None:
        """Park a stalled torrent and release its active slot."""
        logger.info(f"Parking stalled download {item.id} ({item.item_name}): {reason}")
        await self._downloader.park_stalled_download(item.id, reason=reason)
        now = datetime.now(timezone.utc)
        state.parked_at = now
        state.testing_until = None
        state.test_baseline_bytes = int(item.downloaded_bytes or 0)
        state.next_test_at = now + self._test_interval()
        self._emit_status(f"Parked stalled download: {item.item_name}", phase="warning", item=item.item_name)

    async def _maybe_start_health_test(self, item: DownloadItem, state: DownloadHealthState, now: datetime) -> bool:
        """Resume a parked torrent in a priority slot for a bounded test window."""
        if state.testing_until and now < state.testing_until:
            return False
        if state.next_test_at and now < state.next_test_at:
            return False

        logger.info(f"Starting health test for stalled download {item.id} ({item.item_name})")
        state.original_priority = item.priority
        state.test_baseline_bytes = int(item.downloaded_bytes or 0)
        state.testing_until = now + self._test_duration()
        state.next_test_at = None
        resumed = await self._downloader.start_health_test(item.id, temporary_priority=DownloadPriority.HIGH)
        if resumed:
            self._emit_status(f"Testing stalled download: {item.item_name}", phase="info", item=item.item_name)
            return True
        # Could not get a slot; retry soon rather than waiting a full interval.
        state.testing_until = None
        state.next_test_at = now + timedelta(minutes=5)
        return False

    async def _finish_successful_test(self, item: DownloadItem, state: DownloadHealthState) -> None:
        """A parked torrent moved bytes during its test; keep it active."""
        logger.info(f"Health test succeeded for {item.id} ({item.item_name}); leaving download active")
        if state.original_priority and item.priority != state.original_priority:
            try:
                await self._downloader.set_priority(item.id, state.original_priority)
            except Exception as exc:
                logger.debug(f"Could not restore priority after health test for {item.id}: {exc}")
        state.testing_until = None
        state.next_test_at = None
        state.parked_at = None
        state.original_priority = None
        self._emit_status(f"Stalled download recovered: {item.item_name}", phase="success", item=item.item_name)

    async def _maybe_find_alternatives(self, item: DownloadItem, state: DownloadHealthState, now: datetime) -> int:
        """Search and surface alternatives at most once per parked interval."""
        if not self._pipeline:
            return 0
        if state.alternative_checked_at and (now - state.alternative_checked_at) < self._alternative_cooldown():
            return 0
        state.alternative_checked_at = now

        try:
            media = self._build_category_item(item)
            episode_label = self._episode_label(item)
            candidates = await self._pipeline.run_search(media, episode_label, mode="llm", language=media.language)
        except Exception as exc:
            logger.warning(f"Failed to search alternatives for stalled download {item.id}: {exc}")
            return 0

        if isinstance(candidates, SearchResult):
            candidates = [candidates]
        valid = [c for c in (candidates or []) if c.magnet and c.magnet != item.magnet][:3]
        if not valid:
            return 0

        await self._surface_alternatives(item, valid)
        return len(valid)

    def _build_category_item(self, item: DownloadItem) -> CategoryItem:
        settings = self._settings_manager.settings
        tracked = next((m for m in settings.tracked_items if m.key == item.item_name or m.key == item.item_id), None)
        if tracked:
            return tracked
        category_id = item.category_id or "media"
        category = self._categories.get(category_id) if self._categories else None
        language = item.language or getattr(settings, "language", "") or "English"
        if category:
            return category.create_item(item.item_name, language=language)
        return GenericMediaItem(key=item.item_name, category_id=category_id, language=language)

    @staticmethod
    def _episode_label(item: DownloadItem) -> str | None:
        if item.season is not None and item.episode is not None:
            return f"S{int(item.season):02d}E{int(item.episode):02d}"
        if item.season is not None:
            return f"Season {int(item.season)}"
        return None

    async def _surface_alternatives(self, item: DownloadItem, candidates: list[SearchResult]) -> None:
        lines = [
            f"The download for '{item.item_name}' is parked because it stopped moving bytes.",
            "I found possible alternatives, but I did not replace anything automatically.",
            "The parked torrent will be tested again later so rare torrents still get a chance.",
            "",
            "Alternatives:",
        ]
        for i, cand in enumerate(candidates, 1):
            size_val = cand.size_bytes if cand.size_bytes else cand.size
            try:
                size_str = QualityAnalyzer.format_size(size_val) if isinstance(size_val, int) else str(size_val)
            except Exception:
                size_str = str(size_val or "Unknown")
            lines.append(f"{i}. {cand.title} ({size_str}, {cand.seeders or 0} seeders)")
        message = "\n".join(lines)
        if self._notifications:
            await self._notifications.send_message(message, title="Stalled Download Alternatives", level="info")
        self._emit_status(f"Found alternatives for parked download: {item.item_name}", phase="info", item=item.item_name)

    def _emit_status(self, message: str, *, phase: str = "info", item: str | None = None) -> None:
        if not self._event_bus:
            return
        try:
            self._event_bus.emit_system("background_status", {"message": message, "phase": phase, "item": item})
        except Exception:
            pass

    def _stall_window(self) -> timedelta:
        raw = getattr(self._settings_manager.settings, "stall_health_window_minutes", None)
        if raw is None:
            raw = float(getattr(self._settings_manager.settings, "stall_alternative_hours", 1.0)) * 60.0
        minutes = max(5.0, float(raw or 30.0))
        return timedelta(minutes=minutes)

    def _test_interval(self) -> timedelta:
        minutes = max(10.0, float(getattr(self._settings_manager.settings, "stall_test_interval_minutes", 60.0) or 60.0))
        return timedelta(minutes=minutes)

    def _test_duration(self) -> timedelta:
        minutes = max(2.0, float(getattr(self._settings_manager.settings, "stall_test_duration_minutes", 15.0) or 15.0))
        return timedelta(minutes=minutes)

    def _alternative_cooldown(self) -> timedelta:
        minutes = max(30.0, float(getattr(self._settings_manager.settings, "stall_alternative_cooldown_minutes", 180.0) or 180.0))
        return timedelta(minutes=minutes)

    def _min_progress_bytes(self) -> int:
        return max(64 * 1024, int(getattr(self._settings_manager.settings, "stall_min_progress_bytes", 512 * 1024) or 512 * 1024))

    def _is_transfer_idle(self, item: DownloadItem) -> bool:
        threshold = max(1024.0, float(getattr(self._settings_manager.settings, "stall_idle_rate_bps", 1024.0) or 1024.0))
        return float(item.download_rate or 0) <= threshold

    @staticmethod
    def _is_download_complete_enough(item: DownloadItem) -> bool:
        return float(item.progress or 0.0) >= 0.995
