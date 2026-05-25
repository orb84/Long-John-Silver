"""
TVMaze metadata provider for episodic categories.
"""

from __future__ import annotations

from typing import Any

from src.core.categories.metadata.base import CategoryMetadataProvider


class TVMazeEpisodicMetadataProvider(CategoryMetadataProvider):
    """Episodic-category metadata adapter for TVMaze."""

    provider_name = "tvmaze"

    def __init__(self, client: Any = None) -> None:
        """Initialize with an optional TVMaze client."""
        self._client = client

    async def search(self, query: str, category: Any) -> list[dict[str, Any]]:
        """Search TVMaze show metadata."""
        if not self._client:
            return []
        result = await self._client.search_show(query)
        return result if isinstance(result, list) else [result]

    async def get_details(self, external_id: str, category: Any) -> dict[str, Any]:
        """Fetch TVMaze show details."""
        if not self._client:
            return {}
        return await self._client.get_show(external_id)
