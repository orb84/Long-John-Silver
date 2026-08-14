"""Canonical authority for completed-download duplicate decisions.

Historical download rows are transfer history, not proof that a logical
category unit is still present in the library.  This collaborator asks the
owning category to interpret the current canonical library object before a
manual queue request is blocked by a terminal ``complete`` row.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable

from loguru import logger

from src.core.models import DownloadImportContext


@dataclass(frozen=True)
class CompletedDownloadDecision:
    """Tri-state canonical decision for a requested download unit."""

    verified: bool
    satisfied: bool | None
    category_id: str = ""
    item_id: str = ""
    unit_label: str = ""
    source: str = "canonical_library"
    reason: str = ""

    @property
    def retry_completed_row(self) -> bool:
        """Return whether a stale terminal row may be retried safely."""
        return self.verified and self.satisfied is False

    def as_receipt(self) -> dict[str, Any]:
        """Return a compact, serializable audit payload."""
        return {
            key: value
            for key, value in {
                "verified": self.verified,
                "satisfied": self.satisfied,
                "category_id": self.category_id,
                "item_id": self.item_id,
                "unit_label": self.unit_label,
                "source": self.source,
                "reason": self.reason,
                "retry_completed_row": self.retry_completed_row,
            }.items()
            if value not in (None, "")
        }


class CompletedDownloadAuthority:
    """Verify logical-unit presence through category-owned canonical objects."""

    def __init__(
        self,
        *,
        settings_manager: Any,
        category_registry: Any,
        category_context_factory: Callable[[], Any],
    ) -> None:
        """Store collaborators required to build and interpret library objects."""
        self._settings_manager = settings_manager
        self._categories = category_registry
        self._context_factory = category_context_factory

    async def evaluate(
        self,
        *,
        import_context: DownloadImportContext | dict[str, Any] | None,
        category_id: str,
        item_name: str,
    ) -> CompletedDownloadDecision:
        """Return a verified present/absent decision when enough evidence exists."""
        context = self._coerce_context(import_context)
        resolved_category = str((context.category_id if context else "") or category_id or "")
        item_id = str((context.item_id if context else "") or item_name or "")
        descriptor = dict((context.unit_descriptor if context else {}) or {})
        unit_label = str(descriptor.get("label") or descriptor.get("stable_key") or "").strip()
        if not resolved_category or not item_id or not descriptor:
            return self._unknown(resolved_category, item_id, unit_label, "structured category identity is incomplete")

        category = self._category(resolved_category)
        hook = getattr(category, "canonical_download_satisfaction", None)
        if not callable(hook):
            return self._unknown(resolved_category, item_id, unit_label, "owning category has no canonical completion hook")

        try:
            runtime_context = self._context_factory()
            builder = getattr(runtime_context, "library_objects", None) or getattr(
                runtime_context,
                "library_object_builder",
                None,
            )
        except Exception as exc:
            logger.warning(
                "Canonical completion context creation failed for {}/{} {}: {}",
                resolved_category,
                item_id,
                unit_label,
                exc,
            )
            return self._unknown(
                resolved_category,
                item_id,
                unit_label,
                f"canonical library context creation failed: {exc}",
            )
        if builder is None or not hasattr(builder, "build"):
            return self._unknown(resolved_category, item_id, unit_label, "canonical library builder is unavailable")

        settings_item = self._tracked_item(resolved_category, item_id, item_name)
        try:
            maybe_object = builder.build(resolved_category, item_id, settings_item=settings_item)
            canonical = await maybe_object if hasattr(maybe_object, "__await__") else maybe_object
            satisfied = hook(canonical, descriptor)
        except Exception as exc:
            logger.warning(
                "Canonical completion verification failed for {}/{} {}: {}",
                resolved_category,
                item_id,
                unit_label,
                exc,
            )
            return self._unknown(resolved_category, item_id, unit_label, f"canonical verification failed: {exc}")

        if satisfied is None:
            return self._unknown(resolved_category, item_id, unit_label, "category could not interpret the requested descriptor")
        return CompletedDownloadDecision(
            verified=True,
            satisfied=bool(satisfied),
            category_id=resolved_category,
            item_id=item_id,
            unit_label=unit_label,
            reason=(
                "requested logical unit is present in the canonical library"
                if satisfied
                else "requested logical unit is absent from the canonical library"
            ),
        )

    @staticmethod
    def _coerce_context(value: DownloadImportContext | dict[str, Any] | None) -> DownloadImportContext | None:
        """Return a typed import context without raising on legacy payloads."""
        if isinstance(value, DownloadImportContext):
            return value
        if not isinstance(value, dict):
            return None
        try:
            return DownloadImportContext(**value)
        except Exception:
            return None

    def _category(self, category_id: str) -> Any | None:
        """Resolve one category without assuming registry implementation details."""
        if not self._categories:
            return None
        try:
            return self._categories.get(category_id)
        except Exception:
            return None

    def _tracked_item(self, category_id: str, item_id: str, item_name: str) -> Any:
        """Return the configured item or a minimal category-neutral item envelope."""
        settings = getattr(self._settings_manager, "settings", None)
        for item in getattr(settings, "tracked_items", []) or []:
            item_category = str(getattr(item, "item_type", getattr(item, "category_id", "")) or "")
            item_key = str(getattr(item, "key", "") or "")
            if item_category == category_id and item_key in {item_id, item_name}:
                return item
        return SimpleNamespace(key=item_id, item_type=category_id, discovered=False)

    @staticmethod
    def _unknown(category_id: str, item_id: str, unit_label: str, reason: str) -> CompletedDownloadDecision:
        """Return a conservative unverified decision."""
        return CompletedDownloadDecision(
            verified=False,
            satisfied=None,
            category_id=category_id,
            item_id=item_id,
            unit_label=unit_label,
            reason=reason,
        )
