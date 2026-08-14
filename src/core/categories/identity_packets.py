"""Response packet construction for category identity resolution."""

from __future__ import annotations

from typing import Any


class CategoryIdentityPackets:
    """Build consistent resolved, ambiguous, and unresolved identity packets."""

    def __init__(self, category_registry: Any) -> None:
        self._registry = category_registry

    def resolved(
        self,
        item_name: str,
        candidate: dict[str, Any],
        candidates: list[dict[str, Any]],
        *,
        source: str,
    ) -> dict[str, Any]:
        """Build the successful identity packet."""
        return {
            "status": "resolved",
            "resolved": True,
            "item_name": item_name,
            "category_id": candidate.get("category_id"),
            "confidence": float(candidate.get("score") or candidate.get("base_score") or 1.0),
            "source": source,
            "reason": f"Unique {source.replace('_', ' ')} evidence matched this title.",
            "candidates": candidates[:6],
            "clarification_required": False,
        }

    def ambiguous(
        self,
        item_name: str,
        candidates: list[dict[str, Any]],
        *,
        reason: str,
        router_hints: list[str] | None = None,
    ) -> dict[str, Any]:
        """Build a clarification packet for competing category evidence."""
        categories = [str(row.get("category_id")) for row in candidates if row.get("category_id")]
        labels = [self._category_label(category_id) for category_id in dict.fromkeys(categories)]
        return {
            "status": "ambiguous",
            "resolved": False,
            "item_name": item_name,
            "category_id": None,
            "confidence": 0.0,
            "reason": reason,
            "ambiguous_categories": list(dict.fromkeys(categories)),
            "router_hints": router_hints or [],
            "candidates": candidates[:6],
            "clarification_required": True,
            "clarification_question": self._question(item_name, labels),
        }

    def unresolved(
        self,
        item_name: str,
        candidates: list[dict[str, Any]],
        reason: str,
        *,
        router_hints: list[str] | None = None,
        metadata_attempted: bool = False,
    ) -> dict[str, Any]:
        """Build a fail-closed packet when no category can be verified."""
        labels = [self._category_label(category_id) for category_id in self._installed_category_ids()]
        return {
            "status": "unresolved",
            "resolved": False,
            "item_name": item_name,
            "category_id": None,
            "confidence": 0.0,
            "reason": reason,
            "router_hints": router_hints or [],
            "metadata_attempted": metadata_attempted,
            "web_search_recommended": metadata_attempted and not candidates,
            "candidates": candidates[:6],
            "clarification_required": True,
            "clarification_question": self._question(item_name, labels),
        }

    def _installed_category_ids(self) -> list[str]:
        if not self._registry or not hasattr(self._registry, "list_ids"):
            return []
        return [category_id for category_id in self._registry.list_ids() if category_id != "media"]

    def _category_label(self, category_id: str) -> str:
        try:
            category = self._registry.get(category_id)
        except Exception:
            category = None
        return str(getattr(category, "display_name", "") or category_id)

    @staticmethod
    def _question(item_name: str, labels: list[str]) -> str:
        compact = [label for label in labels if label][:5]
        if compact:
            return f"What kind of content is '{item_name}' — {', '.join(compact)}?"
        return f"What kind of content is '{item_name}'?"
