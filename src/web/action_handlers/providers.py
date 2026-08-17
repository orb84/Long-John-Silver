"""
Provider action handlers for LJS.

Provides ProvidersActionHandler: the single place for LLM provider
management mutation logic invoked via ActionGateway from UI endpoints.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.llm_providers.settings_mutation import LLMSettingsMutationService

if TYPE_CHECKING:
    from src.ai.assistant import AIAssistant
    from src.core.config import SettingsManager
    from src.llm_providers.manager import LLMProviderManager


class ProvidersActionHandler:
    """Handlers for provider management actions routed through ActionGateway.

    Each method receives keyword arguments from ActionCommand.arguments
    and returns a dict wrapped into ActionResult.data.

    Dependencies (injected at composition root):
        llm_manager — LLMManager (provider key management, presets)
        settings_manager — SettingsManager (save active provider)
        assistant — AIAssistant (reload on provider activation)
    """

    def __init__(self, llm_manager: LLMProviderManager, settings_manager: SettingsManager, assistant: AIAssistant) -> None:
        self._llm = llm_manager
        self._llm_settings = LLMSettingsMutationService(settings_manager, assistant, llm_manager)

    async def add_key(self, provider_id: str, key: str, label: str = "default", set_active: bool = True) -> dict:
        """Add a new API key for a provider."""
        entry = self._llm.keys.add_key(
            provider_id, key, label=label, set_active=set_active,
        )
        return {"id": entry.id, "label": entry.label, "is_active": entry.is_active}

    async def remove_key(self, provider_id: str, key_id: str) -> dict:
        """Remove an API key from a provider."""
        self._llm.keys.remove_key(provider_id, key_id)
        return {"status": "removed"}

    async def activate_key(self, provider_id: str, key_id: str) -> dict:
        """Set a specific API key as active for a provider."""
        self._llm.keys.set_active_key(provider_id, key_id)
        return {"status": "activated"}

    async def activate(self, provider_id: str) -> dict:
        """Activate one provider through the canonical rollback-safe route mutation."""
        await self._llm_settings.update(provider=provider_id)
        return {"status": "activated", "provider_id": provider_id}
