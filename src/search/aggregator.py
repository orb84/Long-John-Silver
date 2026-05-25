"""
Search aggregator for LJS.

Queries all search providers in parallel using asyncio.gather, then
deduplicates, filters blacklisted items, softly pre-filters broken
results, applies release group reputation, and ranks results
by quality score. The LLM is the primary quality evaluator —
this code only removes structurally invalid results.

Each provider has a per-query timeout (default 15s) and retry
count (default 1) to ensure slow or flaky providers don't block
the entire search pipeline. A global semaphore caps concurrent
provider searches across all callers.
"""

import asyncio
import time
from datetime import datetime, timezone
from loguru import logger
from src.search.base import SearchProvider
from src.core.models import ProviderSearchDiagnostics, SearchAggregateResult, SearchResult, QualityProfile
from src.utils.detailed_logger import SearchLogger
from src.utils.quality import QualityAnalyzer
from src.utils.blacklist import BlacklistManager
from src.core.release_groups import ReleaseGroupTracker
from src.core.smart_quality import SmartQualityInferrer

# Per-provider search timeout in seconds and retry count.
# 35s accommodates browser fallback: httpx may fail fast (1-2s), then
# Playwright needs time for DNS, TLS, JavaScript, and Cloudflare challenges.
DEFAULT_PROVIDER_TIMEOUT = 20
DEFAULT_PROVIDER_RETRIES = 0
# Max concurrent provider searches across ALL callers. Prevents
# N simultaneous shows × M providers from saturating connections.
MAX_CONCURRENT_PROVIDER_SEARCHES = 4


