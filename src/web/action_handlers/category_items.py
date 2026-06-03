"""
Category item action handlers for LJS.

Provides category-generic item mutations and category action delegation so the
web layer no longer needs permanent TV-show-specific action names.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from src.core.models import ActionReceipt
from src.core.category_item_coordinator import CategoryItemCoordinator


class CategoryItemActionHandler:
    """Handlers for category-generic item actions routed through ActionGateway.

    The handler deliberately delegates item construction to the selected
    category. That keeps the web/action layer free of permanent TV/movie/show
    conditionals while still allowing categories to create rich item models.
    """

    def __init__(
        self,
        settings_manager: Any,
        category_registry: Any,
        database: Any = None,
        scheduler: Any = None,
        metadata_enricher: Any = None,
        metadata_clients: dict[str, Any] | None = None,
        artwork_manager: Any = None,
    ) -> None:
        """Initialize with settings, categories, database, and optional scheduler."""
        self._settings_manager = settings_manager
        self._category_registry = category_registry
        self._db = database
        self._scheduler = scheduler
        self._coordinator = CategoryItemCoordinator(
            settings_manager=settings_manager,
            category_registry=category_registry,
            db=database,
            scheduler=scheduler,
            metadata_enricher=metadata_enricher,
            metadata_clients=metadata_clients or {},
            artwork_manager=artwork_manager,
        )

    async def add(self, category_id: str, name: str, **kwargs: Any) -> dict[str, Any]:
        """Add a tracked item under the selected category."""
        self._require_category(category_id)
        item = await self._coordinator.add_or_update_item(category_id, name, **kwargs)
        logger.info(f"Added category item {category_id}:{name}")
        return {"status": "ok", "category_id": category_id, "item_id": name, "item": item.model_dump(mode="json")}

    async def remove(self, category_id: str, item_id: str) -> dict[str, Any]:
        """Remove a tracked item from the selected category."""
        await self._coordinator.remove_item(category_id, item_id)
        logger.info(f"Removed category item {category_id}:{item_id}")
        return {"status": "ok", "category_id": category_id, "item_id": item_id}

    async def update(self, category_id: str, item_id: str, **kwargs: Any) -> dict[str, Any]:
        """Update fields on a tracked category item."""
        ignored = {"category_id", "item_id", "name", "key"}
        changed = {key: value for key, value in kwargs.items() if key not in ignored}
        item = await self._coordinator.update_item(category_id, item_id, **kwargs)
        if not item:
            return {"status": "not_found", "category_id": category_id, "item_id": item_id}
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

    # Category item mutations are intentionally not implemented here.  The web
    # action handler delegates to CategoryItemCoordinator so UI, assistant tools,
    # automation, and library discovery share one add/update/remove pipeline.

    def _repo_available(self) -> bool:
        """Return whether the injected database exposes the media repository."""
        return bool(self._db and getattr(self._db, "media", None))
