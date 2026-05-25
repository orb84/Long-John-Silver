"""
Category item action handlers for LJS.

Provides category-generic item mutations and category action delegation so the
web layer no longer needs permanent TV-show-specific action names.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from src.core.models import ActionReceipt


class CategoryItemActionHandler:
    """Handlers for category-generic item actions routed through ActionGateway.

    The handler deliberately delegates item construction to the selected
    category. That keeps the web/action layer free of permanent TV/movie/show
    conditionals while still allowing categories to create rich item models.
    """

    def __init__(self, settings_manager: Any, category_registry: Any, database: Any = None, scheduler: Any = None) -> None:
        """Initialize with settings, categories, database, and optional scheduler."""
        self._settings_manager = settings_manager
        self._category_registry = category_registry
        self._db = database
        self._scheduler = scheduler

    async def add(self, category_id: str, name: str, **kwargs: Any) -> dict[str, Any]:
        """Add a tracked item under the selected category."""
        category = self._require_category(category_id)
        settings = self._settings_manager.settings
        item = category.create_item(name, **kwargs)
        self._replace_settings_item(settings, category_id, name, item)
        self._settings_manager.save(settings)
        await self._upsert_repo_item(category_id, name, item)
        logger.info(f"Added category item {category_id}:{name}")
        return {"status": "ok", "category_id": category_id, "item_id": name, "item": item.model_dump(mode="json")}

    async def remove(self, category_id: str, item_id: str) -> dict[str, Any]:
        """Remove a tracked item from the selected category."""
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
        logger.info(f"Removed category item {category_id}:{item_id}")
        return {"status": "ok", "category_id": category_id, "item_id": item_id}

    async def update(self, category_id: str, item_id: str, **kwargs: Any) -> dict[str, Any]:
        """Update fields on a tracked category item."""
        self._require_category(category_id)
        settings = self._settings_manager.settings
        item = self._find_item(settings, category_id, item_id)
        if not item:
            return {"status": "not_found", "category_id": category_id, "item_id": item_id}

        changed: dict[str, Any] = {}
        ignored = {"category_id", "item_id", "name", "key"}
        for key, value in kwargs.items():
            if key in ignored:
                continue
            if hasattr(item, key):
                setattr(item, key, value)
                changed[key] = value
                continue

            # Unknown fields are not discarded. They are custom properties owned
            # by user-defined categories and validated by the category contract.
            if not hasattr(item, "properties") or getattr(item, "properties") is None:
                setattr(item, "properties", {})
            item.properties[key] = value
            changed[f"properties.{key}"] = value
        self._settings_manager.save(settings)
        await self._upsert_repo_item(category_id, item_id, item)
        return {"status": "ok", "category_id": category_id, "item_id": item_id, "updated": changed}

    async def pause(self, category_id: str, item_id: str) -> dict[str, Any]:
        """Pause a tracked category item."""
        result = await self.update(category_id, item_id, enabled=False)
        if self._repo_available() and hasattr(self._db.media, "set_category_item_paused"):
            await self._db.media.set_category_item_paused(category_id, item_id, True)
        return result

    async def resume(self, category_id: str, item_id: str) -> dict[str, Any]:
        """Resume a tracked category item."""
        result = await self.update(category_id, item_id, enabled=True)
        if self._repo_available() and hasattr(self._db.media, "set_category_item_paused"):
            await self._db.media.set_category_item_paused(category_id, item_id, False)
        return result

    async def execute_category_action(
        self,
        category_id: str,
        action_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> ActionReceipt:
        """Execute a category-declared action and return its receipt."""
        category = self._require_category(category_id)
        action = next((decl for decl in category.declare_actions() if decl.name == action_name), None)
        workflow_name = getattr(action, "operation", None) or action_name
        workflow_names = {workflow.name for workflow in category.declare_workflows()}
        if self._scheduler and hasattr(self._scheduler, "execute_category_workflow") and workflow_name in workflow_names:
            receipt = await self._scheduler.execute_category_workflow(category_id, workflow_name, arguments or {})
        else:
            context = {
                "database": self._db,
                "settings_manager": self._settings_manager,
                "settings": self._settings_manager.settings,
            }
            receipt = await category.execute_action(action_name, arguments or {}, context=context)
        if isinstance(receipt, ActionReceipt):
            return receipt
        return ActionReceipt(category_id=category_id, action_name=action_name, data={"result": receipt})

    def _require_category(self, category_id: str) -> Any:
        """Return a category or raise a ValueError."""
        if not self._category_registry:
            raise ValueError("Category registry is not available")
        category = self._category_registry.get(category_id)
        if not category:
            raise ValueError(f"Unknown category: {category_id}")
        return category

    def _find_item(self, settings: Any, category_id: str, item_id: str) -> Any:
        """Find a tracked item by category and key."""
        for item in getattr(settings, "tracked_items", []):
            if self._item_matches(item, category_id, item_id):
                return item
        return None

    def _replace_settings_item(self, settings: Any, category_id: str, item_id: str, item: Any) -> None:
        """Insert an item in settings, replacing an existing one with the same identity."""
        if not hasattr(settings, "tracked_items"):
            return
        settings.tracked_items.items = [
            existing for existing in settings.tracked_items.items
            if not self._item_matches(existing, category_id, item_id)
        ]
        settings.tracked_items.append(item)

    def _item_matches(self, item: Any, category_id: str, item_id: str) -> bool:
        """Return whether a settings item matches a category/item identity."""
        item_category = getattr(item, "item_type", getattr(item, "category_id", category_id))
        return getattr(item, "key", None) == item_id and item_category == category_id

    async def _upsert_repo_item(self, category_id: str, item_id: str, item: Any) -> None:
        """Persist a category item to the repository when available."""
        if self._repo_available() and hasattr(self._db.media, "upsert_category_item"):
            await self._db.media.upsert_category_item(category_id, item_id, item.model_dump(mode="json"))

    def _repo_available(self) -> bool:
        """Return whether the injected database exposes the media repository."""
        return bool(self._db and getattr(self._db, "media", None))