class SearchAggregator:
    """Aggregates results from multiple search providers, deduplicates, and ranks them.

    Providers are queried concurrently for speed. Results are then
    deduplicated (first by magnet info-hash, then by normalized title
    — keeping the best-seeded copy of each unique release), filtered
    against the blacklist, pre-filtered against quality constraints
    (should_accept_result), boosted by release group reputation, and
    sorted by quality score.

    Each provider call has a timeout and retry policy so that slow
    or flaky providers don't block the pipeline.
    """

    def __init__(
        self,
        providers: list[SearchProvider],
        blacklist: BlacklistManager,
        quality_profile: QualityProfile | None = None,
        fallback_providers: list[SearchProvider] | None = None,
        release_group_tracker: ReleaseGroupTracker | None = None,
        provider_timeout: int = DEFAULT_PROVIDER_TIMEOUT,
        provider_retries: int = DEFAULT_PROVIDER_RETRIES,
        search_logger: SearchLogger | None = None,
    ):
        self._providers = providers
        self._fallback_providers = fallback_providers or []
        self._blacklist = blacklist
        self._quality_profile = quality_profile or QualityProfile()
        self._release_group_tracker = release_group_tracker
        self._provider_timeout = provider_timeout
        self._provider_retries = provider_retries
        self._search_semaphore = asyncio.Semaphore(MAX_CONCURRENT_PROVIDER_SEARCHES)
        self._search_logger = search_logger
        self._last_successful_search_at: str | None = None
        self._last_error: str | None = None
        self._provider_diagnostics: dict[str, ProviderSearchDiagnostics] = {}

    async def search(self, query: str, category: str | None = None,
                     preferred_language: str | None = None,
                     quality_profile: QualityProfile | None = None) -> list[SearchResult]:
        """Search all providers in parallel and merge results.

        Deduplicates by magnet link, filters blacklisted items,
        pre-filters against quality constraints, applies release
        group reputation boosts, and ranks by quality score.

        Args:
            query: The search query string.
            category: Registry category ID. Providers may use it for filtering.
            preferred_language: Optional language preference for quality scoring.
            quality_profile: Optional per-query quality profile. If omitted,
                uses the aggregator's default profile.
        """
        active_providers = [
            provider for provider in self._providers
            if self._provider_supports_category(provider, category)
        ]
        
        all_results, diagnostics = await self._search_providers_with_diagnostics(
            query, providers=active_providers, category=category,
        )
        profile = quality_profile or self._quality_profile
        ranked, quality_filtered, filtered, deduped = await self._prepare_results(
            all_results, preferred_language=preferred_language, quality_profile=profile,
        )

        fallback_used = False
        if not ranked and self._fallback_providers:
            fallback_providers = [
                provider for provider in self._fallback_providers
                if self._provider_supports_category(provider, category)
            ]
            if fallback_providers:
                logger.warning(
                    f"Primary torrent search returned no usable results for '{query}'. "
                    "Trying explicit direct-scraper fallback providers."
                )
                fallback_results, fallback_diagnostics = await self._search_providers_with_diagnostics(
                    query, providers=fallback_providers, category=category,
                )
                diagnostics.update({f"fallback:{key}": value for key, value in fallback_diagnostics.items()})
                ranked, quality_filtered, filtered, deduped = await self._prepare_results(
                    fallback_results, preferred_language=preferred_language, quality_profile=profile,
                )
                fallback_used = True

        self._provider_diagnostics = diagnostics
        logger.info(
            f"Aggregated {len(ranked)} results from "
            f"{len(active_providers)}/{len(self._providers)} primary providers "
            f"and {len(self._fallback_providers)} fallback providers "
            f"(fallback_used={fallback_used}) "
            f"(after quality filter: {len(quality_filtered)}/{len(filtered)}) "
            f"(query: '{query[:50]}')"
        )
        if self._search_logger:
            try:
                await self._search_logger.log_search(
                    query=query,
                    category=category or "all",
                    active_providers=[type(p).__name__ for p in active_providers],
                    total_raw=len(all_results),
                    unique_deduped=len(deduped),
                    quality_filtered=len(quality_filtered),
                )
            except Exception as le:
                logger.warning(f"Failed to log search details: {le}")
        if ranked:
            self._last_successful_search_at = datetime.now(timezone.utc).isoformat()
            self._last_error = None
        elif diagnostics:
            errors = [diag.error for diag in diagnostics.values() if diag.error]
            self._last_error = "; ".join(errors) if errors else "No providers returned results."
        return ranked

    async def search_with_diagnostics(self, query: str, category: str | None = None,
                                      preferred_language: str | None = None,
                                      quality_profile: QualityProfile | None = None) -> SearchAggregateResult:
        """Search providers and return ranked results plus provider diagnostics."""
        started = time.monotonic()
        active_providers = [
            provider for provider in self._providers
            if self._provider_supports_category(provider, category)
        ]
        all_results, diagnostics = await self._search_providers_with_diagnostics(
            query, providers=active_providers, category=category,
        )
        profile = quality_profile or self._quality_profile
        ranked, quality_filtered, filtered, deduped = await self._prepare_results(
            all_results, preferred_language=preferred_language, quality_profile=profile,
        )
        if not ranked and self._fallback_providers:
            fallback_providers = [
                provider for provider in self._fallback_providers
                if self._provider_supports_category(provider, category)
            ]
            fallback_results, fallback_diagnostics = await self._search_providers_with_diagnostics(
                query, providers=fallback_providers, category=category,
            )
            diagnostics.update({f"fallback:{key}": value for key, value in fallback_diagnostics.items()})
            ranked, quality_filtered, filtered, deduped = await self._prepare_results(
                fallback_results, preferred_language=preferred_language, quality_profile=profile,
            )
        self._provider_diagnostics = diagnostics
        if ranked:
            self._last_successful_search_at = datetime.now(timezone.utc).isoformat()
            self._last_error = None
        elif diagnostics:
            errors = [diag.error for diag in diagnostics.values() if diag.error]
            self._last_error = "; ".join(errors) if errors else "No providers returned results."
        return SearchAggregateResult(
            query=query,
            results=ranked,
            provider_results=diagnostics,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )

    async def health_check(self) -> dict:
        """Return actionable torrent provider health for API diagnostics."""
        providers = []
        for provider in self._providers:
            ok = False
            error = None
            try:
                ok = await provider.health_check()
            except Exception as exc:
                error = str(exc)
            providers.append({
                "name": provider.name,
                "ok": ok,
                "last_error": error or getattr(provider, "latest_error_category", None),
            })
        primary = providers[0]["name"] if providers else None
        return {
            "primary_provider": primary,
            "provider_count": len(providers),
            "fallback_provider_count": len(self._fallback_providers),
            "providers": providers,
            "degraded": not any(provider["ok"] for provider in providers),
            "last_successful_search_at": self._last_successful_search_at,
            "last_error": self._last_error,
        }


    def _provider_timeout_for(self, provider: SearchProvider) -> int:
        """Return the timeout for a provider, allowing direct fallbacks to be capped.

        Public scraper providers are often protected by Cloudflare or ISP-level
        filtering.  They should be quick degraded fallbacks, not long blocking
        operations that make a Jackett failure feel like the whole app froze.
        """
        timeout = getattr(provider, "timeout_seconds", None)
        if timeout is None:
            return self._provider_timeout
        try:
            return max(1, int(timeout))
        except (TypeError, ValueError):
            return self._provider_timeout

    @staticmethod
    def _provider_supports_category(provider: SearchProvider, category: str | None) -> bool:
        """Return whether a provider should be queried for a category ID."""
        if not category:
            return True
        supported = getattr(provider, "supported_categories", None)
        if not supported:
            return True
        return "*" in supported or category in supported

    async def _search_providers(self, query: str, providers: list[SearchProvider] | None = None) -> list[SearchResult]:
        """Query all providers concurrently with timeout and retry.

        Each provider gets its own timeout (``provider_timeout`` seconds).
        If a provider times out or fails, it is retried up to
        ``provider_retries`` times. Slow or flaky providers don't block
        the others — asyncio.gather runs all in parallel.

        Gated by a global semaphore to prevent N concurrent show checks
        from each fanning out to M providers simultaneously.
        """
        target_providers = providers if providers is not None else self._providers
        if not target_providers:
            return []

        async def _query_provider_with_retry(provider: SearchProvider, q: str) -> list[SearchResult]:
            """Query a single provider with retry and semaphore gating."""
            async with self._search_semaphore:
                for attempt in range(1 + self._provider_retries):
                    try:
                        return await asyncio.wait_for(
                            provider.search(q),
                            timeout=self._provider_timeout
                        )
                    except (asyncio.TimeoutError, Exception) as e:
                        if attempt < self._provider_retries:
                            logger.debug(f"Retrying {provider.name} after error: {e}")
                            continue
                        logger.warning(f"Provider {provider.name} failed after {attempt+1} attempts: {e}")
            return []

        tasks = [
            _query_provider_with_retry(p, query)
            for p in target_providers
        ]
        results_lists = await asyncio.gather(*tasks)
        return [res for sublist in results_lists for res in sublist]

    async def _search_providers_with_diagnostics(
        self,
        query: str,
        providers: list[SearchProvider] | None = None,
        category: str | None = None,
    ) -> tuple[list[SearchResult], dict[str, ProviderSearchDiagnostics]]:
        """Query providers and capture per-provider diagnostics."""
        target_providers = providers if providers is not None else self._providers
        diagnostics: dict[str, ProviderSearchDiagnostics] = {}
        if not target_providers:
            return [], diagnostics

        async def _query(provider: SearchProvider) -> list[SearchResult]:
            started = time.monotonic()
            try:
                async with self._search_semaphore:
                    results = await asyncio.wait_for(
                        self._call_provider_search(provider, query, category),
                        timeout=self._provider_timeout_for(provider),
                    )
                diagnostics[provider.name] = ProviderSearchDiagnostics(
                    provider=provider.name,
                    ok=True,
                    result_count=len(results),
                    magnet_count=sum(1 for result in results if result.magnet),
                    used_browser=False,
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                )
                return results
            except asyncio.TimeoutError:
                diagnostics[provider.name] = ProviderSearchDiagnostics(
                    provider=provider.name, ok=False, error=f"provider timeout after {self._provider_timeout_for(provider)}s",
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                )
            except Exception as exc:
                diagnostics[provider.name] = ProviderSearchDiagnostics(
                    provider=provider.name, ok=False, error=str(exc),
                    blocked_reason=getattr(provider, "latest_error_category", None),
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                )
            return []

        result_lists = await asyncio.gather(*[_query(provider) for provider in target_providers])
        return [result for result_list in result_lists for result in result_list], diagnostics


    async def _prepare_results(
        self,
        results: list[SearchResult],
        preferred_language: str | None,
        quality_profile: QualityProfile,
    ) -> tuple[list[SearchResult], list[SearchResult], list[SearchResult], list[SearchResult]]:
        """Deduplicate, filter, quality-check, and rank raw provider results."""
        deduped = self._deduplicate(results)
        filtered = self._blacklist.filter_results(deduped)
        quality_filtered = self._quality_filter(filtered, quality_profile=quality_profile)
        ranked = await self._rank(
            quality_filtered,
            preferred_language=preferred_language,
            quality_profile=quality_profile,
        )
        return ranked, quality_filtered, filtered, deduped

    async def _call_provider_search(
        self,
        provider: SearchProvider,
        query: str,
        category: str | None,
    ) -> list[SearchResult]:
        """Call a provider, passing category hints to providers that accept them."""
        try:
            return await provider.search(query, category=category)  # type: ignore[call-arg]
        except TypeError:
            return await provider.search(query)

    def _deduplicate(self, results: list[SearchResult]) -> list[SearchResult]:
        """Remove duplicate results by magnet link or hash, then by title.

        Primary deduplication uses the magnet info-hash. A secondary pass
        removes results sharing the same normalized title, keeping only the
        best-seeded one. This prevents the same release mirrored on
        different trackers/providers (with different info-hashes) from
        appearing as duplicates in the result list.
        """
        # Pass 1: Deduplicate by magnet info-hash
        seen_hashes = set()
        unique_by_hash = []
        for r in results:
            if not r.magnet:
                unique_by_hash.append(r)
                continue
            # Extract info hash from magnet if possible
            import re
            m = re.search(r"xt=urn:btih:([a-z0-9]+)", r.magnet, re.I)
            key = m.group(1).lower() if m else r.magnet
            if key not in seen_hashes:
                seen_hashes.add(key)
                unique_by_hash.append(r)

        # Pass 2: Deduplicate by normalized title — keep the best-seeded result per title
        title_groups: dict[str, list[SearchResult]] = {}
        for r in unique_by_hash:
            normalized = r.title.lower().strip()
            title_groups.setdefault(normalized, []).append(r)

        unique = []
        for group in title_groups.values():
            best = max(group, key=lambda r: (r.seeders or 0))
            unique.append(best)

        return unique

    def _quality_filter(self, results: list[SearchResult],
                        quality_profile: QualityProfile | None = None) -> list[SearchResult]:
        """Remove results that fail quality checks or are obviously broken.

        Args:
            results: List of search results to filter.
            quality_profile: Optional per-query quality profile.

        Returns:
            Filtered list of SearchResult objects.
        """
        inferrer = SmartQualityInferrer()
        profile = quality_profile or self._quality_profile
        return [r for r in results if self._is_acceptable_result(r, inferrer, profile)]

    def _is_acceptable_result(self, result: SearchResult, inferrer: SmartQualityInferrer,
                              profile: QualityProfile) -> bool:
        """Check if a single search result is acceptable based on size/quality.

        Args:
            result: The SearchResult to validate.
            inferrer: Inferrer utility to evaluate result.
            profile: QualityProfile to use.

        Returns:
            True if acceptable, False otherwise.
        """
        if not result.title or not result.title.strip():
            logger.debug("Quality-filtered: empty title")
            return False
        if result.size_bytes is not None and result.size_bytes == 0:
            logger.debug(f"Quality-filtered: zero-byte file: {result.title}")
            return False
        accepted, reason = inferrer.should_accept_result(result, profile)
        if not accepted:
            logger.debug(f"Quality-filtered by SmartQualityInferrer: '{result.title[:60]}' — {reason}")
            return False
        return True


    async def _rank(self, results: list[SearchResult],
                     preferred_language: str | None = None,
                     quality_profile: QualityProfile | None = None) -> list[SearchResult]:
        """Sort results by quality score (best first), then by seeders.

        Quality scoring includes language matching (bonus for preferred
        language, penalty for wrong language) and release group reputation
        boosts when available.

        Args:
            results: List of search results to rank.
            preferred_language: Optional language preference.
            quality_profile: Optional quality profile to use for scoring.
        """
        profile = quality_profile or self._quality_profile
        
        for result in results:
            base_score = QualityAnalyzer.score_result(result.title, profile,
                                       preferred_language=preferred_language)

            # Apply release group reputation boost
            reputation_boost = 0.0
            if self._release_group_tracker:
                reputation_boost = await self._release_group_tracker.get_reputation_boost(
                    result.title
                )

            result.quality_score = max(0.0, base_score + reputation_boost)

        return sorted(
            results,
            key=lambda r: (
                r.quality_score,
                r.seeders or 0,
            ),
            reverse=True,
        )
