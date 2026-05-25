"""Seed-in-place library sharing service.

This module keeps the library-sharing read model outside the downloader. The
DownloadManager owns libtorrent lifecycle; LibrarySharingService translates the
active torrent rows and user sharing settings into UI-ready sharing status.
"""

from __future__ import annotations

from typing import Any

from src.core.models import DownloadStatus


class LibrarySharingService:
    """Builds status snapshots for torrent-backed library sharing.

    Use this service from web routers or future LLM tools when the caller needs
    to understand what library files are being shared. Mutations should still go
    through dedicated settings/download actions so torrent handles remain owned
    by the download subsystem.
    """

    def __init__(self, downloader: Any, settings_manager: Any) -> None:
        """Create the service with downloader and settings dependencies.

        Args:
            downloader: DownloadManager-like object exposing
                ``get_library_shares``.
            settings_manager: SettingsManager-like object exposing current
                ``settings``.
        """
        self._downloader = downloader
        self._settings_manager = settings_manager

    async def snapshot(self) -> dict[str, Any]:
        """Return aggregate sharing status and per-item rows.

        Returns:
            Dict with ``policy``, ``summary``, and ``items`` keys suitable for
            JSON responses and chat-tool reporting.
        """
        settings = self._settings_manager.settings
        sharing = settings.sharing
        rows = await self._downloader.get_library_shares()
        items = [self._item(row) for row in rows]
        return {
            "policy": sharing.model_dump(),
            "summary": {
                "enabled": sharing.enabled,
                "mode": sharing.mode,
                "active_seed_slots": sharing.active_seed_slots,
                "library_upload_speed_kbps": sharing.library_upload_speed_kbps or 0,
                "sharing_count": len(items),
                "active_upload_bps": sum(int(item.get("upload_rate", 0) or 0) for item in items),
                "uploaded_bytes": sum(int(item.get("uploaded_bytes", 0) or 0) for item in items),
            },
            "items": items,
        }

    def _item(self, item: Any) -> dict[str, Any]:
        """Convert a DownloadItem into a library sharing row."""
        status = item.status.value if hasattr(item.status, "value") else str(item.status)
        active = item.status == DownloadStatus.SEEDING or status == "seeding"
        return {
            "id": item.id,
            "category_id": item.category_id,
            "item_id": item.item_id,
            "item_name": item.item_name,
            "torrent_title": item.torrent_title or item.item_name,
            "status": status,
            "active": active,
            "season": item.season,
            "episode": item.episode,
            "save_path": item.save_path,
            "file_path": item.file_path,
            "total_size": item.total_size,
            "downloaded_bytes": item.downloaded_bytes,
            "uploaded_bytes": item.uploaded_bytes,
            "seed_ratio": item.seed_ratio,
            "upload_rate": item.upload_rate,
            "num_peers": item.num_peers,
            "num_seeds": item.num_seeds,
            "completed_at": item.completed_at,
            "files": [f.model_dump() for f in (item.files or [])],
        }
