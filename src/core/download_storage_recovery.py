"""Recovery loop for downloads held because storage was temporarily unavailable."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from loguru import logger

from src.core.models import DownloadItem, DownloadStatus
from src.core.storage_path_availability import StoragePathGuard, StoragePathUnavailableError


class StorageUnavailableDownloadReason:
    """Consistent marker for downloads parked by missing/unavailable storage."""

    PREFIX = "Storage unavailable:"
    RECOVERED_MARKER = "storage reconnected; queued for restart"

    @classmethod
    def from_error(cls, error: StoragePathUnavailableError | Exception) -> str:
        """Return a persisted reason for a storage-unavailable start failure."""
        return f"{cls.PREFIX} {error}"

    @classmethod
    def is_storage_unavailable(cls, reason: str | None) -> bool:
        """Return whether a stalled download is waiting for storage recovery."""
        text = str(reason or "")
        return cls.PREFIX.lower() in text.lower()

    @classmethod
    def recovered_reason(cls, existing_reason: str | None) -> str:
        """Append a compact recovery note without duplicating it forever."""
        text = str(existing_reason or "").strip()
        if cls.RECOVERED_MARKER in text:
            return text
        return f"{text}; {cls.RECOVERED_MARKER}" if text else cls.RECOVERED_MARKER


class DownloadStorageRecoveryService:
    """Poll storage paths and requeue downloads once a missing drive returns."""

    def __init__(self, db: object, download_dir: str, poll_seconds: float = 10.0) -> None:
        """Initialize the storage recovery service.

        Args:
            db: Database facade exposing ``downloads`` repository methods.
            download_dir: Default download staging directory.
            poll_seconds: Delay between availability checks.
        """
        self._db = db
        self._download_dir = download_dir
        self._poll_seconds = max(2.0, float(poll_seconds or 10.0))
        self._last_unavailable: dict[str, str] = {}

    async def run_loop(self, on_recovered: Callable[[], Awaitable[None]] | None = None) -> None:
        """Run a supervised recovery loop until cancelled."""
        try:
            while True:
                recovered = await self.recover_once()
                if recovered and on_recovered is not None:
                    try:
                        await on_recovered()
                    except Exception as exc:
                        logger.warning(f"Storage recovery callback failed after requeueing downloads: {exc}")
                await asyncio.sleep(self._poll_seconds)
        except asyncio.CancelledError:
            raise

    async def recover_once(self) -> int:
        """Requeue storage-stalled downloads whose target path is writable again."""
        recovered = 0
        for item in await self._storage_stalled_downloads():
            target = self._target_path_for_item(item)
            availability = StoragePathGuard.try_prepare_directory(target)
            if not availability.available_for_writes:
                self._remember_unavailable(item, availability.reason)
                continue
            item.status = DownloadStatus.QUEUED
            item.reason = StorageUnavailableDownloadReason.recovered_reason(item.reason)
            item.download_rate = 0.0
            item.upload_rate = 0.0
            item.eta_seconds = 0.0
            item.num_peers = 0
            item.num_seeds = 0
            await self._db.downloads.upsert_download(item)
            recovered += 1
            logger.info(
                f"Storage target is available again; requeued download {item.id} "
                f"({item.item_name}) for normal queue processing at {availability.path}."
            )
        return recovered

    async def _storage_stalled_downloads(self) -> list[DownloadItem]:
        """Return active stalled downloads that were parked by storage failure."""
        rows = await self._db.downloads.get_active_downloads()
        return [
            item for item in rows
            if item.status == DownloadStatus.STALLED
            and StorageUnavailableDownloadReason.is_storage_unavailable(item.reason)
        ]

    def _target_path_for_item(self, item: DownloadItem) -> Path:
        """Return the save root that must exist before this item can restart."""
        return Path(item.save_path or self._download_dir).expanduser()

    def _remember_unavailable(self, item: DownloadItem, reason: str) -> None:
        """Throttle repeated logs while a storage-stalled item remains unavailable."""
        previous = self._last_unavailable.get(item.id)
        if previous == reason:
            return
        self._last_unavailable[item.id] = reason
        logger.warning(f"Download {item.id} remains held for unavailable storage: {reason}")
