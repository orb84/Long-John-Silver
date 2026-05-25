"""
Library action handlers for LJS.

Provides LibraryActionHandler: the single place for library management
mutation logic invoked via ActionGateway from UI endpoints.
"""

from typing import Any

from src.core.config import SettingsManager
from src.core.librarian import Librarian
from src.core.scheduler import MediaScheduler


class LibraryActionHandler:
    """Handlers for library management actions routed through ActionGateway.

    Each method receives keyword arguments from ActionCommand.arguments
    and returns a dict wrapped into ActionResult.data.

    Dependencies (injected at composition root):
        scheduler — MediaScheduler (library scans)
        settings_manager — SettingsManager (category item config)
        librarian — Librarian (library consolidation)
    """

    def __init__(self, scheduler: MediaScheduler, settings_manager: SettingsManager, librarian: Librarian) -> None:
        self._scheduler = scheduler
        self._sm = settings_manager
        self._librarian = librarian

    async def scan(self) -> dict:
        """Trigger a library scan without blocking the HTTP request."""
        if hasattr(self._scheduler, "request_library_scan"):
            return self._scheduler.request_library_scan(force=True, refresh_metadata=True, reason="manual")

        # Compatibility fallback for tests or older scheduler doubles.
        await self._scheduler.scan_library(force=True)
        result = self._scheduler.get_last_scan_result()
        if not result:
            return {"status": "ok", "message": "Scan triggered, no results yet."}
        return {
            "status": "ok",
            "items_found": len(result.items),
            "categories_found": sorted({item.category_id for item in result.items}),
            "total_files": result.total_files,
            "total_size_mb": round(result.total_size_bytes / 1024 / 1024, 1),
        }

    async def update_category_item_config(self, category_id: str, item_id: str, **kwargs: Any) -> dict:
        """Update configurable fields on one category item."""
        settings = self._sm.settings
        item = next(
            (
                i for i in settings.tracked_items
                if i.key == item_id and getattr(i, "item_type", category_id) == category_id
            ),
            None,
        )
        if not item:
            return {"found": False, "category_id": category_id, "item_id": item_id}
        for field_name in ("language", "subtitle_languages", "quality", "auto_download"):
            if field_name in kwargs and hasattr(item, field_name):
                setattr(item, field_name, kwargs[field_name])
        self._sm.save(settings)
        return {"found": True, "category_id": category_id, "item_id": item_id}

    async def consolidate(self, dry_run: bool = True) -> dict:
        """Consolidate the library (move organized files to final paths)."""
        results = await self._librarian.consolidate_library(dry_run=dry_run)
        return {"status": "ok", "results": results, "dry_run": dry_run}
