"""
Download action handlers for LJS.

Provides DownloadsActionHandler: the single place for download
mutation logic invoked via ActionGateway from UI endpoints.
"""

from loguru import logger

from src.core.downloader import DownloadManager
from src.core.models import DownloadPriority


class DownloadsActionHandler:
    """Handlers for download management actions routed through ActionGateway.

    Each method receives keyword arguments from ActionCommand.arguments
    and returns a dict wrapped into ActionResult.data.

    Dependencies (injected at composition root):
        downloader — DownloadManager (add magnet, manage downloads)
    """

    def __init__(self, downloader: DownloadManager) -> None:
        self._downloader = downloader

    async def upload(self, magnet: str, item_name: str = "Manual Upload") -> dict:
        """Add a download via manual magnet link."""
        item = await self._downloader.add_magnet(
            magnet_link=magnet,
            item_name=item_name,
            priority=DownloadPriority.HIGH,
            reason="Manual User Upload",
        )
        return {"status": "added", "download_id": item.id}

    async def pause_downloads(self, download_ids: list[str]) -> dict:
        """Pause multiple downloads by ID."""
        succeeded: list[str] = []
        failed: list[str] = []
        for dl_id in download_ids:
            try:
                result = await self._downloader.pause_download(dl_id)
                if result is not None:
                    succeeded.append(dl_id)
                else:
                    failed.append(dl_id)
            except Exception as exc:
                logger.debug(f'Batch pause failed for {dl_id}: {exc}')
                failed.append(dl_id)
        return {
            "succeeded": succeeded,
            "failed": failed,
            "total": len(download_ids),
        }

    async def resume_downloads(self, download_ids: list[str]) -> dict:
        """Resume multiple paused downloads by ID."""
        succeeded: list[str] = []
        failed: list[str] = []
        for dl_id in download_ids:
            try:
                result = await self._downloader.resume_download(dl_id)
                if result is not None:
                    succeeded.append(dl_id)
                else:
                    failed.append(dl_id)
            except Exception as exc:
                logger.debug(f'Batch resume failed for {dl_id}: {exc}')
                failed.append(dl_id)
        return {
            "succeeded": succeeded,
            "failed": failed,
            "total": len(download_ids),
        }

    async def cancel_downloads(self, download_ids: list[str]) -> dict:
        """Cancel multiple downloads by ID."""
        succeeded: list[str] = []
        failed: list[str] = []
        for dl_id in download_ids:
            try:
                await self._downloader.cancel_download(dl_id)
                succeeded.append(dl_id)
            except Exception as exc:
                logger.debug(f'Batch cancel failed for {dl_id}: {exc}')
                failed.append(dl_id)
        return {
            "succeeded": succeeded,
            "failed": failed,
            "total": len(download_ids),
        }
