"""
Upgrade action handlers for LJS.

Provides UpgradesActionHandler: the single place for quality upgrade
mutation logic invoked via ActionGateway from UI endpoints.
"""

from src.core.database import Database
from src.core.downloader import DownloadManager
from src.core.models import DownloadPriority


class UpgradesActionHandler:
    """Handlers for upgrade management actions routed through ActionGateway.

    Each method receives keyword arguments from ActionCommand.arguments
    and returns a dict wrapped into ActionResult.data.

    Dependencies (injected at composition root):
        db — Database (upgrade candidate CRUD)
        downloader — DownloadManager (add magnet for approved upgrade)
    """

    def __init__(self, db: Database, downloader: DownloadManager) -> None:
        self._db = db
        self._downloader = downloader

    async def approve(self, upgrade_id: int, confirmed: bool = False) -> dict:
        """Approve a quality upgrade and add the magnet to the download queue.

        Requires ``confirmed=True`` to prevent automated/accidental approval.
        Returns ``{"status": "confirmation_required"}`` if not confirmed.
        Returns ``{"found": False}`` if the upgrade_id does not exist.
        """
        if not confirmed:
            return {"status": "confirmation_required", "message": "User confirmation required before approving upgrade"}
        candidates = await self._db.downloads.get_upgrade_candidates()
        candidate = next((c for c in candidates if c.id == upgrade_id), None)
        if not candidate:
            return {"found": False}
        await self._db.downloads.set_upgrade_status(upgrade_id, "approved")
        item = await self._downloader.add_magnet(
            candidate.best_upgrade_magnet,
            item_name=candidate.item_name,
            reason="upgrade_quality",
            priority=DownloadPriority.LOW,
        )
        return {"status": "approved", "download_id": item.id, "item_name": candidate.item_name}

    async def deny(self, upgrade_id: int) -> dict:
        """Deny a quality upgrade candidate."""
        await self._db.downloads.set_upgrade_status(upgrade_id, "denied")
        return {"status": "denied"}
