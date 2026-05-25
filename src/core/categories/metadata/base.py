"""
Category metadata provider interfaces for LJS.

Metadata lookups are owned by categories so movie, TV, anime, and future
categories can choose providers without global assistant tool special cases.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.categories.base import MediaCategory


class CategoryMetadataProvider(ABC):
    """Abstract metadata provider used through a category boundary."""

    provider_name: str = ""

    @abstractmethod
    async def search(self, query: str, category: MediaCategory) -> list[dict[str, Any]]:
        """Search provider metadata for a category-specific query."""

    @abstractmethod
    async def get_details(self, external_id: str, category: MediaCategory) -> dict[str, Any]:
        """Fetch provider details for one external identifier."""
