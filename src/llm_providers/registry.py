"""
Provider registry for the LLM Providers library.

Combines built-in presets with user-defined custom providers
and manages the registry of all available providers.
"""

from loguru import logger
from typing import Optional
from src.llm_providers.models import ProviderPreset, ProviderType, ProviderConfig
from src.llm_providers.presets import get_builtin_presets
from src.llm_providers.key_store import KeyStore


class ProviderRegistry:
    """Registry of all available LLM providers and their configurations."""

    def __init__(self, key_store: KeyStore):
        self._key_store = key_store
        self._presets: dict[str, ProviderPreset] = get_builtin_presets()
        self._active_provider_id: Optional[str] = None
        self._api_base_overrides: dict[str, str] = {}

    def register_custom(self, preset: ProviderPreset) -> None:
        """Register a custom provider preset."""
        preset.provider_type = ProviderType.CUSTOM
        self._presets[preset.id] = preset
        logger.info(f"Registered custom provider: {preset.id}")

    def remove_provider(self, provider_id: str) -> None:
        """Remove a provider (only custom providers can be removed)."""
        preset = self._presets.get(provider_id)
        if preset and preset.provider_type == ProviderType.CUSTOM:
            del self._presets[provider_id]
            logger.info(f"Removed custom provider: {provider_id}")

    def get_preset(self, provider_id: str) -> Optional[ProviderPreset]:
        """Look up a provider preset by ID."""
        return self._presets.get(provider_id)

    def list_presets(self) -> list[ProviderPreset]:
        """Return all provider presets ordered for display."""
        from src.llm_providers.presets import get_ordered_presets
        builtins = get_ordered_presets()
        custom = [p for p in self._presets.values() if p.provider_type == ProviderType.CUSTOM]
        return builtins + custom

    def set_active_provider(self, provider_id: str) -> None:
        """Set the currently active provider."""
        if provider_id not in self._presets:
            logger.error(f"Unknown provider: {provider_id}")
            return
        self._active_provider_id = provider_id
        logger.info(f"Active provider set to: {provider_id}")

    def get_active_provider_id(self) -> Optional[str]:
        """Return the currently active provider ID."""
        return self._active_provider_id

    def set_api_base_override(self, provider_id: str, api_base: str) -> None:
        """Override the API base URL for a provider."""
        self._api_base_overrides[provider_id] = api_base

    def get_resolved_api_base(self, provider_id: str) -> str:
        """Get the API base URL for a provider, applying any override."""
        if provider_id in self._api_base_overrides:
            return self._api_base_overrides[provider_id]
        preset = self._presets.get(provider_id)
        return preset.api_base if preset else ""

    def get_resolved_api_key(self, provider_id: str) -> Optional[str]:
        """Get the active API key for a provider from the key store."""
        entry = self._key_store.get_active_key(provider_id)
        return entry.key if entry else None

    def get_config(self, provider_id: Optional[str] = None) -> Optional[ProviderConfig]:
        """Get the full resolved configuration for a provider."""
        pid = provider_id or self._active_provider_id
        if not pid:
            return None
        preset = self._presets.get(pid)
        if not preset:
            return None
        active_key = self._key_store.get_active_key(pid)
        return ProviderConfig(
            provider_id=pid,
            preset=preset,
            active_key_id=active_key.id if active_key else None,
            api_base_override=self._api_base_overrides.get(pid),
        )

    def is_provider_ready(self, provider_id: str) -> bool:
        """Check if a provider has all required configuration (key if needed)."""
        preset = self._presets.get(provider_id)
        if not preset:
            return False
        if preset.requires_api_key and not self._key_store.has_keys(provider_id):
            return False
        return True