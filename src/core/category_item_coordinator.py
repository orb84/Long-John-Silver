"""Authoritative category item mutation coordinator.

``CategoryItemCoordinator`` is the only supported write path for durable
category item mutations initiated by UI actions, assistant tools, automation, or
library discovery.  It is deliberately category-neutral: the coordinator owns
mutation ordering and consistency, while the category owns item construction,
metadata enrichment, lifecycle/watch semantics, and any domain-specific fields.

The invariant is:

    item mutation -> category enrichment hook -> settings + repository ->
    lifecycle invalidation -> category watch-policy/RSS/release-watch sync

Callers should not manually append to ``settings.tracked_items`` or upsert a new
``category_items`` row unless they are a low-level repair/synchronization path
explicitly documented as such.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from loguru import logger


@dataclass(slots=True)
class CategoryItemMutationContext:
    """Dependencies and mutation intent exposed to category hooks.

    ``source`` lets categories distinguish a user/agent add from a scanner
    discovery or migration repair without the generic coordinator learning the
    category domain. ``enrich_metadata`` is false for cheap discovery/repair
    paths where provider I/O would violate startup/scanner discipline; the item
    is still persisted and its watch policy can be recomputed later when normal
    lifecycle work says provider calls are due.
    """

    settings: Any
    db: Any = None
    metadata_enricher: Any = None
    metadata_clients: dict[str, Any] = field(default_factory=dict)
    artwork_manager: Any = None
    category_registry: Any = None
    source: str = "manual"
    enrich_metadata: bool = True


class CategoryItemCoordinator:
    """Single entry point for category item add/update/remove mutations.

    The coordinator never branches on category-specific semantics.  It asks the
    category to create/enrich an item and asks the scheduler to sync the generic
    watch policy produced by that category.  This keeps UI, assistant tools,
    library discovery, and automation from drifting into separate behaviors.
    """

    def __init__(
        self,
        *,
        settings_manager: Any,
        category_registry: Any,
        db: Any = None,
        scheduler: Any = None,
        metadata_enricher: Any = None,
        metadata_clients: dict[str, Any] | None = None,
        artwork_manager: Any = None,
    ) -> None:
        self._settings_manager = settings_manager
        self._category_registry = category_registry
        self._db = db
        self._scheduler = scheduler
        self._metadata_enricher = metadata_enricher
        self._metadata_clients = metadata_clients or {}
        self._artwork_manager = artwork_manager

    async def add_or_update_item(
        self,
        category_id: str,
        name: str,
        *,
        source: str = "manual",
        enrich_metadata: bool = True,
        sync_watch: bool = True,
        invalidate_lifecycle: bool = True,
        **kwargs: Any,
    ) -> Any:
        """Create/update a category item and synchronize dependent services.

        Existing items are updated in place instead of replaced with a fresh
        default model.  This avoids losing user choices such as language,
        quality, auto-download, provider IDs, or category-owned properties when
        the UI/agent repeats an add for an already-tracked item.
        """
        category = self._require_category(category_id)
        settings = self._settings_manager.settings
        item_id = str(name or "").strip()
        if not item_id:
            raise ValueError("Category item name is required")

        existing = self._find_item(settings, category_id, item_id)
        if existing is not None:
            item = existing
            self._apply_item_updates(item, kwargs)
        else:
            item = category.create_item(item_id, **self._item_creation_kwargs(kwargs))

        context = self._context(settings, source=source, enrich_metadata=enrich_metadata)
        item = await category.enrich_item_on_add(item, context)
        self._replace_settings_item(settings, category_id, item_id, item)
        self._settings_manager.save(settings)
        await self._upsert_repo_item(category_id, getattr(item, "key", item_id), item)
        reason = "add_or_update" if source == "manual" else f"{source}:add_or_update"
        await self._after_item_changed(
            category_id,
            getattr(item, "key", item_id),
            item,
            reason=reason,
            sync_watch=sync_watch,
            invalidate_lifecycle=invalidate_lifecycle,
        )
        logger.info(f"Category item added/updated through coordinator: {category_id}:{item_id} source={source}")
        return item

    async def update_item(self, category_id: str, item_id: str, **kwargs: Any) -> Any | None:
        """Update an existing category item and resynchronize watch state."""
        self._require_category(category_id)
        settings = self._settings_manager.settings
        item = self._find_item(settings, category_id, item_id)
        if not item:
            return None
        self._apply_item_updates(item, kwargs)
        self._settings_manager.save(settings)
        await self._upsert_repo_item(category_id, item_id, item)
        await self._after_item_changed(category_id, item_id, item, reason="update")
        return item

    async def remove_item(self, category_id: str, item_id: str) -> None:
        """Remove a category item and tear down dependent watch state."""
        self._require_category(category_id)
        settings = self._settings_manager.settings
        if hasattr(settings, "tracked_items"):
            settings.tracked_items.items = [
                item for item in settings.tracked_items.items
                if not self._item_matches(item, category_id, item_id)
            ]
            self._settings_manager.save(settings)
        if self._repo_available() and hasattr(self._db.media, "delete_category_item"):
            await self._db.media.delete_category_item(category_id, item_id)
        if self._scheduler and hasattr(self._scheduler, "sync_category_watch_policy"):
            await self._scheduler.sync_category_watch_policy(category_id, item_id, item=None, reason="remove")
        if self._scheduler and hasattr(self._scheduler, "invalidate_item_lifecycle"):
            try:
                await self._scheduler.invalidate_item_lifecycle(category_id, item_id, reason="remove")
            except Exception as exc:
                logger.debug("Lifecycle invalidation failed for removed %s/%s: %s", category_id, item_id, exc)
        logger.info(f"Category item removed through coordinator: {category_id}:{item_id}")

    def _context(self, settings: Any, *, source: str, enrich_metadata: bool) -> CategoryItemMutationContext:
        return CategoryItemMutationContext(
            settings=settings,
            db=self._db,
            metadata_enricher=self._metadata_enricher,
            metadata_clients=dict(self._metadata_clients),
            artwork_manager=self._artwork_manager,
            category_registry=self._category_registry,
            source=source,
            enrich_metadata=enrich_metadata,
        )

    async def _after_item_changed(
        self,
        category_id: str,
        item_id: str,
        item: Any,
        *,
        reason: str,
        sync_watch: bool = True,
        invalidate_lifecycle: bool = True,
    ) -> None:
        if not self._scheduler:
            return
        if invalidate_lifecycle and hasattr(self._scheduler, "invalidate_item_lifecycle"):
            try:
                await self._scheduler.invalidate_item_lifecycle(category_id, item_id, reason=reason)
            except Exception as exc:
                logger.debug("Lifecycle invalidation failed for %s/%s: %s", category_id, item_id, exc)
        if sync_watch and hasattr(self._scheduler, "sync_category_watch_policy"):
            await self._scheduler.sync_category_watch_policy(category_id, item_id, item=item, reason=reason)

    def _require_category(self, category_id: str) -> Any:
        if not self._category_registry:
            raise ValueError("Category registry is not available")
        category = self._category_registry.get(category_id)
        if not category:
            raise ValueError(f"Unknown category: {category_id}")
        return category

    def _find_item(self, settings: Any, category_id: str, item_id: str) -> Any | None:
        for item in getattr(settings, "tracked_items", []) or []:
            if self._item_matches(item, category_id, item_id):
                return item
        return None

    def _replace_settings_item(self, settings: Any, category_id: str, item_id: str, item: Any) -> None:
        if not hasattr(settings, "tracked_items"):
            return
        settings.tracked_items.items = [
            existing for existing in settings.tracked_items.items
            if not self._item_matches(existing, category_id, item_id)
        ]
        settings.tracked_items.append(item)

    def _item_matches(self, item: Any, category_id: str, item_id: str) -> bool:
        item_category = getattr(item, "item_type", getattr(item, "category_id", category_id))
        item_key = str(getattr(item, "key", "") or "")
        return item_key == str(item_id) and str(item_category) == str(category_id)

    def _item_creation_kwargs(self, kwargs: Mapping[str, Any]) -> dict[str, Any]:
        control = {"source", "enrich_metadata", "sync_watch", "invalidate_lifecycle"}
        return {key: value for key, value in dict(kwargs).items() if key not in control}

    def _apply_item_updates(self, item: Any, kwargs: Mapping[str, Any]) -> None:
        ignored = {"category_id", "item_id", "name", "key", "source", "enrich_metadata", "sync_watch", "invalidate_lifecycle"}
        for key, raw_value in dict(kwargs).items():
            if key in ignored:
                continue
            value = self._coerce_item_update_value(key, raw_value)
            if hasattr(item, key):
                setattr(item, key, value)
            else:
                if not hasattr(item, "properties") or getattr(item, "properties") is None:
                    setattr(item, "properties", {})
                item.properties[key] = value

    def _coerce_item_update_value(self, key: str, value: Any) -> Any:
        if key == "auto_download":
            return self._coerce_optional_bool(value)
        return value

    @staticmethod
    def _coerce_optional_bool(value: Any) -> bool | None:
        if value is None:
            return None
        if isinstance(value, bool):
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

    async def _upsert_repo_item(self, category_id: str, item_id: str, item: Any) -> None:
        if self._repo_available() and hasattr(self._db.media, "upsert_category_item"):
            payload = self._item_payload(item)
            await self._db.media.upsert_category_item(category_id, item_id, payload)

    def _item_payload(self, item: Any) -> dict[str, Any]:
        if hasattr(item, "model_dump"):
            return item.model_dump(mode="json")
        if isinstance(item, Mapping):
            return dict(item)
        return {
            "key": str(getattr(item, "key", "") or ""),
            "display_name": getattr(item, "display_name", None),
            "category_id": getattr(item, "category_id", getattr(item, "item_type", None)),
            "item_type": getattr(item, "item_type", getattr(item, "category_id", None)),
        }

    def _repo_available(self) -> bool:
        return bool(self._db and getattr(self._db, "media", None))
