"""SearXNG provider for private/self-hosted web research."""

from __future__ import annotations

from typing import Any

import httpx
from loguru import logger

from src.core.models import WebSearchHealth, WebSearchHit, WebSearchResult
from src.search.web.base import WebSearchProvider


class SearXNGSearchProvider(WebSearchProvider):
    """Web search provider backed by a SearXNG JSON endpoint."""

    provider_name = "searxng"

    def __init__(
        self,
        api_base: str,
        api_key: str = "",
        *,
        categories: list[str] | None = None,
        language: str = "auto",
        safe_search: int = 1,
        timeout_seconds: float = 8.0,
        time_range: str = "",
    ) -> None:
        self._api_base = api_base.rstrip("/")
        self._api_key = api_key.strip()
        self._categories = [str(c).strip() for c in (categories or ["general"]) if str(c).strip()] or ["general"]
        self._language = str(language or "auto").strip() or "auto"
        self._safe_search = max(0, min(int(safe_search or 0), 2))
        self._timeout_seconds = max(1.0, float(timeout_seconds or 8.0))
        self._time_range = str(time_range or "").strip()
        self._last_error: str | None = None
        self._last_error_code: str = ""
        self._last_status_code: int | None = None

    async def search(self, query: str, max_results: int = 5) -> WebSearchResult:
        """Search SearXNG and normalize result records."""
        if not self._api_base:
            return self._failed(query, "CONFIG_MISSING", "SearXNG base URL is missing.")
        params = self._params(query, page=1)
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else None
        query_preview = self._query_preview(query)
        logger.info(
            "SearXNGSearchProvider: search endpoint={} query='{}' categories={} language={} time_range={} limit={}",
            f"{self._api_base}/search",
            query_preview,
            self._categories,
            self._language,
            self._time_range or "none",
            max_results,
        )
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds, follow_redirects=True) as client:
                response = await client.get(f"{self._api_base}/search", params=params, headers=headers)
            self._last_status_code = response.status_code
            logger.info(
                "SearXNGSearchProvider: response status={} query='{}' content_type={}",
                response.status_code,
                query_preview,
                response.headers.get("content-type", ""),
            )
            if response.status_code == 403:
                return self._failed(query, "JSON_FORMAT_DISABLED", "SearXNG is reachable, but JSON output is disabled in settings.yml.", status_code=403)
            if response.status_code == 429:
                return self._failed(query, "RATE_LIMITED", "SearXNG or an upstream engine is rate-limited.", status_code=429)
            if response.status_code >= 400:
                return self._failed(query, "PROVIDER_HTTP_ERROR", f"SearXNG returned HTTP {response.status_code}.", status_code=response.status_code)
            try:
                payload = response.json()
            except ValueError:
                return self._failed(query, "INVALID_PROVIDER_RESPONSE", "SearXNG returned a non-JSON response.", status_code=response.status_code)
            results = payload.get("results", []) if isinstance(payload, dict) else []
            hits = self._normalize_hits(results, max_results=max_results)
            self._last_error = None if hits else "SearXNG returned no results."
            self._last_error_code = "" if hits else "NO_RESULTS"
            logger.info(
                "SearXNGSearchProvider: normalized {} hit(s) query='{}' error_code={}",
                len(hits),
                query_preview,
                self._last_error_code or "none",
            )
            return WebSearchResult(query=query, provider=self.provider_name, ok=bool(hits), hits=hits, error=self._last_error, error_code=self._last_error_code)
        except httpx.ConnectError as exc:
            return self._failed(query, "PROVIDER_UNREACHABLE", f"SearXNG is not reachable: {exc}")
        except httpx.TimeoutException:
            return self._failed(query, "PROVIDER_TIMEOUT", "SearXNG request timed out.")
        except Exception as exc:
            return self._failed(query, "PROVIDER_ERROR", str(exc))

    async def health_check(self) -> WebSearchHealth:
        """Probe the configured endpoint and verify JSON search is enabled."""
        if not self._api_base:
            logger.warning("SearXNGSearchProvider: health check failed because base URL is missing")
            return WebSearchHealth(provider=self.provider_name, configured=False, ok=False, last_error="SearXNG base URL is missing.", error_code="CONFIG_MISSING")
        logger.info("SearXNGSearchProvider: health probe starting endpoint={}", f"{self._api_base}/search")
        result = await self.search("ljs-health-check", max_results=1)
        json_ok = result.ok or self._last_error_code == "NO_RESULTS"
        logger.info(
            "SearXNGSearchProvider: health probe finished ok={} json_api={} status={} error_code={}",
            json_ok,
            json_ok,
            self._last_status_code,
            "" if json_ok else self._last_error_code,
        )
        return WebSearchHealth(
            provider=self.provider_name,
            configured=True,
            ok=json_ok,
            last_error=None if json_ok else result.error,
            error_code="" if json_ok else self._last_error_code,
            status_code=self._last_status_code,
            endpoint=f"{self._api_base}/search",
            json_api=json_ok,
        )

    def _params(self, query: str, *, page: int) -> dict[str, Any]:
        params: dict[str, Any] = {
            "q": query,
            "format": "json",
            "categories": ",".join(self._categories),
            "pageno": max(1, int(page)),
            "safesearch": self._safe_search,
        }
        if self._language and self._language != "auto":
            params["language"] = self._language
        if self._time_range:
            params["time_range"] = self._time_range
        return params

    def _normalize_hits(self, results: Any, *, max_results: int) -> list[WebSearchHit]:
        if not isinstance(results, list):
            return []
        seen: set[str] = set()
        hits: list[WebSearchHit] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            if not title or not url or url in seen:
                continue
            seen.add(url)
            engines = item.get("engines") or item.get("engine") or []
            if isinstance(engines, str):
                engines = [engines]
            if not isinstance(engines, list):
                engines = []
            source = ", ".join(str(engine) for engine in engines if str(engine).strip()) or "SearXNG"
            hits.append(WebSearchHit(
                title=title,
                url=url,
                snippet=str(item.get("content") or item.get("snippet") or ""),
                source=source,
                rank=len(hits) + 1,
                category=str(item.get("category") or ""),
                published_at=str(item.get("publishedDate") or item.get("published_date") or ""),
                engines=[str(engine) for engine in engines if str(engine).strip()],
            ))
            if len(hits) >= max(1, int(max_results or 5)):
                break
        return hits

    def _failed(self, query: str, code: str, message: str, *, status_code: int | None = None) -> WebSearchResult:
        self._last_error = message
        self._last_error_code = code
        self._last_status_code = status_code
        logger.warning(
            "SearXNGSearchProvider: failed query='{}' code={} status={} message={}",
            self._query_preview(query),
            code,
            status_code,
            message,
        )
        return WebSearchResult(query=query, provider=self.provider_name, ok=False, error=message, error_code=code)

    @staticmethod
    def _query_preview(query: str, *, max_chars: int = 120) -> str:
        cleaned = " ".join(str(query or "").split())
        if len(cleaned) <= max_chars:
            return cleaned
        return cleaned[: max_chars - 1].rstrip() + "…"
