"""Tavily Search API provider for LJS."""

from __future__ import annotations

import httpx

from src.core.models import WebSearchHealth, WebSearchHit, WebSearchResult
from src.search.web.base import WebSearchProvider


class TavilySearchProvider(WebSearchProvider):
    """LLM-oriented web search provider backed by Tavily."""

    provider_name = "tavily"

    def __init__(self, api_key: str, api_base: str = "") -> None:
        self._api_key = api_key.strip()
        self._api_base = api_base.rstrip("/") or "https://api.tavily.com/search"
        self._last_error: str | None = None

    async def search(self, query: str, max_results: int = 5) -> WebSearchResult:
        """Search Tavily and normalize the results list."""
        if not self._api_key:
            return WebSearchResult(query=query, provider=self.provider_name, ok=False, error="Tavily API key is missing.")
        payload = {"api_key": self._api_key, "query": query, "max_results": max_results, "include_answer": False}
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(self._api_base, json=payload)
                response.raise_for_status()
            results = response.json().get("results", [])
            hits = [
                WebSearchHit(
                    title=str(item.get("title") or ""),
                    url=str(item.get("url") or ""),
                    snippet=str(item.get("content") or ""),
                    source="Tavily",
                    rank=index + 1,
                )
                for index, item in enumerate(results[:max_results])
                if item.get("title") and item.get("url")
            ]
            self._last_error = None if hits else "Tavily returned no results."
            return WebSearchResult(query=query, provider=self.provider_name, ok=bool(hits), hits=hits, error=self._last_error)
        except Exception as exc:
            self._last_error = str(exc)
            return WebSearchResult(query=query, provider=self.provider_name, ok=False, error=self._last_error)

    async def health_check(self) -> WebSearchHealth:
        """Return whether Tavily is configured and the last observed status."""
        return WebSearchHealth(provider=self.provider_name, configured=bool(self._api_key), ok=bool(self._api_key) and self._last_error is None, last_error=self._last_error)
