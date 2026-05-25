"""
Brave Search API provider for LJS.

Brave is the preferred simple production provider because it exposes a stable
JSON API with a straightforward API-key authentication model.
"""

from __future__ import annotations

import httpx

from src.core.models import WebSearchHealth, WebSearchHit, WebSearchResult
from src.search.web.base import WebSearchProvider


class BraveSearchProvider(WebSearchProvider):
    """Web search provider backed by the Brave Search API."""

    provider_name = "brave"

    def __init__(self, api_key: str, api_base: str = "") -> None:
        """Initialize with Brave API credentials."""
        self._api_key = api_key.strip()
        self._api_base = api_base.rstrip("/") or "https://api.search.brave.com/res/v1/web/search"
        self._last_error: str | None = None

    async def search(self, query: str, max_results: int = 5) -> WebSearchResult:
        """Search Brave and normalize web results."""
        if not self._api_key:
            return WebSearchResult(query=query, provider=self.provider_name, ok=False, error="Brave API key is missing.")
        try:
            headers = {"Accept": "application/json", "X-Subscription-Token": self._api_key}
            params = {"q": query, "count": max(1, min(max_results, 20))}
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(self._api_base, params=params, headers=headers)
                response.raise_for_status()
            web_results = response.json().get("web", {}).get("results", [])
            hits = [
                WebSearchHit(
                    title=str(item.get("title") or ""),
                    url=str(item.get("url") or ""),
                    snippet=str(item.get("description") or ""),
                    source="Brave",
                    rank=index + 1,
                )
                for index, item in enumerate(web_results[:max_results])
                if item.get("title") and item.get("url")
            ]
            self._last_error = None if hits else "Brave returned no web results."
            return WebSearchResult(query=query, provider=self.provider_name, ok=bool(hits), hits=hits, error=self._last_error)
        except Exception as exc:
            self._last_error = str(exc)
            return WebSearchResult(query=query, provider=self.provider_name, ok=False, error=self._last_error)

    async def health_check(self) -> WebSearchHealth:
        """Return whether Brave is configured and the last observed status."""
        return WebSearchHealth(provider=self.provider_name, configured=bool(self._api_key), ok=bool(self._api_key) and self._last_error is None, last_error=self._last_error)
