"""
TMDB-backed metadata providers owned by movie and TV categories.
"""

from __future__ import annotations

from typing import Any

from src.core.categories.metadata.base import CategoryMetadataProvider


class TMDBMovieMetadataProvider(CategoryMetadataProvider):
    """Movie-category metadata adapter for TMDB."""

    provider_name = "tmdb"

    def __init__(self, client: Any = None) -> None:
        """Initialize with an optional TMDB client."""
        self._client = client

    async def search(self, query: str, category: Any) -> list[dict[str, Any]]:
        """Search TMDB movie metadata."""
        if not self._client:
            return []
        result = await self._client.search_movie(query)
        return result if isinstance(result, list) else [result]

    async def get_details(self, external_id: str, category: Any) -> dict[str, Any]:
        """Fetch TMDB movie details."""
        if not self._client:
            return {}
        return await self._client.get_movie_details(external_id)


class TMDBTvMetadataProvider(CategoryMetadataProvider):
    """TV-category metadata adapter for TMDB."""

    provider_name = "tmdb"

    def __init__(self, client: Any = None) -> None:
        """Initialize with an optional TMDB client."""
        self._client = client

    async def search(self, query: str, category: Any) -> list[dict[str, Any]]:
        """Search TMDB TV metadata."""
        if not self._client:
            return []
        result = await self._client.search_tv(query)
        return result if isinstance(result, list) else [result]

    async def get_details(self, external_id: str, category: Any) -> dict[str, Any]:
        """Fetch TMDB TV details."""
        if not self._client:
            return {}
        return await self._client.get_tv_details(external_id)
