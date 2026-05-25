"""Kagi Search API provider for LJS."""

from __future__ import annotations

import httpx

from src.core.models import WebSearchHealth, WebSearchHit, WebSearchResult
from src.search.web.base import WebSearchProvider


class KagiSearchProvider(WebSearchProvider):
    """High-quality account-backed web search provider using Kagi."""

    provider_name = "kagi"

    def __init__(self, api_key: str, api_base: str = "") -> None:
        self._api_key = api_key.strip()
        self._api_base = api_base.rstrip("/") or "https://kagi.com/api/v0/search"
        self._last_error: str | None = None

    async def search(self, query: str, max_results: int = 5) -> WebSearchResult:
        """Search Kagi and normalize organic result records."""
        if not self._api_key:
            return WebSearchResult(query=query, provider=self.provider_name, ok=False, error="Kagi API key is missing.")
        try:
            headers = {"Authorization": f"Bot {self._api_key}"}
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(self._api_base, params={"q": query, "limit": max_results}, headers=headers)
                response.raise_for_status()
            data = response.json().get("data", [])
            hits: list[WebSearchHit] = []
            for item in data:
                if item.get("t") not in {0, "0", "search-result", None} and not item.get("url"):
                    continue
                title = str(item.get("title") or "")
                url = str(item.get("url") or "")
                if not title or not url:
                    continue
                hits.append(WebSearchHit(title=title, url=url, snippet=str(item.get("snippet") or ""), source="Kagi", rank=len(hits) + 1))
                if len(hits) >= max_results:
                    break
            self._last_error = None if hits else "Kagi returned no results."
            return WebSearchResult(query=query, provider=self.provider_name, ok=bool(hits), hits=hits, error=self._last_error)
        except Exception as exc:
            self._last_error = str(exc)
            return WebSearchResult(query=query, provider=self.provider_name, ok=False, error=self._last_error)

    async def health_check(self) -> WebSearchHealth:
        """Return whether Kagi is configured and the last observed status."""
        return WebSearchHealth(provider=self.provider_name, configured=bool(self._api_key), ok=bool(self._api_key) and self._last_error is None, last_error=self._last_error)
