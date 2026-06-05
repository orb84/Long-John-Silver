"""
DuckDuckGo HTML web search fallback for LJS.

DuckDuckGo HTML scraping is intentionally implemented as a last-resort provider.
It has no official stability guarantees and should not be the default healthy
production search path when a real provider can be configured.
"""

from __future__ import annotations

from urllib.parse import urlencode

from src.search.web.url_utils import normalize_search_result_url

import httpx
from bs4 import BeautifulSoup
from loguru import logger

from src.core.models import WebSearchHealth, WebSearchHit, WebSearchResult
from src.search.web.base import WebSearchProvider


class DuckDuckGoHtmlSearchProvider(WebSearchProvider):
    """Last-resort provider using DuckDuckGo's public HTML results page."""

    provider_name = "duckduckgo_html"

    def __init__(self, enabled: bool = False, api_base: str = "") -> None:
        """Initialize the fallback provider.

        Args:
            enabled: Whether the fallback is explicitly allowed.
            api_base: Optional alternate DuckDuckGo-compatible HTML endpoint.
        """
        self._enabled = enabled
        self._api_base = api_base.rstrip("/") or "https://html.duckduckgo.com/html/"
        self._last_error: str | None = None

    async def search(self, query: str, max_results: int = 5) -> WebSearchResult:
        """Search DuckDuckGo HTML and normalize result cards."""
        query_preview = self._query_preview(query)
        if not self._enabled:
            logger.info("DuckDuckGoHtmlSearchProvider: fallback disabled query='{}'", query_preview)
            return WebSearchResult(
                query=query,
                provider=self.provider_name,
                ok=False,
                error="DuckDuckGo HTML fallback is disabled in settings.",
                error_code="FALLBACK_DISABLED",
            )
        try:
            url = f"{self._api_base}?{urlencode({'q': query})}"
            logger.warning("DuckDuckGoHtmlSearchProvider: degraded fallback search query='{}' endpoint={}", query_preview, self._api_base)
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                response = await client.get(url, headers={"User-Agent": "LJS/1.0"})
                response.raise_for_status()
            hits = self.parse_html(response.text, max_results)
            self._last_error = None if hits else "No results parsed from DuckDuckGo HTML."
            logger.warning(
                "DuckDuckGoHtmlSearchProvider: degraded fallback returned {} hit(s) query='{}'",
                len(hits),
                query_preview,
            )
            return WebSearchResult(query=query, provider=self.provider_name, ok=bool(hits), hits=hits, error=self._last_error, error_code="" if hits else "NO_RESULTS")
        except Exception as exc:
            self._last_error = str(exc)
            logger.warning("DuckDuckGoHtmlSearchProvider: degraded fallback failed query='{}' error={}", query_preview, exc)
            return WebSearchResult(query=query, provider=self.provider_name, ok=False, error=self._last_error, error_code="FALLBACK_FAILED")

    async def health_check(self) -> WebSearchHealth:
        """Return fallback configuration health without making a network call."""
        return WebSearchHealth(
            provider=self.provider_name,
            configured=self._enabled,
            ok=self._enabled and self._last_error is None,
            last_error=self._last_error,
        )

    @staticmethod
    def parse_html(html: str, max_results: int) -> list[WebSearchHit]:
        """Parse DuckDuckGo HTML into normalized hit models."""
        soup = BeautifulSoup(html, "html.parser")
        hits: list[WebSearchHit] = []
        for rank, item in enumerate(soup.find_all("div", class_="result"), start=1):
            title_el = item.find("a", class_="result__a")
            if not title_el:
                continue
            snippet_el = item.find("a", class_="result__snippet") or item.find("div", class_="result__snippet")
            title = title_el.get_text(" ", strip=True)
            url = normalize_search_result_url(title_el.get("href", ""))
            if not title or not url:
                continue
            hits.append(
                WebSearchHit(
                    title=title,
                    url=url,
                    snippet=snippet_el.get_text(" ", strip=True) if snippet_el else "",
                    source="DuckDuckGo",
                    rank=rank,
                )
            )
            if len(hits) >= max_results:
                break
        return hits

    @staticmethod
    def _query_preview(query: str, *, max_chars: int = 120) -> str:
        cleaned = " ".join(str(query or "").split())
        if len(cleaned) <= max_chars:
            return cleaned
        return cleaned[: max_chars - 1].rstrip() + "…"

    _parse_html = parse_html
