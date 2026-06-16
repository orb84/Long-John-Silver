"""
Libtorrent engine wrapper for LJS.
Handles the low-level libtorrent session and torrent handles.
"""

import asyncio
import re
import time
from collections import Counter
from pathlib import Path
from loguru import logger
from typing import Optional

from src.core.storage_path_availability import StoragePathGuard


class TorrentEngine:
    """Wrapper for the libtorrent session and low-level operations.

    The engine owns the only libtorrent session in the application.  Higher
    layers should treat it as the authority for session-wide settings, torrent
    handle lifecycle, and global transfer caps.  Do not set per-handle bandwidth
    limits directly from UI/action code; call :meth:`apply_settings` instead so
    the global and per-handle fallback limiters stay in sync.
    """

    def __init__(self, download_dir: str, max_concurrent: int = 3):
        """Create an engine for a staging directory.

        Args:
            download_dir: Directory passed to libtorrent as the save path.
            max_concurrent: Initial active-download limit mirrored into
                libtorrent session settings.
        """
        self._download_dir = download_dir
        self._max_concurrent = max_concurrent
        self._session = None
        self._alert_task: asyncio.Task | None = None
        self._handles: dict[str, object] = {}
        self._handle_modes: dict[str, str] = {}
        self._alert_last_log: dict[str, float] = {}
        self._alert_suppressed: Counter[str] = Counter()
        self._alert_log_interval_seconds = 120.0
        self._rate_limits: dict[str, int] = {
            "download_rate_limit": 0,
            "upload_rate_limit": 0,
            "library_seed_upload_rate_limit": 0,
            # Boolean flag encoded as 0/1.  It deliberately lives beside the
            # raw byte caps because rate rebalancing is the single place that
            # knows whether active downloads and library seeds are both present.
            "pause_library_seeds_when_downloading": 0,
        }
        availability = StoragePathGuard.try_prepare_directory(download_dir)
        if not availability.available_for_writes:
            logger.warning(f"Torrent download directory is unavailable at startup: {availability.reason}")

    @property
    def download_dir(self) -> str:
        """Return the configured default download directory."""
        return self._download_dir

    async def initialize(self) -> None:
        """Initialize the libtorrent session."""
        self._session = await asyncio.to_thread(self._create_session)
        self._alert_task = asyncio.create_task(self._alert_loop(), name="torrent_engine_alerts")
        logger.info("Torrent engine initialized.")

    def _create_session(self):
        """Create a libtorrent session with desktop-client-like settings."""
        import libtorrent as lt
        ses = lt.session()
        settings = ses.get_settings()

        def set_opt(name: str, value) -> None:
            """Set a libtorrent option only when the installed build exposes it."""
            if name in settings:
                settings[name] = value
            else:
                logger.debug(f"libtorrent setting unavailable on this build: {name}")

        # Use a standard BT port but allow libtorrent to fall back if another
        # client already owns it.  A hard 6881 bind can make LJS effectively
        # firewalled when the user's faster desktop client is also running.
        set_opt("listen_interfaces", "0.0.0.0:6881,[::]:6881")
        set_opt("listen_system_port_fallback", True)
        set_opt("cache_size", 4096)  # 64 MB on 16 KiB blocks
        set_opt("cache_expiry", 60)
        set_opt("active_downloads", self._max_concurrent)
        set_opt("active_limit", max(self._max_concurrent * 3, self._max_concurrent))
        set_opt("active_seeds", 2)
        set_opt("active_checking", 1)
        set_opt("connections_limit", 600)
        set_opt("connection_speed", 120)
        set_opt("half_open_limit", 80)
        set_opt("peer_connect_timeout", 12)
        set_opt("request_timeout", 35)
        set_opt("piece_timeout", 45)
        set_opt("max_queued_disk_bytes", 32 * 1024 * 1024)
        set_opt("send_buffer_watermark", 2 * 1024 * 1024)
        set_opt("send_buffer_low_watermark", 512 * 1024)
        # Match normal desktop clients: use all discovery transports, both TCP
        # and uTP, and announce broadly instead of waiting for tracker tier order.
        set_opt("enable_incoming_tcp", True)
        set_opt("enable_outgoing_tcp", True)
        set_opt("enable_incoming_utp", True)
        set_opt("enable_outgoing_utp", True)
        set_opt("enable_upnp", True)
        set_opt("enable_natpmp", True)
        set_opt("enable_lsd", True)
        set_opt("enable_dht", True)
        set_opt("announce_to_all_trackers", True)
        set_opt("announce_to_all_tiers", True)
        set_opt("prefer_udp_trackers", True)
        set_opt("auto_manage_startup", 30)
        set_opt("unchoke_slots_limit", -1)
        set_opt("max_peerlist_size", 10000)

        ses.apply_settings(settings)
        return ses

    async def _alert_loop(self) -> None:
        """Log useful libtorrent alerts for tracker/peer diagnostics.

        The UI often only shows rates and peer counts. When torrents do not get
        metadata, trackers reject announces, or listen-port mapping fails, those
        details live in libtorrent alerts. Keeping a lightweight pump makes user
        logs actionable without flooding normal output.
        """
        while self._session is not None:
            try:
                alerts = await asyncio.to_thread(self._session.pop_alerts)
                for alert in alerts or []:
                    msg = str(alert.message() if hasattr(alert, "message") else alert)
                    lower = msg.lower()
                    important = any(token in lower for token in (
                        "tracker", "dht", "metadata", "listen", "portmap",
                        "error", "fail", "timed out", "connect", "peer",
                    ))
                    if not important:
                        continue
                    self._log_alert(msg)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug(f"Torrent alert loop error: {exc}")
            await asyncio.sleep(5)


    def _log_alert(self, msg: str) -> None:
        """Log libtorrent alerts without flooding logs with tracker noise."""
        key, level = self._classify_alert(msg)
        now = time.monotonic()
        last = self._alert_last_log.get(key, 0.0)
        if now - last < self._alert_log_interval_seconds:
            self._alert_suppressed[key] += 1
            return
        suppressed = self._alert_suppressed.pop(key, 0)
        suffix = f" (suppressed {suppressed} similar alerts)" if suppressed else ""
        if level == "warning":
            logger.warning(f"Torrent alert: {msg}{suffix}")
        else:
            logger.debug(f"Torrent alert: {msg}{suffix}")
        self._alert_last_log[key] = now

    @staticmethod
    def _classify_alert(msg: str) -> tuple[str, str]:
        """Return a coalescing key and log level for a libtorrent alert."""
        lower = msg.lower()
        if "local service discovery" in lower or "lsd" in lower:
            return "lsd", "debug"
        if "skipping tracker announce" in lower:
            return "tracker_announce_skipped", "debug"
        if "timed out" in lower:
            return "tracker_timeout", "debug"
        if "host not found" in lower:
            return "tracker_host_not_found", "debug"
        if "connection refused" in lower or "unreachable" in lower:
            return "tracker_unreachable", "debug"
        if "unspecified system error" in lower:
            return "tracker_unspecified_system_error", "debug"
        if re.search(r"\b[45]\d\d\b", lower):
            return "tracker_http_error", "debug"
        if any(token in lower for token in ("metadata", "file", "disk", "i/o", "save resume")) and any(token in lower for token in ("error", "fail")):
            return "torrent_storage_or_metadata_error", "warning"
        if any(token in lower for token in ("error", "fail")):
            return "torrent_error", "warning"
        return "torrent_info", "debug"

    async def add_magnet(
        self,
        magnet_link: str,
        download_id: str,
        save_path: str | None = None,
        mode: str = "download",
    ) -> object:
        """Add a magnet link to the session.

        Args:
            magnet_link: Magnet URI to add.
            download_id: Stable application download identifier.
            save_path: Optional per-torrent save root. Seed-in-place library
                sharing uses this to place payloads directly under category
                library roots.
            mode: Bandwidth class, either ``download`` or ``library_seed``.

        Returns:
            The libtorrent torrent handle.
        """
        resolved_save_path = str(save_path or self._download_dir)
        StoragePathGuard.ensure_directory(resolved_save_path)
        import libtorrent as lt
        params = {
            "save_path": resolved_save_path,
            "storage_mode": lt.storage_mode_t.storage_mode_sparse,
        }
        handle = await asyncio.to_thread(
            lt.add_magnet_uri, self._session, magnet_link, params
        )
        self._handles[download_id] = handle
        self._handle_modes[download_id] = mode or "download"
        await self.rebalance_rate_limits()
        return handle

    async def remove_torrent(self, download_id: str) -> None:
        """Remove a torrent from the session and rebalance bandwidth caps."""
        handle = self._handles.pop(download_id, None)
        self._handle_modes.pop(download_id, None)
        if handle and self._session:
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(self._session.remove_torrent, handle),
                    timeout=3.0,
                )
            except asyncio.TimeoutError:
                logger.warning(f"Timeout removing torrent {download_id} from libtorrent session")
            except Exception as e:
                logger.warning(f"Failed to remove torrent {download_id}: {e}")
        await self.rebalance_rate_limits()

    async def apply_settings(self, settings_dict: dict) -> None:
        """Apply raw settings to the libtorrent session.

        ``download_rate_limit`` and ``upload_rate_limit`` are treated as global
        aggregate caps in bytes per second.  In addition to libtorrent's session
        cap, LJS applies a conservative per-active-handle split as a fallback.
        That prevents two active torrents from each using the full UI-entered
        upload cap on libtorrent builds/platforms where the session cap lags or
        reports protocol overhead differently.
        """
        if not self._session:
            return
        settings = self._session.get_settings()
        for key, value in settings_dict.items():
            if key in self._rate_limits:
                if key == "pause_library_seeds_when_downloading":
                    self._rate_limits[key] = 1 if value else 0
                else:
                    self._rate_limits[key] = max(0, int(value or 0))
                continue
            if key in settings:
                settings[key] = value
        # Session-wide bandwidth caps are applied from rebalance_rate_limits(),
        # not directly here, because the correct aggregate upload cap depends on
        # which transfer classes are currently active.  For example, if download
        # uploads are uncapped but library seeding is capped, a session upload cap
        # would incorrectly throttle active downloads; only the per-handle seed
        # fallback should apply in that case.
        await asyncio.to_thread(self._session.apply_settings, settings)
        await self.rebalance_rate_limits()

    async def rebalance_rate_limits(self) -> None:
        """Reapply per-handle fallback caps for the current active handle set.

        This method is cheap and safe to call after pause/resume/add/remove
        transitions.  It preserves unlimited mode by setting per-handle limits
        back to zero when the corresponding global cap is zero.
        """
        await asyncio.to_thread(self._rebalance_rate_limits_sync)

    def _rebalance_rate_limits_sync(self) -> None:
        """Synchronously split caps by transfer class.

        Downloading torrents share the regular download/upload quota. Library
        seed-in-place torrents share the dedicated library seed upload quota so
        users can keep seeding without starving active downloads.
        """
        active_items = [
            (download_id, handle)
            for download_id, handle in self._handles.items()
            if self._handle_is_active(handle)
        ]
        download_items = [
            (download_id, handle) for download_id, handle in active_items
            if self._handle_modes.get(download_id, "download") != "library_seed"
        ]
        seed_items = [
            (download_id, handle) for download_id, handle in active_items
            if self._handle_modes.get(download_id, "download") == "library_seed"
        ]
        down_cap = int(self._rate_limits.get("download_rate_limit") or 0)
        download_up_cap = int(self._rate_limits.get("upload_rate_limit") or 0)
        seed_up_cap = int(self._rate_limits.get("library_seed_upload_rate_limit") or 0)
        pause_seeds = bool(self._rate_limits.get("pause_library_seeds_when_downloading")) and bool(download_items)
        effective_seed_up_cap = 1 if pause_seeds else seed_up_cap
        per_down = self._split_cap(down_cap, len(download_items))
        per_download_up = self._split_cap(download_up_cap, len(download_items))
        per_seed_up = self._split_cap(effective_seed_up_cap, len(seed_items))

        self._apply_session_rate_caps(download_items, seed_items, down_cap, download_up_cap, seed_up_cap, pause_seeds)

        for download_id, handle in self._handles.items():
            try:
                active = self._handle_is_active(handle)
                mode = self._handle_modes.get(download_id, "download")
                if hasattr(handle, "set_download_limit"):
                    handle.set_download_limit(per_down if active and mode != "library_seed" else 0)
                if hasattr(handle, "set_upload_limit"):
                    if not active:
                        handle.set_upload_limit(0)
                    elif mode == "library_seed":
                        handle.set_upload_limit(per_seed_up)
                    else:
                        handle.set_upload_limit(per_download_up)
            except Exception as exc:
                logger.debug(f"Could not apply per-handle torrent cap: {exc}")
        if active_items and (down_cap or download_up_cap or seed_up_cap or pause_seeds):
            seed_display = "paused" if pause_seeds else (per_seed_up or "∞")
            logger.debug(
                "Bandwidth cap rebalanced: "
                f"downloads={len(download_items)} down={per_down or '∞'} B/s up={per_download_up or '∞'} B/s; "
                f"library_seeds={len(seed_items)} up={seed_display} B/s"
            )

    def _apply_session_rate_caps(
        self,
        download_items: list[tuple[str, object]],
        seed_items: list[tuple[str, object]],
        down_cap: int,
        download_up_cap: int,
        seed_up_cap: int,
        pause_seeds: bool,
    ) -> None:
        """Apply safe session aggregate caps for the active transfer mix.

        Libtorrent's session caps are global, while LJS exposes two user-facing
        upload budgets: one for active downloads and one for completed library
        seeds.  A global session cap can only be used when every active upload
        class is capped.  If any active class is intentionally unlimited, the
        session cap must remain unlimited and the class-specific per-handle
        fallback limits do the enforcement.
        """
        if not self._session:
            return
        settings = self._session.get_settings()

        if "download_rate_limit" in settings:
            settings["download_rate_limit"] = down_cap if download_items and down_cap > 0 else 0

        active_upload_caps: list[int] = []
        if download_items:
            active_upload_caps.append(download_up_cap)
        if seed_items and not pause_seeds:
            active_upload_caps.append(seed_up_cap)
        # A zero cap means "unlimited" in libtorrent.  Only set a session upload
        # cap when every active upload class has a non-zero user cap; otherwise a
        # session cap would accidentally throttle the unlimited class.
        if "upload_rate_limit" in settings:
            if active_upload_caps and all(cap > 0 for cap in active_upload_caps):
                settings["upload_rate_limit"] = sum(active_upload_caps)
            else:
                settings["upload_rate_limit"] = 0

        self._session.apply_settings(settings)

    @staticmethod
    def _split_cap(cap: int, count: int) -> int:
        """Split an aggregate cap over a class of active handles."""
        if not cap:
            return 0
        per_handle = cap // max(1, int(count or 1))
        return max(1, per_handle)

    def _handle_is_active(self, handle: object) -> bool:
        """Return whether a torrent handle should receive a share of bandwidth."""
        try:
            if hasattr(handle, "is_valid") and not handle.is_valid():
                return False
            status = handle.status() if hasattr(handle, "status") else None
            if status is not None and getattr(status, "paused", False):
                return False
            return True
        except Exception:
            return False

    async def set_max_concurrent(self, max_concurrent: int) -> None:
        """Update libtorrent active-download limits at runtime."""
        self._max_concurrent = max(1, int(max_concurrent or 1))
        await self.apply_settings({
            "active_downloads": self._max_concurrent,
            "active_limit": max(self._max_concurrent * 2, self._max_concurrent),
        })
        logger.info(f"Torrent engine concurrency limit set to {self._max_concurrent}")

    async def mark_handle_mode(self, download_id: str, mode: str) -> None:
        """Move a torrent handle into another bandwidth class.

        Args:
            download_id: Application download identifier.
            mode: ``download`` for active transfers or ``library_seed`` for
                opt-in seed-in-place library sharing.
        """
        if download_id in self._handles:
            self._handle_modes[download_id] = mode or "download"
            await self.rebalance_rate_limits()

    def get_handle(self, download_id: str) -> Optional[object]:
        """Get a torrent handle by its ID."""
        return self._handles.get(download_id)

    async def close(self) -> None:
        """Shut down the session and remove all torrents concurrently.

        Uses per-handle timeouts to prevent blocking shutdown.
        Removes all torrents in parallel rather than sequentially.
        """
        if not self._session:
            return

        if self._alert_task:
            self._alert_task.cancel()
            try:
                await self._alert_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            self._alert_task = None

        handles = list(self._handles.values())
        self._handles.clear()
        self._handle_modes.clear()

        async def _remove_one(h: object) -> None:
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(self._session.remove_torrent, h),
                    timeout=3.0,
                )
            except Exception:
                pass

        if handles:
            await asyncio.gather(*[_remove_one(h) for h in handles], return_exceptions=True)

        self._session = None
        logger.info('Torrent engine shut down.')
