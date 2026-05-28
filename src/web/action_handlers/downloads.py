"""
Download action handlers for LJS.

Provides DownloadsActionHandler: the single place for download mutation logic
invoked via ActionGateway from UI endpoints.  It routes torrent/libtorrent rows
to DownloadManager and Soulseek/slskd rows to slskd's transfer API instead of
pretending every visible download card is a torrent.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from src.core.models import DownloadPriority
from src.integrations.slskd_client import SlskdClient
from src.integrations.slskd_transfer_view import SlskdTransferReadModel


def _is_soulseek_id(download_id: str) -> bool:
    return str(download_id or "").startswith("slskd:")


class DownloadsActionHandler:
    """Handlers for download management actions routed through ActionGateway."""

    def __init__(self, downloader: Any, settings_manager: Any | None = None, database: Any | None = None) -> None:
        self._downloader = downloader
        self._settings_manager = settings_manager
        self._database = database

    async def upload(self, magnet: str, item_name: str = "Manual Upload") -> dict:
        """Add a download via manual magnet link."""
        item = await self._downloader.add_magnet(
            magnet_link=magnet,
            item_name=item_name,
            priority=DownloadPriority.HIGH,
            reason="Manual User Upload",
        )
        return {"status": "added", "download_id": item.id}

    async def pause_download(self, download_id: str) -> dict:
        """Pause a torrent download; Soulseek transfers can only be cancelled/removed."""
        if _is_soulseek_id(download_id):
            return {"error": "Soulseek/slskd transfers cannot be paused through the API. Use Cancel/Remove to stop and clear the transfer."}
        result = await self._downloader.pause_download(download_id)
        return result.model_dump() if hasattr(result, "model_dump") else (result or {})

    async def resume_download(self, download_id: str) -> dict:
        """Resume a torrent download; slskd does not expose resume for queued rows."""
        if _is_soulseek_id(download_id):
            return {"error": "Soulseek/slskd transfers cannot be resumed from LJS yet; choose a fresh Soulseek candidate if needed."}
        result = await self._downloader.resume_download(download_id)
        return result.model_dump() if hasattr(result, "model_dump") else (result or {})

    async def set_priority(self, download_id: str, priority: DownloadPriority | str | None = None) -> dict:
        """Change torrent priority; keep Soulseek priority read-only."""
        if _is_soulseek_id(download_id):
            return {"error": "Soulseek/slskd transfer priority is managed by slskd and the remote user's queue."}
        if priority is None:
            priority = DownloadPriority.NORMAL
        result = await self._downloader.set_priority(download_id, priority)
        return result.model_dump() if hasattr(result, "model_dump") else (result or {})

    async def restart_download(self, download_id: str) -> dict:
        """Restart a failed/cancelled torrent; Soulseek requires a fresh queue action."""
        if _is_soulseek_id(download_id):
            return {"error": "Soulseek/slskd transfers cannot be restarted from the download card. Search/queue the Soulseek candidate again."}
        result = await self._downloader.restart_download(download_id)
        return result.model_dump() if hasattr(result, "model_dump") else (result or {})

    async def set_file_priority(self, download_id: str, file_index: int, priority: int) -> dict:
        """Change torrent file priority; Soulseek files are not libtorrent files."""
        if _is_soulseek_id(download_id):
            return {"error": "Soulseek/slskd file priority is not supported by LJS."}
        result = await self._downloader.set_file_priority(download_id, file_index, priority)
        return {"value": bool(result)}

    async def cancel_download(self, download_id: str) -> dict:
        """Cancel a torrent or cancel/remove a Soulseek transfer group."""
        if _is_soulseek_id(download_id):
            return await self._cancel_soulseek_download(download_id)
        await self._downloader.cancel_download(download_id)
        return {"status": "cancelled", "download_id": download_id}

    async def pause_downloads(self, download_ids: list[str]) -> dict:
        """Pause multiple torrent downloads by ID."""
        succeeded: list[str] = []
        failed: list[str] = []
        for dl_id in download_ids:
            try:
                result = await self.pause_download(dl_id)
                if result and not result.get("error"):
                    succeeded.append(dl_id)
                else:
                    failed.append(dl_id)
            except Exception as exc:
                logger.debug(f'Batch pause failed for {dl_id}: {exc}')
                failed.append(dl_id)
        return {"succeeded": succeeded, "failed": failed, "total": len(download_ids)}

    async def resume_downloads(self, download_ids: list[str]) -> dict:
        """Resume multiple paused torrent downloads by ID."""
        succeeded: list[str] = []
        failed: list[str] = []
        for dl_id in download_ids:
            try:
                result = await self.resume_download(dl_id)
                if result and not result.get("error"):
                    succeeded.append(dl_id)
                else:
                    failed.append(dl_id)
            except Exception as exc:
                logger.debug(f'Batch resume failed for {dl_id}: {exc}')
                failed.append(dl_id)
        return {"succeeded": succeeded, "failed": failed, "total": len(download_ids)}

    async def cancel_downloads(self, download_ids: list[str]) -> dict:
        """Cancel multiple torrent/Soulseek downloads by ID."""
        succeeded: list[str] = []
        failed: list[str] = []
        for dl_id in download_ids:
            try:
                result = await self.cancel_download(dl_id)
                if result and not result.get("error"):
                    succeeded.append(dl_id)
                else:
                    failed.append(dl_id)
            except Exception as exc:
                logger.debug(f'Batch cancel failed for {dl_id}: {exc}')
                failed.append(dl_id)
        return {"succeeded": succeeded, "failed": failed, "total": len(download_ids)}

    async def _cancel_soulseek_download(self, download_id: str) -> dict:
        if not self._settings_manager:
            return {"error": "Soulseek settings are not available."}
        cfg = getattr(getattr(self._settings_manager, "settings", None), "soulseek", None)
        if cfg is None or not getattr(cfg, "api_configured", False):
            return {"error": "Soulseek/slskd is not configured."}

        model = SlskdTransferReadModel(self._settings_manager, self._database)
        rows = await model.active_download_rows(include_completed=True)
        row = next((r for r in rows if str(r.get("id") or "") == str(download_id)), None)
        if row is None:
            removed_shadow = await model.remove_shadow_transfers([download_id])
            if removed_shadow:
                return {"status": "removed", "download_id": download_id, "backend": "soulseek", "removed_shadow": removed_shadow}
            return {"error": "Soulseek transfer not found."}

        username = str(row.get("slskd_username") or row.get("username") or "").strip()
        transfer_ids: list[str] = []
        for file_row in row.get("files") or []:
            if not isinstance(file_row, dict):
                continue
            transfer_id = str(file_row.get("slskd_id") or file_row.get("id") or file_row.get("file_path") or "").strip()
            if transfer_id and transfer_id not in transfer_ids:
                transfer_ids.append(transfer_id)
        if not username:
            await model.remove_shadow_transfers([download_id])
            return {"status": "removed", "download_id": download_id, "backend": "soulseek", "warning": "Only the local shadow row could be removed; no slskd username was available."}

        cancelled: list[str] = []
        failed: list[dict[str, str]] = []
        client = SlskdClient(cfg)
        for transfer_id in transfer_ids or [str(row.get("slskd_folder") or row.get("item_name") or "")]:
            if not transfer_id:
                continue
            result = await client.cancel_download(username=username, transfer_id=transfer_id, remove=True)
            if isinstance(result, dict) and result.get("ok") is False:
                failed.append({"transfer_id": transfer_id, "error": str(result.get("error") or result.get("error_code") or "cancel failed")})
            else:
                cancelled.append(transfer_id)
        # slskd has a bulk clear endpoint for rows already in terminal states.
        if str(row.get("status") or "").lower() in {"complete", "failed", "cancelled"}:
            await client.remove_completed_downloads()
        removed_shadow = await model.remove_shadow_transfers([download_id])
        if failed and not cancelled and not removed_shadow:
            return {"error": failed[0]["error"], "failed": failed, "download_id": download_id, "backend": "soulseek"}
        return {
            "status": "cancelled" if cancelled else "removed",
            "download_id": download_id,
            "backend": "soulseek",
            "cancelled": cancelled,
            "failed": failed,
            "removed_shadow": removed_shadow,
        }
