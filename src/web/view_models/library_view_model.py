"""
LibraryInspectionBuilder for LJS.

Builds the UI inspection payload from canonical category library objects.  The
web layer does not know what a TV episode, movie file, game version, or book
chapter means; it renders the category-owned object shape and generic summary
fields computed by that category.
"""

from __future__ import annotations

from typing import Any

from src.core.library_objects import CanonicalLibraryObjectBuilder


class LibraryInspectionBuilder:
    """Build the library inspection payload for the UI from canonical objects."""

    def __init__(self, settings_manager: Any, db: Any, downloader: Any, category_registry: Any | None = None) -> None:
        """Inject settings, database, downloader dependencies, and category registry."""
        self._settings_manager = settings_manager
        self._db = db
        self._downloader = downloader
        self._library_objects = CanonicalLibraryObjectBuilder(db=db, category_registry=category_registry)

    async def build(self) -> dict:
        """Build the full category-first library inspection payload."""
        settings = self._settings_manager.settings
        active_downloads = await self._downloader.get_active_downloads()
        canonical_objects = await self._library_objects.build_many(
            [item for item in settings.tracked_items if item.enabled],
            active_downloads=active_downloads,
        )
        items_data = []
        total_units = 0
        for item, canonical in zip([item for item in settings.tracked_items if item.enabled], canonical_objects):
            category_id = canonical.get("category_id") or getattr(item, "item_type", "media")
            computed = canonical.get("computed") or {}
            units = canonical.get("units") or []
            item_downloads = [download for download in active_downloads if download.item_name == item.key]
            poster_path, poster_url = await self._poster_for_item(str(category_id), item, canonical)
            total_units += int(computed.get("downloaded_unit_count") or computed.get("downloaded_episode_count") or len(units))
            items_data.append({
                "name": item.key,
                "category": category_id,
                "display_name": canonical.get("display_name") or item.display_name or item.key,
                "language": getattr(item, "language", settings.language),
                "subtitle_languages": getattr(item, "subtitle_languages", []),
                "check_interval_days": getattr(item, "check_interval_days", None),
                "enabled": item.enabled,
                "discovered": item.discovered,
                "paused": bool((canonical.get("state") or {}).get("paused")) or not bool(canonical.get("enabled", True)),
                "poster_path": poster_path,
                "poster_url": poster_url,
                "quality": getattr(item, "quality", settings.default_quality).model_dump(),
                "progress": await self._db.media.get_item_progress(str(category_id), item.key),
                "canonical_object": canonical,
                "unit_groups": self._group_units(canonical),
                "seasons": {str(row.get("season_number")): row.get("episodes", []) for row in canonical.get("seasons", [])},
                "total_units": len(units),
                "total_episodes": int(computed.get("downloaded_episode_count") or 0),
                "total_groups": len(canonical.get("seasons", [])) or len(canonical.get("files", [])),
                "downloading": [self._download_summary(download) for download in item_downloads],
            })

        return {
            "items": items_data,
            "global_subtitle_languages": settings.subtitle_languages,
            "total_items": len(items_data),
            "total_units": total_units,
            "total_episodes": sum(int((item.get("canonical_object") or {}).get("computed", {}).get("downloaded_episode_count") or 0) for item in items_data),
        }

    async def _poster_for_item(self, category_id: str, item: Any, canonical: dict[str, Any]) -> tuple[str | None, str | None]:
        """Resolve poster identifiers from the canonical item and metadata rows."""
        direct = {**(canonical.get("metadata") or {}), **(getattr(item, "metadata", {}) or {})}
        if direct.get("local_poster_url"):
            return direct.get("poster_path"), direct["local_poster_url"]
        if direct.get("poster_url"):
            return direct.get("poster_path"), direct["poster_url"]
        if direct.get("poster_path"):
            return direct["poster_path"], self._tmdb_url(direct["poster_path"])
        for metadata in canonical.get("provider_metadata") or []:
            if metadata.get("local_poster_url"):
                return metadata.get("poster_path"), metadata["local_poster_url"]
            if metadata.get("poster_url"):
                return metadata.get("poster_path"), metadata["poster_url"]
            if metadata.get("poster_path"):
                return metadata["poster_path"], self._tmdb_url(metadata["poster_path"])
        poster_path = getattr(item, "poster_path", None)
        return poster_path, self._tmdb_url(poster_path)

    @staticmethod
    def _tmdb_url(poster_path: str | None) -> str | None:
        """Convert a TMDB poster path or URL into a browser-ready URL."""
        if not poster_path:
            return None
        if str(poster_path).startswith(("http://", "https://", "/category-data/")):
            return str(poster_path)
        if str(poster_path).startswith("/"):
            return f"https://image.tmdb.org/t/p/w500{poster_path}"
        return None

    @staticmethod
    def _group_units(canonical: dict[str, Any]) -> dict[str, dict[str, list[dict[str, Any]]]]:
        """Group canonical units without interpreting category-specific meanings."""
        grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for unit in canonical.get("units") or []:
            unit_type = str(unit.get("unit_type") or "unit")
            group_key = str(unit.get("group_key") or unit.get("season") or unit.get("disc") or unit.get("album") or "default")
            grouped.setdefault(unit_type, {}).setdefault(group_key, []).append(unit)
        for unit_groups in grouped.values():
            for group_units in unit_groups.values():
                group_units.sort(key=lambda row: int(row.get("sort_index") or 0))
        return grouped

    @staticmethod
    def _download_summary(download: Any) -> dict[str, Any]:
        """Render an active download for library item display."""
        return {
            "season": getattr(download, "season", None),
            "episode": getattr(download, "episode", None),
            "progress": getattr(download, "progress", 0.0),
            "status": getattr(getattr(download, "status", ""), "value", getattr(download, "status", "")),
            "id": getattr(download, "id", ""),
        }
