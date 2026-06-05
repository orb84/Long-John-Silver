"""Configurable web-search service with explicit degraded fallback tracing."""

from __future__ import annotations

from loguru import logger

from src.core.models import WebSearchConfig, WebSearchHealth, WebSearchResult
from src.search.web.base import WebSearchProvider
from src.search.web.brave import BraveSearchProvider
from src.search.web.duckduckgo_html import DuckDuckGoHtmlSearchProvider
from src.search.web.kagi import KagiSearchProvider
from src.search.web.searxng import SearXNGSearchProvider
from src.search.web.tavily import TavilySearchProvider


class WebSearchService:
    """Selects and executes the configured general web search provider."""

    def __init__(self, config: WebSearchConfig | None = None, *, time_range: str = "") -> None:
        """Initialize from a WebSearchConfig object."""
        self._config = config or WebSearchConfig()
        self._time_range = str(time_range or "").strip()
        self._provider = self._build_provider(self._config, time_range=self._time_range)
        self._fallback = DuckDuckGoHtmlSearchProvider(
            enabled=self._config.allow_duckduckgo_fallback,
            api_base=self._config.api_base if self._config.provider == "duckduckgo_html" else "",
        )

    async def search(self, query: str, max_results: int | None = None) -> WebSearchResult:
        """Search the configured provider and optionally explicit fallback."""
        limit = max_results or self._config.max_results
        query_preview = self._query_preview(query)
        if not self._config.enabled:
            logger.info("WebSearchService: skipped disabled search query='{}'", query_preview)
            return WebSearchResult(query=query, provider="disabled", ok=False, error="Web search is disabled.")

        logger.info(
            "WebSearchService: primary search provider={} mode={} query='{}' limit={} fallback_allowed={}",
            self._provider.provider_name,
            getattr(self._config, "mode", ""),
            query_preview,
            limit,
            bool(self._config.allow_duckduckgo_fallback),
        )
        result = await self._provider.search(query, max_results=limit)
        if result.ok:
            logger.info(
                "WebSearchService: primary provider={} returned {} hit(s) query='{}'",
                result.provider,
                len(result.hits),
                query_preview,
            )
            return result

        if self._provider.provider_name == "duckduckgo_html":
            logger.warning(
                "WebSearchService: DuckDuckGo fallback provider failed query='{}' error={}",
                query_preview,
                result.error or "unknown",
            )
            return result

        logger.warning(
            "WebSearchService: primary provider={} failed query='{}' error={} fallback_allowed={}",
            self._provider.provider_name,
            query_preview,
            result.error or "unknown",
            bool(self._config.allow_duckduckgo_fallback),
        )
        if self._config.allow_duckduckgo_fallback:
            fallback_result = await self._fallback.search(query, max_results=limit)
            fallback_result.primary_provider = result.provider or self._provider.provider_name
            fallback_result.primary_error = result.error or ""
            fallback_result.primary_error_code = getattr(result, "error_code", "") or "PRIMARY_FAILED"
            if fallback_result.ok:
                fallback_result.fallback_used = True
                logger.warning(
                    "WebSearchService: degraded DuckDuckGo fallback succeeded query='{}' hits={} primary_provider={} primary_error={}",
                    query_preview,
                    len(fallback_result.hits),
                    fallback_result.primary_provider,
                    fallback_result.primary_error,
                )
                return fallback_result
            logger.error(
                "WebSearchService: degraded DuckDuckGo fallback also failed query='{}' fallback_error={} primary_error={}",
                query_preview,
                fallback_result.error or "unknown",
                result.error or "unknown",
            )
        else:
            logger.info("WebSearchService: degraded fallback disabled for query='{}'", query_preview)
        return result

    async def health_check(self) -> WebSearchHealth:
        """Return health for the configured primary provider."""
        if not self._config.enabled:
            logger.info("WebSearchService: health check skipped because web search is disabled")
            return WebSearchHealth(provider="disabled", configured=False, ok=False, last_error="Web search is disabled.")
        logger.info(
            "WebSearchService: health check provider={} mode={} endpoint={} fallback_allowed={}",
            self._provider.provider_name,
            getattr(self._config, "mode", ""),
            getattr(self._config, "api_base", ""),
            bool(self._config.allow_duckduckgo_fallback),
        )
        return await self._provider.health_check()

    @property
    def provider_name(self) -> str:
        """Return the active provider name for logs/provenance."""
        return self._provider.provider_name

    @staticmethod
    def _build_provider(config: WebSearchConfig, *, time_range: str = "") -> WebSearchProvider:
        """Create the provider named in settings."""
        provider = (config.provider or "").lower().strip()
        if provider == "brave":
            return BraveSearchProvider(api_key=config.api_key, api_base=config.api_base)
        if provider == "tavily":
            return TavilySearchProvider(api_key=config.api_key, api_base=config.api_base)
        if provider == "kagi":
            return KagiSearchProvider(api_key=config.api_key, api_base=config.api_base)
        if provider == "searxng":
            return SearXNGSearchProvider(
                api_base=config.api_base,
                api_key=config.api_key,
                categories=config.default_categories,
                language=config.default_language,
                safe_search=config.safe_search,
                timeout_seconds=config.request_timeout_seconds,
                time_range=time_range,
            )
        return DuckDuckGoHtmlSearchProvider(
            enabled=config.allow_duckduckgo_fallback or provider == "duckduckgo_html",
            api_base=config.api_base,
        )

    @staticmethod
    def _query_preview(query: str, *, max_chars: int = 120) -> str:
        """Return a bounded query preview for diagnostics without huge log lines."""
        cleaned = " ".join(str(query or "").split())
        if len(cleaned) <= max_chars:
            return cleaned
        return cleaned[: max_chars - 1].rstrip() + "…"
