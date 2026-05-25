"""
State coordinator for LJS.

Synchronizes generic category item state between Settings (YAML) and
Database (SQLite) during startup. This is not a compatibility shim: fresh
installs still use settings as the editable desired-state source while the
category database stores runtime state and progress envelopes.
"""

from typing import Any

from loguru import logger

from src.core.config import SettingsManager
from src.core.database import Database
from src.core.models import CategoryItem
from src.core.categories.identity import canonical_item_key, clean_category_item_name, clean_display_title


class StateCoordinator:
    """Coordinates generic category-item state across persistence layers."""

    def __init__(self, settings_manager: SettingsManager, db: Database) -> None:
        """Initialize the coordinator with settings and database access.

        Args:
            settings_manager: Settings manager that owns user-configured category items.
            db: Database facade used for category item progress envelopes.
        """
        self._settings_manager = settings_manager
        self._db = db

    async def sync_category_items(self) -> None:
        """Ensure configured category items have database progress envelopes.

        The coordinator is deliberately category-generic. TV-specific fields such
        as ``last_season`` and ``last_episode`` are copied only when present on a
        TV item; other categories receive a minimal tracked-state envelope. The
        category itself owns the meaning of any state beyond these generic keys.
        """
        settings = self._settings_manager.settings
        await self._repair_identity_artifact_duplicates(settings)
        synced_count = 0

        for item in settings.tracked_items:
            category_id = self._category_id_for(item)
            await self._ensure_category_item(category_id, item)
            progress = await self._db.media.get_item_progress(category_id, item.key)
            if progress:
                continue

            payload = self._initial_progress_payload(item)
            await self._db.media.update_item_progress(category_id, item.key, payload)
            synced_count += 1

        if synced_count > 0:
            # Startup can initialize hundreds of discovered movie rows after a new
            # migration.  Log a single summary so Voyage Logs remains useful.
            logger.info(f"State sync complete: initialized {synced_count} category items.")
        else:
            logger.debug("State sync: all category items already have database records.")


    async def _repair_identity_artifact_duplicates(self, settings: Any) -> None:
        """Merge category items created by old ``(None)`` path artefacts.

        Earlier target-path formatting could literalize missing years into item
        IDs such as ``For All Mankind (None)``.  Keep the canonical configured
        item and migrate lightweight DB state/units/metadata away from the bad
        key so the library does not show duplicate shows after startup.
        """
        seen: dict[tuple[str, str], CategoryItem] = {}
        cleaned_items: list[CategoryItem] = []
        changed = False
        for item in list(getattr(settings.tracked_items, "items", settings.tracked_items) or []):
            category_id = self._category_id_for(item)
            clean_key = clean_category_item_name(item.key, category_id)
            canonical = (category_id, canonical_item_key(clean_key))
            existing = seen.get(canonical)
            if existing:
                await self._merge_db_item(category_id, item.key, existing.key)
                changed = True
                continue
            if clean_key != item.key:
                old_key = item.key
                try:
                    item.key = clean_key
                    display = getattr(item, "display_name", None)
                    if (
                        not display
                        or canonical_item_key(display) == canonical_item_key(old_key)
                        or canonical_item_key(display) == canonical_item_key(clean_display_title(old_key))
                    ):
                        item.display_name = clean_key
                    await self._merge_db_item(category_id, old_key, clean_key)
                    changed = True
                    logger.info(f"Normalized dirty tracked item identity {category_id}/{old_key} -> {clean_key}")
                except Exception as exc:
                    logger.debug(f"Could not normalize tracked item key {old_key!r}: {exc}")
            seen[canonical] = item
            cleaned_items.append(item)
        if changed and hasattr(settings.tracked_items, "items"):
            settings.tracked_items.items = cleaned_items
            self._settings_manager.save(settings)

    async def _merge_db_item(self, category_id: str, old_item_id: str, new_item_id: str) -> None:
        """Move duplicate category DB rows from old_item_id to new_item_id."""
        if not old_item_id or old_item_id == new_item_id:
            return
        if await self._db.media.get_category_item(category_id, new_item_id) is None:
            old_payload = await self._db.media.get_category_item(category_id, old_item_id) or {}
            payload = dict(old_payload)
            payload.update({
                "category_id": category_id,
                "item_id": new_item_id,
                "key": new_item_id,
                "display_name": clean_category_item_name(new_item_id, category_id),
                "item_type": category_id,
            })
            await self._db.media.upsert_category_item(category_id, new_item_id, payload)

        conn = await self._db.get_connection() if hasattr(self._db, "get_connection") else None
        if conn is None:
            return
        try:
            await conn.execute(
                """UPDATE OR IGNORE category_item_units
                   SET item_id = ? WHERE category_id = ? AND item_id = ?""",
                (new_item_id, category_id, old_item_id),
            )
            await conn.execute(
                """UPDATE OR IGNORE category_item_metadata
                   SET item_id = ? WHERE category_id = ? AND item_id = ?""",
                (new_item_id, category_id, old_item_id),
            )
            await conn.execute(
                """UPDATE OR IGNORE category_property_index
                   SET item_id = ? WHERE category_id = ? AND item_id = ?""",
                (new_item_id, category_id, old_item_id),
            )
            await conn.execute(
                """UPDATE OR IGNORE downloads
                   SET item_id = ?, item_name = ?
                   WHERE category_id = ? AND (item_id = ? OR item_name = ?)""",
                (new_item_id, new_item_id, category_id, old_item_id, old_item_id),
            )
            await conn.execute(
                """DELETE FROM category_items
                   WHERE category_id = ? AND item_id = ?""",
                (category_id, old_item_id),
            )
            await conn.commit()
            logger.info(f"Merged duplicate category identity {category_id}/{old_item_id} -> {new_item_id}")
        except Exception as exc:
            logger.warning(f"Failed to merge duplicate category item {category_id}/{old_item_id}: {exc}")

    async def _ensure_category_item(self, category_id: str, item: CategoryItem) -> None:
        """Ensure the configured category item exists before unit/progress rows.

        SQLite enforces a parent ``category_items`` row for every unit.  Startup
        sync therefore persists the generic item envelope first, then creates
        the progress unit.
        """
        existing = await self._db.media.get_category_item(category_id, item.key)
        if existing:
            return
        payload = item.model_dump(mode="json")
        payload.setdefault("category_id", category_id)
        payload.setdefault("item_id", item.key)
        payload.setdefault("key", item.key)
        payload.setdefault("display_name", item.display_name or item.key)
        payload.setdefault("item_type", category_id)
        await self._db.media.upsert_category_item(category_id, item.key, payload)

    def _category_id_for(self, item: CategoryItem) -> str:
        """Return the category identifier for a configured item.

        Args:
            item: Category item from settings.

        Returns:
            Category identifier used by generic category storage.
        """
        return getattr(item, "category_id", getattr(item, "item_type", "media")) or "media"

    def _initial_progress_payload(self, item: CategoryItem) -> dict[str, Any]:
        """Build an initial progress/state payload for a category item.

        Args:
            item: Category item from settings.

        Returns:
            Generic progress envelope with category-owned optional fields.
        """
        payload: dict[str, Any] = {"status": "tracked"}
        if getattr(item, "is_episodic", False):
            payload["last_season"] = getattr(item, "last_season", None) or 1
            payload["last_episode"] = getattr(item, "last_episode", None) or 0
        return payload
