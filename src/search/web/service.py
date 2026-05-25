"""
Configurable web search service for LJS.

The service selects one primary provider from settings and only uses the
DuckDuckGo HTML scraper when explicitly enabled as a fallback. This prevents
assistant research from silently depending on fragile scraping while still
allowing a manual degraded mode for users who prefer it.
"""

from __future__ import annotations

from src.core.models import WebSearchConfig, WebSearchHealth, WebSearchResult
from src.search.web.base import WebSearchProvider
from src.search.web.brave import BraveSearchProvider
from src.search.web.duckduckgo_html import DuckDuckGoHtmlSearchProvider
from src.search.web.kagi import KagiSearchProvider
from src.search.web.searxng import SearXNGSearchProvider
from src.search.web.tavily import TavilySearchProvider


class WebSearchService:
    """Selects and executes the configured general web search provider."""

    def __init__(self, config: WebSearchConfig | None = None) -> None:
        """Initialize from a WebSearchConfig object."""
        self._config = config or WebSearchConfig()
        self._provider = self._build_provider(self._config)
        self._fallback = DuckDuckGoHtmlSearchProvider(
            enabled=self._config.allow_duckduckgo_fallback,
            api_base=self._config.api_base if self._config.provider == "duckduckgo_html" else "",
        )

    async def search(self, query: str, max_results: int | None = None) -> WebSearchResult:
        """Search the configured provider and optionally explicit fallback."""
        if not self._config.enabled:
            return WebSearchResult(query=query, provider="disabled", ok=False, error="Web search is disabled.")
        limit = max_results or self._config.max_results
        result = await self._provider.search(query, max_results=limit)
        if result.ok or self._provider.provider_name == "duckduckgo_html":
            return result
        if self._config.allow_duckduckgo_fallback:
            fallback_result = await self._fallback.search(query, max_results=limit)
            if fallback_result.ok:
                return fallback_result
        return result

    async def health_check(self) -> WebSearchHealth:
        """Return health for the configured primary provider."""
        if not self._config.enabled:
            return WebSearchHealth(provider="disabled", configured=False, ok=False, last_error="Web search is disabled.")
        return await self._provider.health_check()

    @staticmethod
    def _build_provider(config: WebSearchConfig) -> WebSearchProvider:
        """Create the provider named in settings."""
        provider = (config.provider or "").lower().strip()
        if provider == "brave":
            return BraveSearchProvider(api_key=config.api_key, api_base=config.api_base)
        if provider == "tavily":
            return TavilySearchProvider(api_key=config.api_key, api_base=config.api_base)
        if provider == "kagi":
            return KagiSearchProvider(api_key=config.api_key, api_base=config.api_base)
        if provider == "searxng":
            return SearXNGSearchProvider(api_base=config.api_base, api_key=config.api_key)
        return DuckDuckGoHtmlSearchProvider(
            enabled=config.allow_duckduckgo_fallback or provider == "duckduckgo_html",
            api_base=config.api_base,
        )
