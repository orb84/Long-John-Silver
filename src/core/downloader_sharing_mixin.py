"""Download manager mixin for seed-in-place sharing and transfer caps.

The main DownloadManager remains the orchestrator, while this mixin owns the
policy-heavy logic introduced by library sharing: separate upload quotas,
share-visible torrent queries, and category-root placement for seed-in-place
payloads. Keeping this behavior here prevents the manager from regrowing into a
monolith while still allowing the queue loop to call ``_start_download`` through
normal inheritance.
"""

from __future__ import annotations

import re
from pathlib import Path

from src.core.categories.identity import clean_display_title

from loguru import logger

from src.core.models import DownloadItem, DownloadStatus, QualityProfile


class DownloadSharingMixin:
    """Adds sharing-aware start, listing, and bandwidth behavior to downloads.

    The mixin expects the concrete class to provide the DownloadManager
    collaborators ``_db``, ``_deps``, ``_download_dir``, ``_engine``,
    ``_settings_manager``, ``_seed_policy``, and ``_start_coordinator``.
    Subclasses should not bypass these methods when adding future sharing modes;
    instead extend this mixin or delegate to a new sharing service so the core
    manager stays focused on orchestration.
    """

    async def apply_speed_limits(self, quality: QualityProfile) -> None:
        """Apply aggregate transfer limits for downloads and library seeds.

        Args:
            quality: Effective quality/download profile containing normal
                download and active-download upload caps.

        ``quality.max_upload_speed_kbps`` is the upload quota for active
        downloading torrents. ``settings.sharing.library_upload_speed_kbps`` is
        a separate opt-in library-seeding quota. Both are enforced by the
        torrent engine as per-class aggregate caps.
        """
        sharing = self._settings_manager.settings.sharing
        limits = {
            "download_rate_limit": int(quality.max_download_speed_kbps or 0) * 1024,
            "upload_rate_limit": int(quality.max_upload_speed_kbps or 0) * 1024,
            "library_seed_upload_rate_limit": int(sharing.library_upload_speed_kbps or 0) * 1024,
            "active_seeds": int(sharing.active_seed_slots or 0),
            "pause_library_seeds_when_downloading": 1 if sharing.pause_when_downloading else 0,
        }
        await self._engine.apply_settings(limits)
        self._seed_policy.update_limits(
            seed_ratio_target=sharing.seed_ratio_target if sharing.enabled else quality.seed_ratio_target,
            seed_duration_hours=sharing.seed_duration_hours if sharing.enabled else quality.seed_duration_hours,
        )
        logger.info(f"Global speed limits applied: {limits}")

    async def apply_sharing_settings(self) -> None:
        """Apply current sharing settings to the running torrent engine.

        Use this after Compass or first-run setup edits sharing options. It
        reuses the existing speed-limit path so download and sharing quotas stay
        synchronized.
        """
        await self.apply_speed_limits(self._settings_manager.settings.default_quality)

    async def get_library_shares(self) -> list[DownloadItem]:
        """Return torrent-backed library files currently eligible for sharing.

        Returns:
            Active download rows that represent seed-in-place library payloads,
            including paused/stalled rows so the sharing view can explain why an
            expected item is not uploading right now.
        """
        rows = await self._db.downloads.get_active_downloads()
        visible_statuses = {
            DownloadStatus.SEEDING,
            DownloadStatus.DOWNLOADING,
            DownloadStatus.QUEUED,
            DownloadStatus.PAUSED,
            DownloadStatus.STALLED,
        }
        return [item for item in rows if item.sharing_enabled and item.status in visible_statuses]

    async def _start_download(self, item: DownloadItem) -> None:
        """Start a download, preparing seed-in-place placement when enabled.

        The torrent starts in normal download bandwidth mode. Once it reaches
        seeding, the lifecycle monitor marks the handle as ``library_seed`` so
        the separate library upload quota applies.
        """
        save_path, sharing_enabled = self._resolve_torrent_save_path(item)
        item.save_path = str(save_path)
        item.sharing_enabled = bool(sharing_enabled)
        await self._db.downloads.upsert_download(item)
        selective = self._start_coordinator.pop_selective(item.id)
        await self._start_coordinator.start_download(
            item,
            selective=selective,
            save_path=str(save_path),
            sharing_enabled=sharing_enabled,
        )

    def _resolve_torrent_save_path(self, item: DownloadItem) -> tuple[Path, bool]:
        """Resolve the libtorrent save path for a queued item.

        Args:
            item: Download row about to be started.

        Returns:
            ``(save_path, sharing_enabled)``. Seed-in-place downloads are
            written below the category library root in a stable item/season
            folder so the torrent payload itself becomes the library copy.
            Non-sharing downloads continue to use the staging folder.
        """
        settings = self._settings_manager.settings
        sharing = settings.sharing
        staging = Path(self._download_dir).resolve()
        if not sharing.category_enabled(item.category_id):
            return staging, False
        category = self._deps.category_registry.get(item.category_id) if self._deps.category_registry and item.category_id else None
        if category is None:
            logger.warning(f"Seed-in-place requested but no category found for {item.category_id}; using staging folder")
            return staging, False
        if hasattr(category, "sharing_save_path_for_item"):
            try:
                return category.sharing_save_path_for_item(item, settings, staging)
            except Exception as exc:
                logger.warning(f"Seed-in-place category save-path planning failed for {item.item_name}: {exc}")
                return staging, False
        try:
            root = Path(category.get_root_path(settings)).resolve()
        except Exception as exc:
            logger.warning(f"Seed-in-place requested but category root is unavailable for {item.item_name}: {exc}")
            return staging, False
        context = getattr(item, "import_context", None)
        title = getattr(context, "planning_title", None) or item.item_name or item.torrent_title or item.id
        safe_title = self._safe_folder_name(clean_display_title(title))
        return (root / safe_title).resolve(), True

    @staticmethod
    def _safe_folder_name(value: str) -> str:
        """Return a filesystem-safe display folder for seed-in-place roots.

        Args:
            value: Human-readable media or torrent title.

        Returns:
            A short path segment that keeps useful release/title details while
            stripping characters that commonly break cross-platform paths.
        """
        cleaned = re.sub(r"[^A-Za-z0-9._() \[\]-]+", " ", str(value or "")).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned[:120] or "Untitled"
