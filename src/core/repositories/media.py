"""
Category-first media repository for LJS.

The repository stores category items, item units, and provider metadata without
knowing category-specific fields. Category classes own the meaning and
validation of their properties; the database stores stable envelopes plus JSON
payloads so new categories do not require schema migrations.
"""

import json
from datetime import datetime, timezone
from typing import Any

from src.core.repositories.base import BaseRepository


class MediaRepository(BaseRepository):
    """Repository for category-generic item, unit, and metadata persistence."""

    @staticmethod
    def _now() -> str:
        """Return a UTC ISO timestamp for persisted records."""
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _dump(value: Any) -> str:
        """Serialize JSON values safely for SQLite storage."""
        return json.dumps(value if value is not None else {}, default=str)

    @staticmethod
    def _load(value: str | None, default: Any) -> Any:
        """Deserialize a JSON value, falling back to a safe default."""
        if not value:
            return default
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return default

    @staticmethod
    def _item_display_name(item_id: str, item: dict[str, Any]) -> str:
        """Resolve the human-readable item name from common category fields."""
        return str(
            item.get("display_name")
            or item.get("name")
            or item.get("title")
            or item.get("key")
            or item.get("item_name")
            or item_id
        )

    @staticmethod
    def _item_type(category_id: str, item: dict[str, Any]) -> str:
        """Resolve the persisted item type, defaulting to the category id."""
        return str(item.get("item_type") or item.get("type") or category_id)

    async def upsert_category_item(self, category_id: str, item_id: str, item: dict[str, Any]) -> None:
        """Insert or update a category item without interpreting custom fields.

        Args:
            category_id: Owning category identifier, for example ``tv`` or ``music``.
            item_id: Category-local stable item identifier.
            item: Full category-owned state. Optional ``properties``, ``metadata``,
                and ``state`` dicts are stored in separate JSON columns for clarity,
                while the full payload is preserved in ``item_json``.
        """
        properties = item.get("properties") or {}
        metadata = item.get("metadata") or {}
        state = item.get("state") or {}
        display_name = self._item_display_name(item_id, item)
        await self._db.execute(
            """INSERT INTO category_items
               (category_id, item_id, display_name, item_type, enabled, status,
                properties_json, metadata_json, state_json, item_json,
                last_checked_at, last_download_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(category_id, item_id) DO UPDATE SET
                   display_name = excluded.display_name,
                   item_type = excluded.item_type,
                   enabled = excluded.enabled,
                   status = excluded.status,
                   properties_json = excluded.properties_json,
                   metadata_json = excluded.metadata_json,
                   state_json = excluded.state_json,
                   item_json = excluded.item_json,
                   last_checked_at = excluded.last_checked_at,
                   last_download_at = excluded.last_download_at,
                   updated_at = datetime('now')""",
            (
                category_id,
                item_id,
                display_name,
                self._item_type(category_id, item),
                1 if item.get("enabled", True) else 0,
                str(item.get("status") or ""),
                self._dump(properties),
                self._dump(metadata),
                self._dump(state),
                self._dump(item),
                item.get("last_checked_at"),
                item.get("last_download_at"),
            ),
        )
        await self._replace_property_index(category_id, item_id, properties)
        await self._db.commit()

    async def ensure_category_item(self, category_id: str, item_id: str, display_name: str | None = None) -> None:
        """Ensure a parent category item row exists for future unit writes.

        This is a defensive guard around SQLite foreign-key constraints. Normal
        flows persist category items explicitly, but scanner/download paths can
        discover units before startup state sync has seen the parent item.
        """
        if await self.get_category_item(category_id, item_id):
            return
        await self.upsert_category_item(category_id, item_id, {
            "category_id": category_id,
            "item_id": item_id,
            "key": item_id,
            "display_name": display_name or item_id,
            "item_type": category_id,
            "status": "discovered",
        })

    async def get_category_item(self, category_id: str, item_id: str) -> dict[str, Any] | None:
        """Return one category item as a merged category-owned payload."""
        cursor = await self._db.execute(
            "SELECT * FROM category_items WHERE category_id = ? AND item_id = ?",
            (category_id, item_id),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return self._row_to_category_item(row)

    async def list_category_items(self, category_id: str | None = None) -> list[dict[str, Any]]:
        """List category items, optionally filtered by category."""
        if category_id:
            cursor = await self._db.execute(
                "SELECT * FROM category_items WHERE category_id = ? ORDER BY display_name, item_id",
                (category_id,),
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM category_items ORDER BY category_id, display_name, item_id"
            )
        rows = await cursor.fetchall()
        return [self._row_to_category_item(row) for row in rows]

    async def delete_category_item(self, category_id: str, item_id: str) -> None:
        """Delete a category item and all repository rows owned by it."""
        await self._db.execute(
            "DELETE FROM category_item_units WHERE category_id = ? AND item_id = ?",
            (category_id, item_id),
        )
        await self._db.execute(
            "DELETE FROM category_property_index WHERE category_id = ? AND item_id = ?",
            (category_id, item_id),
        )
        await self._db.execute(
            "DELETE FROM category_item_metadata WHERE category_id = ? AND item_id = ?",
            (category_id, item_id),
        )
        await self._db.execute(
            "DELETE FROM category_item_processing_state WHERE category_id = ? AND item_id = ?",
            (category_id, item_id),
        )
        await self._db.execute(
            "DELETE FROM category_item_processing_events WHERE category_id = ? AND item_id = ?",
            (category_id, item_id),
        )
        await self._db.execute(
            "DELETE FROM category_item_suggestion_state WHERE category_id = ? AND item_id = ?",
            (category_id, item_id),
        )
        await self._db.execute(
            "DELETE FROM category_items WHERE category_id = ? AND item_id = ?",
            (category_id, item_id),
        )
        await self._db.commit()

    async def remove_category_units(
        self,
        category_id: str,
        item_id: str,
        *,
        status: str | None = None,
        unit_type: str | None = None,
    ) -> int:
        """Remove units for one item, optionally restricted by status/type."""
        clauses = ["category_id = ?", "item_id = ?"]
        values: list[Any] = [category_id, item_id]
        if status is not None:
            clauses.append("status = ?")
            values.append(status)
        if unit_type is not None:
            clauses.append("unit_type = ?")
            values.append(unit_type)
        cursor = await self._db.execute(
            f"DELETE FROM category_item_units WHERE {' AND '.join(clauses)}",
            tuple(values),
        )
        await self._db.commit()
        return int(getattr(cursor, "rowcount", 0) or 0)

    async def set_category_item_paused(self, category_id: str, item_id: str, paused: bool) -> None:
        """Set the paused flag in a category item's generic state payload."""
        item = await self.get_category_item(category_id, item_id)
        if not item:
            item = {"category_id": category_id, "item_id": item_id, "display_name": item_id}
        state = dict(item.get("state") or {})
        state["paused"] = bool(paused)
        item["state"] = state
        item["enabled"] = not paused
        await self.upsert_category_item(category_id, item_id, item)


    async def invalidate_category_item_processing(
        self,
        category_id: str,
        item_id: str,
        reason: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Mark one item lifecycle ledger as due after a repository event.

        This lightweight helper lets scanner/download paths invalidate cached
        suggestions without importing the lifecycle engine. The engine will
        recompute exact fingerprints on the next scheduled or manual pass.
        """
        now = self._now()
        await self._db.execute(
            """INSERT INTO category_item_processing_state
               (category_id, item_id, next_check_at, next_check_reason, invalidated_by, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(category_id, item_id) DO UPDATE SET
                    next_check_at = excluded.next_check_at,
                    next_check_reason = excluded.next_check_reason,
                    invalidated_by = excluded.invalidated_by,
                    updated_at = excluded.updated_at""",
            (category_id, item_id, now, reason, json.dumps([reason], ensure_ascii=False), now),
        )
        await self._db.execute(
            """INSERT INTO category_item_processing_events
               (category_id, item_id, event_type, purpose, reason, status, payload_json, created_at)
               VALUES (?, ?, 'invalidated', 'repository_event', ?, 'pending', ?, ?)""",
            (category_id, item_id, reason, self._dump(payload or {}), now),
        )
        await self._db.commit()

    async def get_category_item_paused(self, category_id: str, item_id: str) -> bool:
        """Return whether a category item is paused."""
        item = await self.get_category_item(category_id, item_id)
        if not item:
            return False
        state = item.get("state") or {}
        return bool(state.get("paused")) or not bool(item.get("enabled", True))

    async def find_category_items_by_property(
        self,
        category_id: str,
        property_name: str,
        value: str | int | float | bool,
    ) -> list[dict[str, Any]]:
        """Find items by an indexed dynamic property value."""
        value_text = str(value).lower()
        cursor = await self._db.execute(
            """SELECT ci.* FROM category_items ci
               JOIN category_property_index pi
                 ON pi.category_id = ci.category_id AND pi.item_id = ci.item_id
               WHERE pi.category_id = ? AND pi.property_name = ? AND pi.value_text = ?
               ORDER BY ci.display_name""",
            (category_id, property_name, value_text),
        )
        rows = await cursor.fetchall()
        return [self._row_to_category_item(row) for row in rows]

    async def _replace_property_index(
        self,
        category_id: str,
        item_id: str,
        properties: dict[str, Any],
    ) -> None:
        """Rebuild the optional search index for scalar dynamic properties."""
        await self._db.execute(
            "DELETE FROM category_property_index WHERE category_id = ? AND item_id = ?",
            (category_id, item_id),
        )
        for name, value in properties.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                value_text = "" if value is None else str(value).lower()
                value_number = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
                await self._db.execute(
                    """INSERT OR REPLACE INTO category_property_index
                       (category_id, item_id, property_name, value_text, value_number, value_json, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
                    (category_id, item_id, name, value_text, value_number, self._dump(value)),
                )

    def _row_to_category_item(self, row: Any) -> dict[str, Any]:
        """Convert a category_items row into the category-owned payload shape."""
        data = self._load(row["item_json"], {})
        data.setdefault("category_id", row["category_id"])
        data.setdefault("item_id", row["item_id"])
        data.setdefault("key", row["item_id"])
        data.setdefault("display_name", row["display_name"])
        data.setdefault("item_type", row["item_type"])
        data.setdefault("enabled", bool(row["enabled"]))
        data.setdefault("status", row["status"])
        data.setdefault("properties", self._load(row["properties_json"], {}))
        data.setdefault("metadata", self._load(row["metadata_json"], {}))
        data.setdefault("state", self._load(row["state_json"], {}))
        data.setdefault("last_checked_at", row["last_checked_at"])
        data.setdefault("last_download_at", row["last_download_at"])
        return data

    async def upsert_category_unit(
        self,
        category_id: str,
        item_id: str,
        unit_key: str,
        unit: dict[str, Any],
        status: str = "",
        unit_type: str = "",
    ) -> None:
        """Insert or update a unit belonging to a category item.

        Units are category-owned sub-objects: episodes for TV, tracks for music,
        chapters for books, files for generic media, and so on.
        """
        properties = unit.get("properties") or {}
        metadata = unit.get("metadata") or {}
        state = unit.get("state") or {}
        display_name = str(unit.get("display_name") or unit.get("title") or unit_key)
        await self.ensure_category_item(category_id, item_id, str(unit.get("item_display_name") or item_id))
        await self._db.execute(
            """INSERT INTO category_item_units
               (category_id, item_id, unit_key, unit_type, display_name, status,
                sort_index, properties_json, metadata_json, state_json, unit_json, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(category_id, item_id, unit_key) DO UPDATE SET
                   unit_type = excluded.unit_type,
                   display_name = excluded.display_name,
                   status = excluded.status,
                   sort_index = excluded.sort_index,
                   properties_json = excluded.properties_json,
                   metadata_json = excluded.metadata_json,
                   state_json = excluded.state_json,
                   unit_json = excluded.unit_json,
                   updated_at = datetime('now')""",
            (
                category_id,
                item_id,
                unit_key,
                unit_type or str(unit.get("unit_type") or ""),
                display_name,
                status or str(unit.get("status") or ""),
                int(unit.get("sort_index") or 0),
                self._dump(properties),
                self._dump(metadata),
                self._dump(state),
                self._dump(unit),
            ),
        )
        await self._db.commit()

    async def get_category_unit(self, category_id: str, item_id: str, unit_key: str) -> dict[str, Any] | None:
        """Return a single category item unit."""
        cursor = await self._db.execute(
            """SELECT * FROM category_item_units
               WHERE category_id = ? AND item_id = ? AND unit_key = ?""",
            (category_id, item_id, unit_key),
        )
        row = await cursor.fetchone()
        return self._row_to_category_unit(row) if row else None

    async def list_category_units(
        self,
        category_id: str,
        item_id: str,
        unit_type: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """List units for one category item, optionally filtered by type/status."""
        clauses = ["category_id = ?", "item_id = ?"]
        values: list[Any] = [category_id, item_id]
        if unit_type is not None:
            clauses.append("unit_type = ?")
            values.append(unit_type)
        if status is not None:
            clauses.append("status = ?")
            values.append(status)
        cursor = await self._db.execute(
            f"""SELECT * FROM category_item_units
                WHERE {' AND '.join(clauses)}
                ORDER BY sort_index, unit_key""",
            tuple(values),
        )
        rows = await cursor.fetchall()
        return [self._row_to_category_unit(row) for row in rows]

    def _row_to_category_unit(self, row: Any) -> dict[str, Any]:
        """Convert a category_item_units row to a merged payload."""
        data = self._load(row["unit_json"], {})
        data.setdefault("category_id", row["category_id"])
        data.setdefault("item_id", row["item_id"])
        data.setdefault("unit_key", row["unit_key"])
        data.setdefault("unit_type", row["unit_type"])
        data.setdefault("display_name", row["display_name"])
        data.setdefault("status", row["status"])
        data.setdefault("properties", self._load(row["properties_json"], {}))
        data.setdefault("metadata", self._load(row["metadata_json"], {}))
        data.setdefault("state", self._load(row["state_json"], {}))
        return data

    async def update_item_progress(
        self,
        category_id: str,
        item_id: str,
        progress_payload: dict[str, Any] | int,
        last_episode: int | None = None,
    ) -> None:
        """Record category-owned progress state for an item.

        Args:
            category_id: Category that owns the item.
            item_id: Category-local item identifier.
            progress_payload: Category-defined progress fields stored as a generic
                unit. For backwards compatibility, callers may pass
                ``last_season`` as an int and ``last_episode`` separately.
            last_episode: Optional legacy episode number.
        """
        if isinstance(progress_payload, dict):
            payload = dict(progress_payload)
        else:
            payload = {"last_season": int(progress_payload), "last_episode": int(last_episode or 0)}
        payload.setdefault("category_id", category_id)
        payload.setdefault("item_id", item_id)
        payload.setdefault("unit_type", "progress")
        payload.setdefault("display_name", "Progress")
        await self.upsert_category_unit(
            category_id,
            item_id,
            "progress",
            payload,
            status="progress",
            unit_type="progress",
        )



    async def list_category_unit_counts(self, category_id: str) -> dict[str, dict[str, int]]:
        """Return cheap per-item unit counts for one category overview."""
        cursor = await self._db.execute(
            """SELECT
                   item_id,
                   COUNT(*) AS total,
                   SUM(CASE WHEN status = 'downloaded' THEN 1 ELSE 0 END) AS downloaded
               FROM category_item_units
               WHERE category_id = ?
               GROUP BY item_id""",
            (category_id,),
        )
        rows = await cursor.fetchall()
        return {
            str(row["item_id"]): {
                "total": int(row["total"] or 0),
                "downloaded": int(row["downloaded"] or 0),
            }
            for row in rows
        }

    async def get_category_unit_counts(self, category_id: str, item_id: str) -> dict[str, int]:
        """Return cheap unit counts for overview/list-card rendering."""
        cursor = await self._db.execute(
            """SELECT
                   COUNT(*) AS total,
                   SUM(CASE WHEN status = 'downloaded' THEN 1 ELSE 0 END) AS downloaded
               FROM category_item_units
               WHERE category_id = ? AND item_id = ?""",
            (category_id, item_id),
        )
        row = await cursor.fetchone()
        if not row:
            return {"total": 0, "downloaded": 0}
        return {
            "total": int(row["total"] or 0),
            "downloaded": int(row["downloaded"] or 0),
        }

    async def get_item_progress(self, category_id: str, item_id: str) -> dict[str, Any] | None:
        """Return category-owned progress state for one item."""
        return await self.get_category_unit(category_id, item_id, "progress")

    async def get_all_item_progress(self, category_id: str | None = None) -> list[dict[str, Any]]:
        """List progress units for all items, optionally restricted by category."""
        if category_id:
            cursor = await self._db.execute(
                """SELECT * FROM category_item_units
                   WHERE category_id = ? AND unit_key = 'progress'
                   ORDER BY item_id""",
                (category_id,),
            )
        else:
            cursor = await self._db.execute(
                """SELECT * FROM category_item_units
                   WHERE unit_key = 'progress'
                   ORDER BY category_id, item_id"""
            )
        rows = await cursor.fetchall()
        return [self._row_to_category_unit(row) for row in rows]

    async def record_unit_downloaded(
        self,
        category_id: str,
        item_id: str,
        unit_key: str,
        unit_payload: dict[str, Any],
    ) -> None:
        """Record a downloaded category-owned unit.

        The repository does not infer the meaning of the payload. Episodic categories
        may store season/episode, music categories may store track/disc fields, and
        book categories may store chapter metadata.
        """
        now = self._now()
        payload = dict(unit_payload)
        payload.setdefault("category_id", category_id)
        payload.setdefault("item_id", item_id)
        payload.setdefault("unit_key", unit_key)
        payload.setdefault("downloaded_at", now)
        payload.setdefault("unit_type", payload.get("unit_type") or "unit")
        payload.setdefault("display_name", payload.get("title") or payload.get("display_name") or unit_key)
        await self.upsert_category_unit(
            category_id,
            item_id,
            unit_key,
            payload,
            status="downloaded",
            unit_type=str(payload.get("unit_type") or "unit"),
        )

    async def remove_category_unit(self, category_id: str, item_id: str, unit_key: str) -> None:
        """Remove one unit from a category item."""
        await self._db.execute(
            """DELETE FROM category_item_units
               WHERE category_id = ? AND item_id = ? AND unit_key = ?""",
            (category_id, item_id, unit_key),
        )
        await self._db.commit()

    async def category_unit_exists(
        self,
        category_id: str,
        item_id: str,
        unit_key: str,
        status: str | None = None,
    ) -> bool:
        """Return whether a category unit exists, optionally with a specific status."""
        unit = await self.get_category_unit(category_id, item_id, unit_key)
        if not unit:
            return False
        return status is None or unit.get("status") == status

    async def upsert_category_metadata(
        self,
        category_id: str,
        item_id: str,
        provider: str,
        metadata: dict[str, Any],
        external_id: str = "",
    ) -> None:
        """Store provider metadata for any category item."""
        await self._db.execute(
            """INSERT INTO category_item_metadata
               (category_id, item_id, provider, external_id, metadata_json, refreshed_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(category_id, item_id, provider, external_id) DO UPDATE SET
                   metadata_json = excluded.metadata_json,
                   refreshed_at = datetime('now')""",
            (category_id, item_id, provider, str(external_id or ""), self._dump(metadata)),
        )
        await self._db.commit()

    async def get_category_metadata(
        self,
        category_id: str,
        item_id: str,
        provider: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return provider metadata rows for a category item."""
        if provider:
            cursor = await self._db.execute(
                """SELECT * FROM category_item_metadata
                   WHERE category_id = ? AND item_id = ? AND provider = ?
                   ORDER BY refreshed_at DESC""",
                (category_id, item_id, provider),
            )
        else:
            cursor = await self._db.execute(
                """SELECT * FROM category_item_metadata
                   WHERE category_id = ? AND item_id = ?
                   ORDER BY refreshed_at DESC""",
                (category_id, item_id),
            )
        rows = await cursor.fetchall()
        return [
            {
                "category_id": row["category_id"],
                "item_id": row["item_id"],
                "provider": row["provider"],
                "external_id": row["external_id"],
                "metadata": self._load(row["metadata_json"], {}),
                "refreshed_at": row["refreshed_at"],
            }
            for row in rows
        ]


    async def get_all_category_metadata(
        self,
        category_id: str,
        provider: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return all provider metadata rows for one category."""
        if provider:
            cursor = await self._db.execute(
                """SELECT * FROM category_item_metadata
                   WHERE category_id = ? AND provider = ?
                   ORDER BY item_id, refreshed_at DESC""",
                (category_id, provider),
            )
        else:
            cursor = await self._db.execute(
                """SELECT * FROM category_item_metadata
                   WHERE category_id = ?
                   ORDER BY item_id, refreshed_at DESC""",
                (category_id,),
            )
        rows = await cursor.fetchall()
        return [
            {
                "category_id": row["category_id"],
                "item_id": row["item_id"],
                "provider": row["provider"],
                "external_id": row["external_id"],
                "metadata": self._load(row["metadata_json"], {}),
                "refreshed_at": row["refreshed_at"],
            }
            for row in rows
        ]
