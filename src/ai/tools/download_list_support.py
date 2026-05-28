"""Download list reporting services for LLM tools.

The list_downloads tool exposes a stable schema while this module owns sorting,
queue-position calculation, and summary aggregation.  Keeping presentation here
lets UI/chat telemetry evolve without bloating the tool class.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.ai.tools.download_support import DownloadSnapshotPresenter
from src.integrations.slskd_transfer_view import SlskdTransferReadModel

if TYPE_CHECKING:
    from src.core.downloader import DownloadManager


class DownloadListReportService:
    """Build the structured list_downloads report from the download manager."""

    def __init__(self, downloader: "DownloadManager", settings_manager: object | None = None, database: object | None = None) -> None:
        """Create a report service backed by concrete download and optional Soulseek managers."""
        self._downloader = downloader
        self._settings_manager = settings_manager
        self._database = database

    async def report(self) -> dict[str, Any]:
        """Return serialized active downloads, queue view, and summary counts."""
        active = await self._downloader.get_active_downloads()
        serialized_active = self._serialized(active, {})
        soulseek_rows = await self._soulseek_rows()
        combined_active = serialized_active + soulseek_rows
        queue_items = [item for item in combined_active if str(item.get("status") or "").lower() == "queued"]
        for index, item in enumerate(sorted(queue_items, key=lambda row: str(row.get("created_at") or "")), start=1):
            item["queue_position"] = index
        return {
            "active": combined_active,
            "queue": queue_items,
            "summary": self._summary_from_rows(combined_active),
            "count": len(combined_active),
        }

    async def _soulseek_rows(self) -> list[dict[str, Any]]:
        if not self._settings_manager:
            return []
        try:
            return await SlskdTransferReadModel(self._settings_manager, self._database).active_download_rows(include_completed=True)
        except Exception:
            return []

    def _summary_from_rows(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "total": len(rows),
            "by_status": self._count_by(rows, lambda row: str(row.get("status") or "unknown")),
            "by_health": self._count_by(rows, lambda row: "ok"),
            "active_slots": self._call_optional("active_count"),
            "max_concurrent": self._call_optional("max_concurrent"),
            "soulseek": len([row for row in rows if row.get("source") == "slskd" or row.get("backend") == "soulseek"]),
        }

    def _queued_items(self, active: list[object]) -> list[object]:
        """Return queue-status items sorted by the standard queue key."""
        queued = [item for item in active if self._status(item) == "queued"]
        return sorted(queued, key=DownloadSnapshotPresenter.sort_key)

    def _queue_positions(self, queue_items: list[object]) -> dict[str, int]:
        """Return stable one-based queue positions by download ID."""
        return {getattr(item, "id", ""): index + 1 for index, item in enumerate(queue_items)}

    def _serialized(self, items: list[object], queue_positions: dict[str, int]) -> list[dict[str, Any]]:
        """Serialize download items with optional queue-position data."""
        return [
            DownloadSnapshotPresenter.serialize(item, queue_position=queue_positions.get(getattr(item, "id", "")))
            for item in sorted(items, key=DownloadSnapshotPresenter.sort_key)
        ]

    def _summary(self, active: list[object]) -> dict[str, Any]:
        """Return status/health counts plus active-slot information."""
        return {
            "total": len(active),
            "by_status": self._count_by(active, self._status),
            "by_health": self._count_by(active, DownloadSnapshotPresenter.health_state),
            "active_slots": self._call_optional("active_count"),
            "max_concurrent": self._call_optional("max_concurrent"),
        }

    def _count_by(self, items: list[object], classifier) -> dict[str, int]:
        """Count items by a string classifier."""
        counts: dict[str, int] = {}
        for item in items:
            key = str(classifier(item) or "unknown").lower()
            counts[key] = counts.get(key, 0) + 1
        return counts

    def _status(self, item: object) -> str:
        """Return the normalized download status for an item."""
        return DownloadSnapshotPresenter.enum_value(getattr(item, "status", None), "unknown").lower()

    def _call_optional(self, method_name: str) -> object | None:
        """Call an optional downloader method when present."""
        method = getattr(self._downloader, method_name, None)
        return method() if callable(method) else None


class SupportToolProvider:
    """Compatibility provider for helper-only tool modules.

    This module contributes service collaborators consumed by a higher-level
    provider, so it intentionally returns no standalone agent tools.  Keeping a
    provider-shaped facade preserves package-wide smoke checks while still
    allowing implementation modules to remain focused and dependency-light.
    """

    def get_tools(self) -> list:
        """Return no tools because this support module is not an agent boundary."""
        return []
