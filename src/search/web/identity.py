"""Bounded public-web evidence collection for category identity hooks.

The collector is deliberately category-neutral.  Categories author the query
and interpret the returned hits/pages; this layer only runs the configured
search provider, records degraded fallback provenance, and optionally fetches a
very small number of pages so snippets are not silently treated as durable
facts.
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from src.search.web.service import WebSearchService


class WebIdentitySearch:
    """Collect a small web evidence packet for one category-owned query."""

    _SEARCH_TIMEOUT_SECONDS = 12.0
    _PAGE_TIMEOUT_SECONDS = 8.0

    def __init__(self, config: Any, *, web_reader: Any = None) -> None:
        self._config = config
        self._web_reader = web_reader

    async def collect(
        self,
        query: str,
        *,
        max_results: int = 6,
        max_pages: int = 2,
    ) -> dict[str, Any]:
        """Return normalized search hits and fetched page text within hard bounds."""
        query = str(query or "").strip()
        if not query:
            return {"ok": False, "query": query, "hits": [], "pages": [], "error": "Empty identity query."}
        try:
            result = await asyncio.wait_for(
                WebSearchService(self._config).search(query, max_results=max(1, min(int(max_results), 8))),
                timeout=self._SEARCH_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning("Web identity search timed out query={!r}", query)
            return {"ok": False, "query": query, "hits": [], "pages": [], "error": "Web identity search timed out."}
        except Exception as exc:
            logger.warning("Web identity search failed query={!r}: {}", query, exc)
            return {"ok": False, "query": query, "hits": [], "pages": [], "error": str(exc)}

        hits = [hit.model_dump() if hasattr(hit, "model_dump") else dict(hit) for hit in (result.hits or [])]
        pages: list[dict[str, Any]] = []
        if result.ok and self._web_reader and max_pages > 0:
            urls = [str(hit.get("url") or "").strip() for hit in hits if hit.get("url")][: max(0, min(int(max_pages), 3))]
            tasks = [self._read_page(url) for url in urls]
            if tasks:
                fetched = await asyncio.gather(*tasks, return_exceptions=True)
                for url, row in zip(urls, fetched):
                    if isinstance(row, Exception) or not isinstance(row, dict):
                        continue
                    pages.append({"url": url, **row})

        return {
            "ok": bool(result.ok),
            "query": query,
            "provider": result.provider,
            "fallback_used": bool(getattr(result, "fallback_used", False)),
            "primary_provider": str(getattr(result, "primary_provider", "") or ""),
            "primary_error": str(getattr(result, "primary_error", "") or ""),
            "hits": hits,
            "pages": pages,
            "error": result.error,
        }

    async def _read_page(self, url: str) -> dict[str, Any] | None:
        try:
            row = await asyncio.wait_for(self._web_reader.read_url(url), timeout=self._PAGE_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            return {"ok": False, "error": "Page fetch timed out."}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return row if isinstance(row, dict) else None
