"""Category-neutral public web research and evidence collection."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse, urlunparse

from loguru import logger

from src.core.models import (
    WebEvidence,
    WebEvidenceBundle,
    WebResearchRequest,
    WebResearchSource,
    WebSearchConfig,
    WebSearchHit,
)
from src.search.web.service import WebSearchService
from src.search.web.url_utils import normalize_search_result_url


class WebSourceClassifier:
    """Classify source domains without category-specific semantics."""

    _REFERENCE_HOST_PARTS = {
        "wikipedia.org",
        "wikidata.org",
        "imdb.com",
        "themoviedb.org",
        "tmdb.org",
        "tvmaze.com",
        "trakt.tv",
        "discogs.com",
        "musicbrainz.org",
        "goodreads.com",
        "openlibrary.org",
        "isfdb.org",
    }
    _NEWS_HOST_PARTS = {
        "variety.com",
        "deadline.com",
        "hollywoodreporter.com",
        "theguardian.com",
        "bbc.com",
        "bbc.co.uk",
        "reuters.com",
        "apnews.com",
        "ign.com",
        "polygon.com",
        "theverge.com",
        "rollingstone.com",
        "billboard.com",
    }
    _SOCIAL_HOST_PARTS = {
        "reddit.com",
        "x.com",
        "twitter.com",
        "facebook.com",
        "instagram.com",
        "tiktok.com",
        "threads.net",
        "youtube.com",
        "forum",
    }

    def classify(self, url: str, *, title: str = "", snippet: str = "") -> str:
        """Return a coarse source kind for ranking/provenance display."""
        host = urlparse(url).netloc.lower()
        if not host:
            return "unknown"
        if self._matches_any(host, self._REFERENCE_HOST_PARTS):
            return "reference"
        if self._matches_any(host, self._NEWS_HOST_PARTS):
            return "news"
        if self._matches_any(host, self._SOCIAL_HOST_PARTS):
            return "social"
        path = urlparse(url).path.lower()
        text = f"{title} {snippet} {path}".lower()
        if any(token in text for token in ("press", "news", "article", "interview", "announced")):
            return "news_like"
        if "official" in text or host.startswith("tv.apple.com") or host.endswith("apple.com"):
            return "candidate_official_or_primary"
        if any(token in text for token in ("episodes", "episode-guide", "release-schedule", "schedule")):
            return "schedule_like"
        return "unknown"

    @staticmethod
    def confidence_for(kind: str, *, fetched: bool) -> float:
        """Return a conservative evidence confidence before category interpretation."""
        base = {
            "reference": 0.58,
            "news": 0.62,
            "news_like": 0.52,
            "candidate_official_or_primary": 0.55,
            "schedule_like": 0.42,
            "social": 0.28,
            "unknown": 0.40,
        }.get(kind, 0.40)
        if fetched:
            base += 0.10
        return min(base, 0.75)

    @staticmethod
    def _matches_any(host: str, needles: set[str]) -> bool:
        return any(needle in host for needle in needles)


class WebResearchUrlCanonicalizer:
    """Normalize web evidence URLs for dedupe/provenance."""

    _TRACKING_PARAMS = {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "fbclid",
        "gclid",
        "mc_cid",
        "mc_eid",
    }

    def canonicalize(self, value: str) -> str:
        """Return a stable canonical form for a public http(s) URL."""
        normalized = normalize_search_result_url(value) or ""
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"}:
            return ""
        host = parsed.netloc.lower()
        path = re.sub(r"/+$", "", parsed.path or "/") or "/"
        query = self._clean_query(parsed.query)
        return urlunparse((parsed.scheme.lower(), host, path, "", query, ""))

    def _clean_query(self, query: str) -> str:
        if not query:
            return ""
        from urllib.parse import parse_qsl, urlencode

        pairs = []
        for key, value in parse_qsl(query, keep_blank_values=False):
            if key.lower() in self._TRACKING_PARAMS:
                continue
            pairs.append((key, value))
        return urlencode(pairs, doseq=True)


class WebResearchService:
    """Search, fetch, dedupe, score, and persist public web evidence.

    This service is intentionally category-neutral.  It collects public-source
    evidence and provenance; category extensions later decide whether an air
    date, book edition, album release, sports event, or other fact is supported.
    """

    def __init__(
        self,
        config: WebSearchConfig | None = None,
        *,
        web_reader: object | None = None,
        repository: object | None = None,
    ) -> None:
        self._config = config or WebSearchConfig()
        self._web_reader = web_reader
        self._repository = repository
        self._classifier = WebSourceClassifier()
        self._canonicalizer = WebResearchUrlCanonicalizer()

    async def collect_evidence(self, request: WebResearchRequest) -> WebEvidenceBundle:
        """Run a bounded research plan and return an evidence bundle."""
        logger.info(
            "WebResearchService: collect_evidence started intent={} category={} item={} query='{}' categories={} language={} time_range={} budget={}",
            request.intent,
            request.category_id or "none",
            request.item_id or request.item_name or "none",
            self._query_preview(request.query),
            request.categories,
            request.language,
            request.time_range or "none",
            request.budget.model_dump(),
        )
        if not request.query:
            return WebEvidenceBundle(
                topic="",
                intent=request.intent,
                ok=False,
                warnings=["Web research requires a non-empty query."],
                unresolved_questions=["No query was provided."],
            )
        queries = self._bounded_queries(request)
        bundle = WebEvidenceBundle(topic=request.query, intent=request.intent, facts_authoritative=False)
        seen_urls: set[str] = set()
        fetched_count = 0
        service = self._service_for_request(request)

        for query in queries:
            logger.info("WebResearchService: running discovery query='{}'", self._query_preview(query))
            query_log_id = await self._start_query_log(service, query, request)
            result = await service.search(query, max_results=request.max_results)
            bundle.provider = result.provider
            if query_log_id:
                bundle.query_log_ids.append(query_log_id)
            if not result.ok:
                message = result.error or f"Web search provider {result.provider} returned no usable results."
                logger.warning(
                    "WebResearchService: discovery query failed query='{}' provider={} error={} fallback_used={}",
                    self._query_preview(query),
                    result.provider,
                    message,
                    result.fallback_used,
                )
                bundle.warnings.append(message)
                await self._complete_query_log(query_log_id, status="failed", result_count=0, error_code="SEARCH_FAILED")
                continue

            await self._complete_query_log(query_log_id, status="ok", result_count=len(result.hits), error_code="")
            logger.info(
                "WebResearchService: discovery query returned {} hit(s) provider={} fallback_used={} query='{}'",
                len(result.hits),
                result.provider,
                result.fallback_used,
                self._query_preview(query),
            )
            if result.fallback_used:
                bundle.warnings.append(
                    f"Used degraded DuckDuckGo fallback because {result.primary_provider or 'the primary provider'} failed: {result.primary_error}"
                )
            for hit in self._ranked_hits(result.hits, request):
                canonical_url = self._canonicalizer.canonicalize(hit.url)
                if not canonical_url:
                    logger.debug("WebResearchService: skipped non-public/unusable URL from hit title='{}'", hit.title)
                    continue
                if canonical_url in seen_urls:
                    logger.debug("WebResearchService: deduped candidate URL {}", canonical_url)
                    continue
                seen_urls.add(canonical_url)
                source = self._source_from_hit(hit, query=query, canonical_url=canonical_url)
                bundle.sources.append(source)
                if fetched_count >= request.budget.max_urls_to_fetch:
                    source.fetch_status = "not_fetched_budget_exhausted"
                    logger.info("WebResearchService: fetch budget exhausted for {}", source.canonical_url)
                    await self._persist_source(source, request, query_log_id, status=source.fetch_status)
                    continue
                if request.budget.require_page_extraction_before_facts:
                    fetched_count += 1
                    await self._fetch_and_attach(source, request, query_log_id, bundle)
                else:
                    await self._persist_source(source, request, query_log_id, status="candidate")

        if not bundle.sources:
            bundle.unresolved_questions.append("No candidate public sources were discovered.")
        elif request.budget.require_page_extraction_before_facts and not bundle.evidence:
            bundle.unresolved_questions.append("Candidate sources were found, but none could be fetched/extracted as evidence.")
        bundle.ok = bool(bundle.evidence or (bundle.sources and not request.budget.require_page_extraction_before_facts))
        logger.info(
            "WebResearchService: collect_evidence finished ok={} sources={} fetched_evidence={} warnings={} unresolved={}",
            bundle.ok,
            len(bundle.sources),
            len(bundle.evidence),
            len(bundle.warnings),
            len(bundle.unresolved_questions),
        )
        return bundle

    def _bounded_queries(self, request: WebResearchRequest) -> list[str]:
        queries = [request.query, *request.additional_queries]
        deduped: list[str] = []
        seen: set[str] = set()
        for query in queries:
            key = query.casefold()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(query)
            if len(deduped) >= request.budget.max_searches:
                break
        return deduped


    def _ranked_hits(self, hits: list[WebSearchHit], request: WebResearchRequest) -> list[WebSearchHit]:
        """Return hits in a fetch order that favours likely evidence quality.

        Search providers already rank results, but metasearch ranking can surface
        stale SEO calendars or social pages above official/reference/news
        evidence.  This category-neutral pass only changes fetch order inside the
        bounded budget; it does not turn snippets into facts.
        """
        current_topic = bool(request.time_range or any(str(cat).lower() == "news" for cat in request.categories))

        def key(hit: WebSearchHit) -> tuple[float, int]:
            canonical = self._canonicalizer.canonicalize(hit.url) or hit.url
            kind = self._classifier.classify(canonical, title=hit.title, snippet=hit.snippet)
            base = {
                "candidate_official_or_primary": 0.0,
                "news": 0.5 if current_topic else 1.5,
                "reference": 1.0,
                "news_like": 1.8,
                "schedule_like": 3.0,
                "unknown": 4.0,
                "social": 5.0,
            }.get(kind, 4.0)
            # A dated result is more useful for current/future questions, but
            # source type still dominates date presence.
            if current_topic and str(hit.published_at or "").strip():
                base -= 0.25
            return (base, int(hit.rank or 9999))

        ranked = sorted(hits, key=key)
        logger.debug(
            "WebResearchService: ranked {} hit(s) for fetch order query='{}' current_topic={}",
            len(ranked),
            self._query_preview(request.query),
            current_topic,
        )
        return ranked

    def _service_for_request(self, request: WebResearchRequest) -> WebSearchService:
        config = WebSearchConfig(**self._config.model_dump())
        config.default_categories = request.categories or config.default_categories
        if request.language and request.language != "auto":
            config.default_language = request.language
        return WebSearchService(config, time_range=request.time_range)

    async def _start_query_log(self, service: WebSearchService, query: str, request: WebResearchRequest) -> int | None:
        if not self._repository:
            return None
        try:
            return await self._repository.start_query_log(
                provider=getattr(service, "provider_name", self._config.provider),
                query=query,
                parameters={
                    "categories": request.categories,
                    "language": request.language,
                    "time_range": request.time_range,
                    "max_results": request.max_results,
                    "budget": request.budget.model_dump(),
                },
                intent=request.intent,
                category_id=request.category_id,
                item_id=request.item_id,
            )
        except Exception as exc:
            logger.debug(f"Failed to start web research query log: {exc}")
            return None

    async def _complete_query_log(self, query_log_id: int | None, *, status: str, result_count: int, error_code: str) -> None:
        if not self._repository or not query_log_id:
            return
        try:
            await self._repository.complete_query_log(
                query_log_id,
                status=status,
                result_count=result_count,
                error_code=error_code,
            )
        except Exception as exc:
            logger.debug(f"Failed to complete web research query log: {exc}")

    def _source_from_hit(self, hit: WebSearchHit, *, query: str, canonical_url: str) -> WebResearchSource:
        kind = self._classifier.classify(canonical_url, title=hit.title, snippet=hit.snippet)
        return WebResearchSource(
            title=hit.title,
            url=hit.url,
            canonical_url=canonical_url,
            snippet=hit.snippet,
            source_name=hit.source,
            source_kind=kind,
            rank=hit.rank,
            query=query,
            fetched=False,
            fetch_status="search_result_only",
            published_at=hit.published_at,
            confidence=self._classifier.confidence_for(kind, fetched=False),
        )

    async def _fetch_and_attach(
        self,
        source: WebResearchSource,
        request: WebResearchRequest,
        query_log_id: int | None,
        bundle: WebEvidenceBundle,
    ) -> None:
        if not self._web_reader:
            source.fetch_status = "not_fetched_reader_unavailable"
            bundle.warnings.append(f"No WebReader is available to fetch {source.canonical_url}.")
            await self._persist_source(source, request, query_log_id, status=source.fetch_status)
            return
        try:
            logger.info("WebResearchService: fetching candidate source kind={} url={}", source.source_kind, source.canonical_url)
            read_result = await self._web_reader.read_url(source.canonical_url)
        except Exception as exc:
            source.fetch_status = "fetch_error"
            await self._persist_source(source, request, query_log_id, status="fetch_error", error=str(exc))
            bundle.warnings.append(f"Failed to fetch {source.canonical_url}: {exc}")
            logger.warning("WebResearchService: fetch raised url={} error={}", source.canonical_url, exc)
            return

        if not read_result or read_result.get("ok") is False:
            source.fetch_status = "fetch_failed"
            error = str((read_result or {}).get("error") or "Page could not be fetched.")
            status_code = (read_result or {}).get("status")
            if isinstance(status_code, int):
                source.status_code = status_code
            await self._persist_source(source, request, query_log_id, status="fetch_failed", error=error)
            bundle.warnings.append(f"Candidate source could not be fetched: {source.canonical_url}")
            logger.warning("WebResearchService: fetch failed url={} status={} error={}", source.canonical_url, source.status_code, error)
            return

        content = str(read_result.get("content") or "")
        title = str(read_result.get("title") or source.title or "")
        final_url = str(read_result.get("url") or source.canonical_url)
        canonical = self._canonicalizer.canonicalize(final_url) or source.canonical_url
        source.title = title or source.title
        source.canonical_url = canonical
        source.fetched = True
        source.fetch_status = "fetched"
        source.confidence = self._classifier.confidence_for(source.source_kind, fetched=True)
        snippet = self._snippet(content) or source.snippet
        text_hash = hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest() if content else ""
        evidence_id = await self._persist_source(
            source,
            request,
            query_log_id,
            status="fetched",
            content=content,
            extracted_text_hash=text_hash,
            snippet=snippet,
        )
        source.evidence_id = evidence_id
        logger.info(
            "WebResearchService: fetched evidence url={} evidence_id={} confidence={} hash={}",
            source.canonical_url,
            evidence_id,
            source.confidence,
            text_hash[:12] if text_hash else "none",
        )
        bundle.evidence.append(WebEvidence(
            claim=f"Fetched public source for {request.intent or 'web research'}",
            value=title or source.canonical_url,
            source_name=source.source_name or urlparse(source.canonical_url).netloc,
            url=source.canonical_url,
            snippet=snippet,
            confidence=source.confidence,
            extracted_at=datetime.now(timezone.utc),
        ))

    async def _persist_source(
        self,
        source: WebResearchSource,
        request: WebResearchRequest,
        query_log_id: int | None,
        *,
        status: str,
        content: str = "",
        extracted_text_hash: str = "",
        snippet: str = "",
        error: str = "",
    ) -> int | None:
        if not self._repository:
            return None
        try:
            return await self._repository.upsert_source_evidence(
                query_log_id=query_log_id,
                category_id=request.category_id,
                item_id=request.item_id,
                url=source.url,
                canonical_url=source.canonical_url,
                title=source.title,
                source_kind=source.source_kind,
                source_name=source.source_name,
                fetched_at=datetime.now(timezone.utc).isoformat() if status == "fetched" else "",
                published_at=source.published_at,
                extracted_text_hash=extracted_text_hash,
                confidence=source.confidence,
                snippet=snippet or source.snippet,
                evidence={
                    "intent": request.intent,
                    "query": source.query,
                    "rank": source.rank,
                    "search_snippet": source.snippet,
                    "content_preview": self._snippet(content, max_chars=1800) if content else "",
                    "facts_authoritative": False,
                },
                status=status,
                error=error,
            )
        except Exception as exc:
            logger.debug(f"Failed to persist web source evidence for {source.canonical_url}: {exc}")
            return None

    @staticmethod
    def _query_preview(query: str, *, max_chars: int = 120) -> str:
        cleaned = re.sub(r"\s+", " ", str(query or "")).strip()
        if len(cleaned) <= max_chars:
            return cleaned
        return cleaned[: max_chars - 1].rstrip() + "…"

    @staticmethod
    def _snippet(text: str, *, max_chars: int = 700) -> str:
        cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
        if len(cleaned) <= max_chars:
            return cleaned
        return cleaned[: max_chars - 1].rstrip() + "…"
