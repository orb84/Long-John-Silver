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
        """Update configurable fields on one category item in every durable store."""
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
        changed = False
        for field_name in ("language", "subtitle_languages", "quality", "auto_download"):
            if field_name in kwargs and hasattr(item, field_name):
                value = self._coerce_config_value(field_name, kwargs[field_name])
                setattr(item, field_name, value)
                changed = True
        if changed:
            self._sm.save(settings)
            await self._persist_category_item_config(category_id, item_id, item)
            await self._scheduler.invalidate_item_lifecycle(category_id, item_id, reason="category_item_config_changed")
            await self._scheduler.sync_category_watch_policy(category_id, item_id, item=item, reason="category_item_config_changed")
        return {"found": True, "category_id": category_id, "item_id": item_id, "changed": changed}

    async def _persist_category_item_config(self, category_id: str, item_id: str, item: Any) -> None:
        db = getattr(self._scheduler, "database", None)
        media = getattr(db, "media", None) if db is not None else None
        if not media or not hasattr(media, "upsert_category_item"):
            return
        if hasattr(item, "model_dump"):
            payload = item.model_dump(mode="json")
        elif isinstance(item, dict):
            payload = dict(item)
        else:
            payload = dict(getattr(item, "__dict__", {}) or {})
        await media.upsert_category_item(category_id, item_id, payload)

    def _coerce_config_value(self, field_name: str, value: Any) -> Any:
        if field_name != "auto_download":
            return value
        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            text = value.strip().casefold()
            if text in {"true", "1", "yes", "y", "on", "enabled"}:
                return True
            if text in {"false", "0", "no", "n", "off", "disabled", ""}:
                return False
        return False

    async def consolidate(self, dry_run: bool = True) -> dict:
        """Consolidate the library (move organized files to final paths)."""
        results = await self._librarian.consolidate_library(dry_run=dry_run)
        return {"status": "ok", "results": results, "dry_run": dry_run}
