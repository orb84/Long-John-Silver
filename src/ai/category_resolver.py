"""
Category resolver for LJS assistant runs.

Resolves the active category before prompt construction and tool exposure so
LLM calls are scoped to the category that owns the user's request.
"""

from __future__ import annotations

from src.core.categories.router_matching import router_token_matches
from src.core.models import AgentRunContext, CategoryResolution, Intent


class CategoryResolver:
    """Resolve the active category for one user prompt."""

    _INTENTS_REQUIRING_CATEGORY = {Intent.SEARCH, Intent.DOWNLOAD}

    def __init__(self, category_registry: object | None = None, settings: object | None = None) -> None:
        """Initialize the resolver.

        Args:
            category_registry: Registry exposing resolve_from_text() and get().
            settings: Application settings containing tracked_items.
        """
        self._registry = category_registry
        self._settings = settings

    def resolve(self, user_message: str, intent: Intent) -> object | None:
        """Return the most likely category instance for the prompt."""
        resolution = self.resolve_with_reason(user_message, intent)
        if not resolution.category_id or not self._registry:
            return None
        return self._registry.get(resolution.category_id)

    def resolve_with_reason(self, user_message: str, intent: Intent) -> CategoryResolution:
        """Resolve a category and return confidence/ambiguity details."""
        if not self._registry:
            return CategoryResolution(reason="Category registry unavailable.")

        tracked_items = getattr(self._settings, "tracked_items", None)
        if hasattr(self._registry, "resolve_from_text"):
            category = self._registry.resolve_from_text(user_message, tracked_items=tracked_items)
            if category:
                return CategoryResolution(
                    category_id=category.category_id,
                    confidence=0.9,
                    reason="Matched tracked item, category keyword, or router brief.",
                )

        ambiguous = self._matching_briefs(user_message)
        if len(ambiguous) > 1:
            return CategoryResolution(
                category_id=None,
                confidence=0.4,
                ambiguous_categories=ambiguous,
                reason="Multiple category router briefs matched.",
            )
        if len(ambiguous) == 1:
            return CategoryResolution(
                category_id=ambiguous[0],
                confidence=0.7,
                reason="Matched one category router brief.",
            )
        if intent in self._INTENTS_REQUIRING_CATEGORY:
            return CategoryResolution(
                category_id=None,
                confidence=0.0,
                reason="Intent requires a category, but no category matched.",
            )
        return CategoryResolution(category_id=None, confidence=0.0, reason="No category needed for this intent.")

    def build_context(self, user_message: str, intent: Intent) -> AgentRunContext:
        """Build an AgentRunContext with the resolved category ID."""
        resolution = self.resolve_with_reason(user_message, intent)
        return AgentRunContext(
            user_message=user_message,
            intent=intent,
            category_id=resolution.category_id,
            category_resolution=resolution,
        )

    def _matching_briefs(self, user_message: str) -> list[str]:
        """Return category IDs whose router brief keywords match the prompt."""
        if not self._registry or not hasattr(self._registry, "router_briefs"):
            return []
        matches: list[str] = []
        for brief in self._registry.router_briefs():
            keywords = list(getattr(brief, "keywords", []) or [])
            keywords.extend(getattr(brief, "item_types", []) or [])
            if any(router_token_matches(user_message, keyword) for keyword in keywords if keyword):
                matches.append(brief.category_id)
        return matches
