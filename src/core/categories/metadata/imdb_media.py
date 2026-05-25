"""
IMDb metadata provider placeholder for media categories.
"""

from __future__ import annotations

from typing import Any

from src.core.categories.metadata.base import CategoryMetadataProvider


class IMDbMediaMetadataProvider(CategoryMetadataProvider):
    """IMDb adapter used by categories that opt into IMDb metadata."""

    provider_name = "imdb"

    def __init__(self, client: Any = None) -> None:
        """Initialize with an optional IMDb-compatible client."""
        self._client = client

    async def search(self, query: str, category: Any) -> list[dict[str, Any]]:
        """Search IMDb metadata when a client is available."""
        if not self._client:
            return []
        result = await self._client.search(query)
        return result if isinstance(result, list) else [result]

    async def get_details(self, external_id: str, category: Any) -> dict[str, Any]:
        """Fetch IMDb details when a client is available."""
        if not self._client:
            return {}
        return await self._client.get_details(external_id)
