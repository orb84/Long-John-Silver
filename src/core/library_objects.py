"""
Canonical category library objects for LJS.

The library core is intentionally category-neutral: it knows how to fetch item,
unit, and metadata envelopes from SQLite, but it does not know what any
category-specific child object means. Each registered category declares its
canonical object specification and receives the raw envelopes so it can build
the normalized object used by suggestions, UI, agent queries, lifecycle
fingerprints, and future export APIs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class CanonicalLibraryObjectContext:
    """Raw category-owned envelopes used to build one canonical object.

    The fields are deliberately generic.  The owning category interprets the
    payloads according to ``category.library_object_spec()`` and returns the
    public normalized shape.  Core callers should not inspect unit names or child-object fields directly; they should request a canonical
    object and use the category-declared ``computed`` fields when they need
    status.
    """

    category_id: str
    item_id: str
    item: dict[str, Any]
    units: list[dict[str, Any]] = field(default_factory=list)
    metadata_rows: list[dict[str, Any]] = field(default_factory=list)
    settings_item: Any | None = None
    active_downloads: list[Any] = field(default_factory=list)


class CanonicalLibraryObjectBuilder:
    """Build canonical category library objects from repository envelopes.

    This service is the only generic-library read model builder.  It performs
    storage lookup, then delegates all domain interpretation to the category.
    If a future category needs nested structures, it implements them in its
    category package instead of adding branches here.
    """

    def __init__(self, db: Any, category_registry: Any | None = None) -> None:
        """Create a builder from the database facade and optional registry."""
        self._db = db
        self._categories = category_registry

    async def build(
        self,
        category_id: str,
        item_id: str,
        *,
        settings_item: Any | None = None,
        active_downloads: list[Any] | None = None,
    ) -> dict[str, Any]:
        """Return one canonical object built by the owning category.

        Missing repository rows are represented as a minimal discovered item so
        callers can ask about configured-but-not-yet-scanned items without
        crashing.  The category still owns the final shape and can mark the
        object as absent/missing through its computed fields.
        """
        item = await self._db.media.get_category_item(category_id, item_id)
        if item is None:
            item = {
                "category_id": category_id,
                "item_id": item_id,
                "key": item_id,
                "display_name": getattr(settings_item, "display_name", None) or item_id,
                "status": "configured",
            }
        units = await self._db.media.list_category_units(category_id, item_id)
        metadata_rows = await self._db.media.get_category_metadata(category_id, item_id)
        category = self._categories.get(category_id) if self._categories else None
        context = CanonicalLibraryObjectContext(
            category_id=category_id,
            item_id=item_id,
            item=item,
            units=units,
            metadata_rows=metadata_rows,
            settings_item=settings_item,
            active_downloads=list(active_downloads or []),
        )
        if category and hasattr(category, "build_library_object"):
            return category.build_library_object(context)
        return self._generic_object(context)

    async def build_many(
        self,
        items: list[Any],
        *,
        active_downloads: list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Build canonical objects for tracked settings items."""
        objects: list[dict[str, Any]] = []
        for item in items:
            category_id = str(getattr(item, "category_id", getattr(item, "item_type", "media")) or "media")
            item_id = str(getattr(item, "key", "") or "")
            if not category_id or not item_id:
                continue
            objects.append(await self.build(category_id, item_id, settings_item=item, active_downloads=active_downloads))
        return objects

    @staticmethod
    def _generic_object(context: CanonicalLibraryObjectContext) -> dict[str, Any]:
        """Return a safe generic canonical object when no category is available."""
        item = context.item or {}
        units = list(context.units or [])
        downloaded_units = [unit for unit in units if unit.get("status") == "downloaded"]
        total_size = sum(int(unit.get("size_bytes") or 0) for unit in downloaded_units)
        return {
            "schema_version": 1,
            "category_id": context.category_id,
            "item_id": context.item_id,
            "display_name": item.get("display_name") or item.get("key") or context.item_id,
            "item_type": item.get("item_type") or context.category_id,
            "status": item.get("status") or "",
            "properties": item.get("properties") or {},
            "metadata": item.get("metadata") or {},
            "state": item.get("state") or {},
            "units": units,
            "groups": {"default": downloaded_units},
            "computed": {
                "unit_count": len(units),
                "downloaded_unit_count": len(downloaded_units),
                "total_size_bytes": total_size,
                "has_local_files": bool(downloaded_units),
            },
            "provider_metadata": [row.get("metadata") or {} for row in context.metadata_rows],
        }
