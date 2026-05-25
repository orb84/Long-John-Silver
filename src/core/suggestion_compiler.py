"""
Generic suggestion compiler for LJS.

The compiler coordinates category-owned suggestion workflows and persists their
results through the lifecycle ledger. It must not contain TV/movie-specific
business logic; category-specific suggestion behavior lives under category-owned
workflow modules while the core only decides whether a saved item-scoped
suggestion set is still valid.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loguru import logger

from src.core.category_lifecycle import CategoryLifecycleEngine
from src.core.library_objects import CanonicalLibraryObjectBuilder
from src.core.models import CategoryItem

@dataclass(slots=True)
class SuggestionWorkflowContext:
    """Dependencies exposed to category-owned suggestion workflow factories."""

    db: Any
    tmdb_client: Any | None = None
    tvmaze_client: Any | None = None
    settings_manager: Any | None = None
    category_registry: Any | None = None
    library_object_builder: CanonicalLibraryObjectBuilder | None = None


if TYPE_CHECKING:
    from src.core.database import Database
    from src.integrations.tmdb import TMDBClient
    from src.integrations.tvmaze import TVMazeClient
    from src.core.config import SettingsManager


class SuggestionCompiler:
    """Dispatch suggestion compilation to category-owned workflows.

    This class is intentionally a thin router.  Adding a new category should
    register a workflow here or through a future registry extension; it should
    not add category-specific heuristics to the compiler itself.
    """

    def __init__(
        self,
        db: "Database",
        tmdb_client: "TMDBClient | None" = None,
        tvmaze_client: "TVMazeClient | None" = None,
        settings_manager: "SettingsManager | None" = None,
        category_registry: object | None = None,
        lifecycle_engine: CategoryLifecycleEngine | None = None,
    ) -> None:
        """Initialize the compiler and lifecycle-aware category workflows."""
        self._db = db
        self._categories = category_registry
        self._lifecycle = lifecycle_engine or CategoryLifecycleEngine(db=db, category_registry=category_registry, settings_manager=settings_manager)
        self._library_objects = CanonicalLibraryObjectBuilder(db=db, category_registry=category_registry)
        self._workflow_context = SuggestionWorkflowContext(
            db=db,
            tmdb_client=tmdb_client,
            tvmaze_client=tvmaze_client,
            settings_manager=settings_manager,
            category_registry=category_registry,
            library_object_builder=self._library_objects,
        )
        self._workflows: dict[str, object | None] = {}
        self._missing_workflow_logged: set[str] = set()

    async def compile_all(self, items: list[CategoryItem], *, force: bool = False) -> int:
        """Compile due suggestions for enabled category items.

        Existing item-scoped suggestions are reused when lifecycle fingerprints
        and ``next_check_at`` say they are still valid. Unsupported categories
        are still reconciled into the ledger so they can opt into suggestions
        later without startup churn.
        """
        total = 0
        for item in items:
            if not getattr(item, "enabled", True):
                continue
            workflow = self._workflow_for_category(item.item_type)
            if not workflow:
                await self._lifecycle.reconcile_item(item, reason="suggestions_no_workflow")
                self._log_missing_workflow_once(item.item_type)
                continue
            total += await self._lifecycle.compile_suggestions_for_item(item, workflow, force=force)
        return total

    async def compile_for_item(self, item: CategoryItem, *, force: bool = False) -> int:
        """Compile suggestions for one category item if due or forced."""
        workflow = self._workflow_for_category(item.item_type)
        if not workflow:
            await self._lifecycle.reconcile_item(item, reason="suggestions_no_workflow")
            self._log_missing_workflow_once(item.item_type)
            return 0
        return await self._lifecycle.compile_suggestions_for_item(item, workflow, force=force)


    def _workflow_for_category(self, category_id: str) -> object | None:
        """Return the suggestion workflow declared by the owning category.

        The compiler is a category-neutral coordinator.  It must never import a
        TV/movie/game/book workflow directly; categories expose their optional
        workflow through ``create_suggestion_workflow`` so new categories are
        added by registering category packages, not editing this core file.
        """
        if category_id in self._workflows:
            return self._workflows[category_id]
        category = self._categories.get(category_id) if self._categories else None
        workflow = None
        if category and hasattr(category, "create_suggestion_workflow"):
            workflow = category.create_suggestion_workflow(self._workflow_context)
        self._workflows[category_id] = workflow
        return workflow

    def _log_missing_workflow_once(self, category_id: str) -> None:
        """Avoid burying real warnings under one debug line per movie/item."""
        if category_id in self._missing_workflow_logged:
            return
        self._missing_workflow_logged.add(category_id)
        logger.debug(f"No suggestion workflow registered for category '{category_id}'.")
