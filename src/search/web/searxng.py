"""SearXNG provider for private/self-hosted web search."""

from __future__ import annotations

import httpx

from src.core.models import WebSearchHealth, WebSearchHit, WebSearchResult
from src.search.web.base import WebSearchProvider


class SearXNGSearchProvider(WebSearchProvider):
    """Web search provider backed by a SearXNG JSON endpoint."""

    provider_name = "searxng"

    def __init__(self, api_base: str, api_key: str = "") -> None:
        self._api_base = api_base.rstrip("/")
        self._api_key = api_key.strip()
        self._last_error: str | None = None

    async def search(self, query: str, max_results: int = 5) -> WebSearchResult:
        """Search SearXNG and normalize result records."""
        if not self._api_base:
            return WebSearchResult(query=query, provider=self.provider_name, ok=False, error="SearXNG base URL is missing.")
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else None
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                response = await client.get(
                    f"{self._api_base}/search",
                    params={"q": query, "format": "json", "categories": "general"},
                    headers=headers,
                )
                response.raise_for_status()
            results = response.json().get("results", [])
            hits = [
                WebSearchHit(
                    title=str(item.get("title") or ""),
                    url=str(item.get("url") or ""),
                    snippet=str(item.get("content") or ""),
                    source="SearXNG",
                    rank=index + 1,
                )
                for index, item in enumerate(results[:max_results])
                if item.get("title") and item.get("url")
            ]
            self._last_error = None if hits else "SearXNG returned no results."
            return WebSearchResult(query=query, provider=self.provider_name, ok=bool(hits), hits=hits, error=self._last_error)
        except Exception as exc:
            self._last_error = str(exc)
            return WebSearchResult(query=query, provider=self.provider_name, ok=False, error=self._last_error)

    async def health_check(self) -> WebSearchHealth:
        """Return whether SearXNG is configured and the last observed status."""
        return WebSearchHealth(provider=self.provider_name, configured=bool(self._api_base), ok=bool(self._api_base) and self._last_error is None, last_error=self._last_error)
