"""
Provider action handlers for LJS.

Provides ProvidersActionHandler: the single place for LLM provider
management mutation logic invoked via ActionGateway from UI endpoints.
"""

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
        self._sm = settings_manager
        self._assistant = assistant

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
        """Activate a provider, persist to settings, and reload the assistant."""
        self._llm.registry.set_active_provider(provider_id)
        settings = self._sm.settings
        settings.llm.active_provider = provider_id
        preset = self._llm.registry.get_preset(provider_id)
        if preset:
            settings.llm.api_base = preset.api_base
            active_key = self._llm.keys.get_active_key(provider_id)
            if active_key:
                settings.llm.api_key = active_key.key
        self._sm.save(settings)
        self._assistant.update_settings(settings)
        return {"status": "activated", "provider_id": provider_id}
