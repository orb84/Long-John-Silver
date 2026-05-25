"""
Web search provider interfaces for LJS.

This module defines the small abstraction used by assistant-facing web search.
Concrete providers live in sibling modules and return normalized models from
``src.core.models`` so the rest of the agent does not depend on provider JSON.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.core.models import WebSearchHealth, WebSearchResult


class WebSearchProvider(ABC):
    """Abstract base class for configurable general web search providers."""

    provider_name: str = "unknown"

    @abstractmethod
    async def search(self, query: str, max_results: int = 5) -> WebSearchResult:
        """Search the web and return normalized hits."""

    @abstractmethod
    async def health_check(self) -> WebSearchHealth:
        """Return provider configuration and connectivity health."""
