"""Partial-download filename repair services.

The torrent engine uses a temporary ``.downloading`` suffix so users and media
scanners do not mistake unfinished payloads for playable files.  Older lifecycle
paths stripped that suffix during pause/shutdown.  This module keeps the repair
logic outside ``DownloadManager`` so the manager remains a coordinator rather
than a filesystem-recovery monolith.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from src.core.models import DownloadStatus


class PartialDownloadMarkerRepairService:
    """Restores ``.downloading`` markers on incomplete persisted downloads.

    The service is intentionally conservative: it only touches active rows that
    are not complete, only renames files described by persisted download-file
    metadata, and never removes a suffix.  Maintain future lifecycle changes
    around this invariant so paused files cannot look complete in Plex/Finder.
    """

    def __init__(self, database: object, download_dir: str | Path) -> None:
        """Create the repair service.

        Args:
            database: Database facade exposing ``downloads.get_active_downloads``.
            download_dir: Global download root used as a fallback save root.
        """
        self._db = database
        self._download_dir = Path(download_dir)

    async def repair(self) -> None:
        """Reapply ``.downloading`` to incomplete payloads that lost it.

        This is a defensive startup repair for rows left paused/queued/stalled
        by older versions that stripped the temporary suffix during pause or
        shutdown.  It uses persisted file metadata and save roots, so it can fix
        items even when no libtorrent handle is currently loaded.
        """
        try:
            rows = await self._db.downloads.get_active_downloads()
        except Exception:
            return
        for item in rows:
            await self._repair_item(item)

    async def _repair_item(self, item: object) -> None:
        """Repair one persisted download row when it is incomplete."""
        if getattr(item, "status", None) not in (
            DownloadStatus.PAUSED,
            DownloadStatus.QUEUED,
            DownloadStatus.STALLED,
            DownloadStatus.DOWNLOADING,
        ):
            return
        if float(getattr(item, "progress", 0.0) or 0.0) >= 0.995:
            return
        roots = self._roots_for_item(item)
        for download_file in getattr(item, "files", []) or []:
            self._repair_file_marker(download_file, roots)

    def _roots_for_item(self, item: object) -> list[Path]:
        """Return candidate roots for an item's persisted file paths."""
        roots: list[Path] = []
        save_path = getattr(item, "save_path", "")
        if save_path:
            roots.append(Path(save_path))
        roots.append(self._download_dir)
        return roots

    def _repair_file_marker(self, download_file: object, roots: list[Path]) -> None:
        """Restore the marker for one incomplete file when the bare name exists."""
        if getattr(download_file, "status", None) in ("complete", "organized"):
            return
        size = int(getattr(download_file, "size", 0) or 0)
        downloaded = int(getattr(download_file, "downloaded_bytes", 0) or 0)
        if size > 0 and downloaded >= size:
            return
        rel = str(getattr(download_file, "file_path", "") or "")
        if not rel or rel.endswith(".downloading"):
            return
        for root in roots:
            source = (root / rel).resolve()
            target = Path(str(source) + ".downloading")
            try:
                if source.exists() and not target.exists():
                    source.rename(target)
                    logger.info(f"Restored partial-file marker: {target}")
                    break
            except Exception as exc:
                logger.debug(f"Could not restore partial-file marker for {source}: {exc}")
