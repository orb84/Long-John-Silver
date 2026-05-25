"""
LLM Providers — a portable LLM endpoint management library.

Manages multiple LLM provider endpoints with presets, API key storage,
model cataloging (with pricing + context info), and a unified client.

Usage:
    from src.llm_providers import LLMProviderManager

    mgr = LLMProviderManager()
    mgr.keys.add_key("openrouter", "sk-xxx", label="personal")
    mgr.registry.set_active_provider("openrouter")

    models = await mgr.catalog.list_models("openrouter", mgr.registry.get_preset("openrouter"))
    response = await mgr.client.completion(messages=[...], model="openrouter/openai/gpt-4o")
"""

from typing import Optional
from src.llm_providers.key_store import KeyStore
from src.llm_providers.registry import ProviderRegistry
from src.llm_providers.catalog import ModelCatalog
from src.llm_providers.client import LLMClient
from src.llm_providers.models import (
    ProviderType,
    ProviderPreset,
    ProviderConfig,
    ProviderStatus,
    ModelInfo,
    PricingInfo,
    ContextInfo,
    APIKeyEntry,
)
from src.llm_providers.presets import get_ordered_presets


class LLMProviderManager:
    """Facade that owns all sub-components of the LLM Providers library.

    This is the main entry point. It creates and wires together:
    - KeyStore: API key storage and retrieval
    - ProviderRegistry: preset + custom provider management
    - ModelCatalog: model fetching, caching, pricing, context info
    - LLMClient: unified completion interface
    """

    def __init__(self, key_store_path: str = "data/api_keys.json",
                 cache_ttl_minutes: int = 60):
        self.keys = KeyStore(store_path=key_store_path)
        self.registry = ProviderRegistry(key_store=self.keys)
        self.catalog = ModelCatalog(key_store=self.keys, cache_ttl_minutes=cache_ttl_minutes)
        self.client = LLMClient(registry=self.registry)

    def list_providers(self) -> list[ProviderPreset]:
        """List all available provider presets."""
        return self.registry.list_presets()

    def list_ready_providers(self) -> list[ProviderPreset]:
        """List only providers that have all required configuration."""
        return [p for p in self.list_providers() if self.registry.is_provider_ready(p.id)]

    async def get_models_for_provider(self, provider_id: str,
                                       force_refresh: bool = False) -> list[ModelInfo]:
        """Convenience method: get models for a provider."""
        preset = self.registry.get_preset(provider_id)
        if not preset:
            return []
        return await self.catalog.list_models(provider_id, preset, force_refresh=force_refresh)

    async def health_check_all(self) -> dict[str, ProviderStatus]:
        """Check health of all registered providers."""
        results = {}
        for preset in self.list_providers():
            results[preset.id] = await self.catalog.check_health(preset.id, preset)
        return results

    def get_full_provider_info(self, provider_id: str) -> Optional[dict]:
        """Get complete info about a provider: preset, keys, models, status.

        Returns a dict suitable for API responses or rendering.
        """
        preset = self.registry.get_preset(provider_id)
        if not preset:
            return None

        keys = self.keys.list_keys(provider_id)
        active_key = self.keys.get_active_key(provider_id)
        status = self.catalog.get_status(provider_id)
        cached_models = self.catalog.cached_models(provider_id)
        is_ready = self.registry.is_provider_ready(provider_id)

        return {
            "preset": preset.model_dump(),
            "api_base": self.registry.get_resolved_api_base(provider_id),
            "has_active_key": active_key is not None,
            "active_key_label": active_key.label if active_key else None,
            "key_count": len(keys),
            "is_ready": is_ready,
            "status": status.model_dump() if status else None,
            "cached_model_count": len(cached_models),
        }


__all__ = [
    "LLMProviderManager",
    "KeyStore",
    "ProviderRegistry",
    "ModelCatalog",
    "LLMClient",
    "ProviderType",
    "ProviderPreset",
    "ProviderConfig",
    "ProviderStatus",
    "ModelInfo",
    "PricingInfo",
    "ContextInfo",
    "APIKeyEntry",
    "get_ordered_presets",
]