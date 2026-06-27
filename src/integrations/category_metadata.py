"""Cache-aware metadata resolver for definition-backed categories.

The resolver orchestrates provider execution, persistent cache reuse, provider
backoff, and cross-provider disambiguation. Provider-specific HTTP parsing lives
in ``src.integrations.metadata_providers`` so this boundary remains category- and
transport-focused instead of becoming another large adapter grab bag.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger
import httpx

from src.integrations.metadata_cache import MetadataCacheStore, ProviderRateLimiter, stable_cache_key
from src.integrations.metadata_disambiguation import PROVIDER_PRIORITY, rank_and_group
from src.integrations.metadata_providers import MetadataProviderRegistry, ProviderInvocation, ProviderResult, make_stable_id
from src.integrations.metadata_providers.base import ProviderAdapterContext, compact


USER_AGENT = "LJS/0.1 (category-metadata; https://github.com/local/library-jolly-sailor)"


class CategoryMetadataResolver:
    """Resolve metadata for definition-backed Music/Ebook/Audiobook categories."""

    def __init__(self, category: Any, settings: Any, http_client: httpx.AsyncClient | None = None, db: Any | None = None) -> None:
        self.category = category
        self.settings = settings
        self._client = http_client
        self._db = db
        self._cache = MetadataCacheStore(db)
        self._provider_registry = MetadataProviderRegistry()

    async def resolve(self, query: str, *, limit: int = 5) -> dict[str, Any]:
        """Query enabled category services and return normalized candidates.

        The resolver returns stable IDs and disambiguation hints but does not make
        irreversible selection decisions. The LLM should use evidence, conflict
        reports, and user constraints to select/prune candidates when deterministic
        ranking is not decisive.
        """
        query = compact(query)
        if not query:
            return {"ok": False, "error": "query is required", "results": [], "services_tried": [], "services_skipped": []}
        limit = max(1, min(int(limit or 5), 10))
        close_client = False
        client = self._client
        if client is None:
            close_client = True
            client = httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers={"User-Agent": USER_AGENT})
        services_tried: list[str] = []
        services_skipped: list[dict[str, str]] = []
        cache_hits: list[dict[str, str]] = []
        results: list[ProviderResult] = []
        try:
            profile = self._provider_profile()
            if not profile:
                services_skipped.append({"provider": "category_metadata", "reason": f"no resolver profile for category {self.category.category_id}"})
            for spec in profile:
                await self._try(spec, client, query, limit, results, services_tried, services_skipped, cache_hits)
        finally:
            if close_client:
                await client.aclose()
        ranked, groups, disambiguation = self._rank_and_group(query, results, limit=limit)
        return {
            "ok": True,
            "category_id": self.category.category_id,
            "query": query,
            "services_tried": services_tried,
            "services_skipped": services_skipped,
            "cache_hits": cache_hits,
            "results": [item.as_dict() for item in ranked],
            "groups": groups,
            "best": ranked[0].as_dict() if ranked else None,
            "disambiguation": disambiguation,
            "llm_selection_instruction": (
                "Use deterministic score as evidence, not as an absolute truth. When user constraints mention "
                "edition, narrator, release type, language, format, track count, or year, prefer the candidate whose "
                "object_model satisfies those constraints. If top candidates conflict, ask a concise clarification or "
                "state the assumption before proceeding."
            ),
        }

    def _enabled(self, provider: str, *, default: bool = True) -> bool:
        return bool(self.category.category_service_enabled(self.settings, provider, default=default))

    def _secret(self, provider: str, key: str) -> str | None:
        return self.category.category_service_secret(self.settings, provider, key)

    def _provider_profile(self) -> tuple[ProviderInvocation, ...]:
        """Return the declarative provider profile for this category."""
        return self._provider_registry.profile_for_category(self.category)

    def _provider_adapter_context(self, client: httpx.AsyncClient) -> ProviderAdapterContext:
        """Build the adapter context passed to provider-specific modules."""
        return ProviderAdapterContext(
            category=self.category,
            settings=self.settings,
            client=client,
            get_json=self._get_json,
        )

    async def _try(
        self,
        spec: ProviderInvocation,
        client: httpx.AsyncClient,
        query: str,
        limit: int,
        results: list[ProviderResult],
        services_tried: list[str],
        services_skipped: list[dict[str, str]],
        cache_hits: list[dict[str, str]],
    ) -> None:
        """Run one provider if enabled and record skip/failure/cache details."""
        if not self._enabled(spec.provider, default=spec.enabled_default and not spec.optional):
            services_skipped.append({"provider": spec.provider, "reason": "disabled in category config"})
            return
        if spec.skip_when_enabled_reason:
            services_skipped.append({"provider": spec.provider, "reason": spec.skip_when_enabled_reason})
            return
        if spec.keyed and not self._secret(spec.provider, spec.key_name):
            services_skipped.append({"provider": spec.provider, "reason": f"requires {spec.key_name}"})
            return
        method = self._provider_registry.method_for_invocation(spec, self._provider_adapter_context(client))
        if not callable(method):
            services_skipped.append({"provider": spec.provider, "reason": "provider adapter is not implemented"})
            return

        cache_key = stable_cache_key(self.category.category_id, spec.provider, query, limit, spec.kwargs or {})
        cached = await self._cache.get(category_id=self.category.category_id, provider=spec.provider, cache_key=cache_key)
        if cached:
            payload_results = cached.payload.get("results") if isinstance(cached.payload, dict) else []
            rehydrated = [ProviderResult.from_dict(row) for row in payload_results or []]
            results.extend(rehydrated)
            cache_hits.append({"provider": spec.provider, "fetched_at": cached.fetched_at, "expires_at": cached.expires_at, "status": cached.status})
            return

        services_tried.append(spec.provider)
        try:
            async with ProviderRateLimiter(self._db, spec.provider, minimum_interval_seconds=spec.min_interval_seconds):
                provider_results = await method(query, limit, **(spec.kwargs or {}))
            for item in provider_results:
                item.score = max(float(item.score or 0.0), PROVIDER_PRIORITY.get(item.provider, 0.5) * 0.2)
            results.extend(provider_results)
            await self._cache.put(
                category_id=self.category.category_id,
                provider=spec.provider,
                cache_key=cache_key,
                query=query,
                payload={"results": [item.as_dict() for item in provider_results]},
                ttl_seconds=spec.ttl_seconds,
                stable_id=provider_results[0].stable_id if provider_results else "",
                status="ok" if provider_results else "empty",
                provider_signature=json.dumps(spec.kwargs or {}, sort_keys=True),
            )
        except Exception as exc:  # provider failures must not break the agent turn
            logger.warning(f"{self.category.category_id} metadata provider {spec.provider} failed: {exc}")
            stale = await self._cache.get_latest_for_query(
                category_id=self.category.category_id,
                provider=spec.provider,
                query=query,
                allow_stale=True,
            )
            if stale:
                payload_results = stale.payload.get("results") if isinstance(stale.payload, dict) else []
                rehydrated = [ProviderResult.from_dict(row) for row in payload_results or []]
                for row in rehydrated:
                    row.evidence = sorted(set((row.evidence or []) + ["stale metadata reused after provider failure"]))
                    row.score *= 0.92
                results.extend(rehydrated)
                cache_hits.append({
                    "provider": spec.provider,
                    "fetched_at": stale.fetched_at,
                    "expires_at": stale.expires_at,
                    "status": "stale_on_error",
                })
                services_skipped.append({"provider": spec.provider, "reason": f"live request failed; reused stale cache: {exc}"})
                return
            await self._cache.put(
                category_id=self.category.category_id,
                provider=spec.provider,
                cache_key=cache_key,
                query=query,
                payload={"results": [], "error": str(exc)},
                ttl_seconds=min(900, max(60, spec.ttl_seconds // 96)),
                stable_id="",
                status="error",
                provider_signature=json.dumps(spec.kwargs or {}, sort_keys=True),
            )
            services_skipped.append({"provider": spec.provider, "reason": f"request failed: {exc}"})

    async def _get_json(self, client: httpx.AsyncClient, provider: str, url: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET JSON while recording provider rate-limit state."""
        response = await client.get(url, params=params or {})
        await ProviderRateLimiter(self._db, provider).record_response(status_code=response.status_code, headers=dict(response.headers))
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}

    def _rank_and_group(self, query: str, results: list[ProviderResult], *, limit: int) -> tuple[list[ProviderResult], list[dict[str, Any]], dict[str, Any]]:
        """Delegate deterministic grouping/scoring to the metadata disambiguation boundary."""
        ranked = rank_and_group(query, results, limit=limit)
        return list(ranked.ranked), ranked.groups, ranked.disambiguation
