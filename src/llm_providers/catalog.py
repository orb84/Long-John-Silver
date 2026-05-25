"""
Model catalog for the LLM Providers library.

Fetches and caches available models from provider endpoints,
including pricing and context window information where available.
"""

import httpx
from loguru import logger
from typing import Optional
from datetime import datetime, timedelta
from src.llm_providers.models import ModelInfo, PricingInfo, ContextInfo, ProviderPreset, ProviderStatus
from src.llm_providers.context_limits import extract_context_limit, iter_model_records
from src.llm_providers.key_store import KeyStore


class ModelCatalog:
    """Fetches, parses, and caches model listings from provider endpoints."""

    def __init__(self, key_store: KeyStore, cache_ttl_minutes: int = 60):
        self._key_store = key_store
        self._cache: dict[str, list[ModelInfo]] = {}
        self._cache_timestamps: dict[str, datetime] = {}
        self._cache_ttl = timedelta(minutes=cache_ttl_minutes)
        self._statuses: dict[str, ProviderStatus] = {}

    async def list_models(self, provider_id: str, preset: ProviderPreset,
                          force_refresh: bool = False) -> list[ModelInfo]:
        """Fetch available models for a provider (cached with TTL)."""
        if not force_refresh and self._is_cache_valid(provider_id):
            return self._cache.get(provider_id, [])

        models = await self._fetch_models(provider_id, preset)
        self._cache[provider_id] = models
        self._cache_timestamps[provider_id] = datetime.now()
        return models

    async def check_health(self, provider_id: str, preset: ProviderPreset) -> ProviderStatus:
        """Check if a provider endpoint is reachable and report model count."""
        try:
            models = await self.list_models(provider_id, preset, force_refresh=False)
            if not models and provider_id not in self._cache:
                models = await self._fetch_models(provider_id, preset)
                self._cache[provider_id] = models

            self._statuses[provider_id] = ProviderStatus(
                provider_id=provider_id,
                reachable=True,
                model_count=len(models),
                last_checked=datetime.now(),
            )
        except Exception as e:
            self._statuses[provider_id] = ProviderStatus(
                provider_id=provider_id,
                reachable=False,
                model_count=0,
                last_checked=datetime.now(),
                error=str(e),
            )

        return self._statuses[provider_id]

    def get_status(self, provider_id: str) -> Optional[ProviderStatus]:
        """Return cached status for a provider, if any."""
        return self._statuses.get(provider_id)

    def cached_models(self, provider_id: str) -> list[ModelInfo]:
        """Return cached models for a provider without exposing cache internals."""
        return list(self._cache.get(provider_id, []))

    def invalidate_cache(self, provider_id: Optional[str] = None) -> None:
        """Clear cached models. If provider_id is None, clear all."""
        if provider_id:
            self._cache.pop(provider_id, None)
            self._cache_timestamps.pop(provider_id, None)
        else:
            self._cache.clear()
            self._cache_timestamps.clear()

    def _is_cache_valid(self, provider_id: str) -> bool:
        """Check if the cache for a provider is still within TTL."""
        ts = self._cache_timestamps.get(provider_id)
        if not ts:
            return False
        return datetime.now() - ts < self._cache_ttl

    async def _fetch_models(self, provider_id: str, preset: ProviderPreset) -> list[ModelInfo]:
        """Fetch models from the provider's API endpoint."""
        url = preset.models_endpoint
        if not url:
            return []

        api_key_entry = self._key_store.get_active_key(provider_id)
        headers = {"Content-Type": "application/json"}
        if api_key_entry:
            headers["Authorization"] = f"Bearer {api_key_entry.key}"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"[{provider_id}] API returned {e.response.status_code}")
            return []
        except Exception as e:
            logger.error(f"[{provider_id}] Failed to fetch models: {e}")
            return []

        return self._parse_models_response(provider_id, data)

    def _parse_models_response(self, provider_id: str, data: dict) -> list[ModelInfo]:
        """Parse a /v1/models response into ModelInfo objects.

        Handles the standard OpenAI-format response as well as
        provider-specific extensions (pricing, context).
        """
        raw_models = list(iter_model_records(data))
        models = []

        for m in raw_models:
            model_id = m.get("id", "")
            if not model_id:
                continue

            pricing = self._extract_pricing(m)
            context = self._extract_context(m)

            models.append(ModelInfo(
                id=model_id,
                name=m.get("name") or m.get("id", ""),
                provider_id=provider_id,
                pricing=pricing,
                context=context,
                owned_by=m.get("owned_by", ""),
                description=m.get("description", ""),
                available=m.get("available", True),
            ))

        models.sort(key=lambda m: m.name.lower())
        logger.info(f"[{provider_id}] Fetched {len(models)} models")
        return models

    @staticmethod
    def _extract_pricing(model_data: dict) -> PricingInfo:
        """Extract pricing from model metadata (OpenRouter format)."""
        pricing = model_data.get("pricing", {})
        if isinstance(pricing, dict):
            return PricingInfo(
                prompt_per_million=_safe_float(pricing.get("prompt")),
                completion_per_million=_safe_float(pricing.get("completion")),
                currency=pricing.get("currency", "USD"),
            )
        return PricingInfo()

    @staticmethod
    def _extract_context(model_data: dict) -> ContextInfo:
        """Extract context info from provider model metadata.

        Providers expose context-window fields with different names.  Prefer
        explicit context-window fields and use ``max_tokens`` only as a last
        provider-supplied fallback because some APIs use it for generation
        output rather than the full prompt window.
        """
        context_length = extract_context_limit(model_data)

        return ContextInfo(
            max_context_tokens=context_length,
            max_output_tokens=_first_int(model_data, "max_output_tokens", "output_token_limit"),
            supports_vision=model_data.get("supports_vision", False),
            supports_tools=model_data.get("supports_tool_calling", False),
            supports_streaming=model_data.get("supports_streaming", True),
        )


def _first_int(data: dict, *keys: str) -> Optional[int]:
    """Return the first integer-like value found for any key."""
    for key in keys:
        value = data.get(key) if isinstance(data, dict) else None
        if value is None:
            continue
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                continue
    return None


def _safe_float(value) -> Optional[float]:
    """Safely convert a value to float, returning None on failure."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None