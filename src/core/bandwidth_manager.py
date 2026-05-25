"""
Bandwidth manager for LJS.
Handles dynamic throttling of download and upload speeds based on schedules.
"""

import asyncio
from datetime import datetime
from loguru import logger
from typing import Optional, TYPE_CHECKING
from src.core.models import BandwidthSchedule

if TYPE_CHECKING:
    from src.core.config import SettingsManager
    from src.core.torrent_engine import TorrentEngine


class BandwidthManager:
    """Manages dynamic bandwidth throttling based on user-defined schedules."""

    def __init__(self, settings_manager: "SettingsManager", engine: "TorrentEngine") -> None:
        self._settings_manager = settings_manager
        self._engine = engine
        self._current_limit: tuple[Optional[int], Optional[int], Optional[int], Optional[int], bool] = (None, None, None, None, False)
        # Tracks the full applied transfer policy, not just download/upload caps.
        # Sharing settings can change independently from the schedule, so omitting
        # them here would leave stale library seed limits until another schedule tick.

    async def run_loop(self) -> None:
        """Background task to periodically check and apply bandwidth limits."""
        logger.info("Bandwidth scheduler loop started.")
        while True:
            try:
                await self.check_and_apply()
            except Exception as e:
                logger.error(f"Bandwidth manager error: {e}")
            await asyncio.sleep(60)

    async def check_and_apply(self) -> None:
        """Evaluate current time against schedules and apply limits to the engine."""
        settings = self._settings_manager.settings
        now = datetime.now()
        current_time = now.strftime("%H:%M")
        weekday = now.weekday()

        # Find the first matching schedule
        active_schedule = None
        for schedule in settings.bandwidth_schedules:
            if weekday not in schedule.days:
                continue
                
            is_active = False
            if schedule.start_time <= schedule.end_time:
                # Normal schedule (e.g., 09:00 - 17:00)
                if schedule.start_time <= current_time <= schedule.end_time:
                    is_active = True
            else:
                # Midnight wrap schedule (e.g., 22:00 - 06:00)
                if current_time >= schedule.start_time or current_time <= schedule.end_time:
                    is_active = True
            
            if is_active:
                active_schedule = schedule
                break

        # Fallback to global defaults if no schedule matches
        down = active_schedule.max_download_kbps if active_schedule else settings.default_quality.max_download_speed_kbps
        up = active_schedule.max_upload_kbps if active_schedule else settings.default_quality.max_upload_speed_kbps

        sharing = settings.sharing
        library_up = (int(sharing.library_upload_speed_kbps or 0) if sharing.enabled else 0)
        active_seeds = int(sharing.active_seed_slots or 0)
        pause_library_seeds = bool(sharing.enabled and sharing.pause_when_downloading)
        current_policy = (down, up, library_up, active_seeds, pause_library_seeds)

        if current_policy != self._current_limit:
            limits = {}
            # UI/settings labels are KB/s (kilobytes per second).
            # libtorrent expects bytes per second.  Older builds treated these
            # values as kilobits and divided by 8, so a user-entered 50 KB/s
            # upload cap became ~6 KB/s, which can make healthy public torrents
            # crawl because peers deprioritize us.
            limits["download_rate_limit"] = (int(down) * 1024) if down else 0
            limits["upload_rate_limit"] = (int(up) * 1024) if up else 0
            limits["library_seed_upload_rate_limit"] = library_up * 1024
            limits["active_seeds"] = active_seeds
            limits["pause_library_seeds_when_downloading"] = 1 if pause_library_seeds else 0

            await self._engine.apply_settings(limits)
            self._current_limit = current_policy
            
            mode = f"Schedule: {getattr(active_schedule, 'name', 'active window')}" if active_schedule else "Default Profile"
            logger.info(
                f"Bandwidth Throttling: {mode} -> "
                f"Down: {down or 'Uncapped'} KB/s, "
                f"Download Up: {up or 'Uncapped'} KB/s, "
                f"Library Seed Up: {library_up or 'Uncapped'} KB/s"
                f"{' (paused while downloading)' if pause_library_seeds else ''}"
            )
