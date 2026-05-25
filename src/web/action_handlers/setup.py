"""
Setup action handlers for LJS.

Provides SetupActionHandler: the single place for setup wizard
mutation logic invoked via ActionGateway from UI endpoints.
"""

from typing import Any

from src.ai.assistant import AIAssistant
from src.core.config import SettingsManager
from src.core.autostart import AutoStartManager
from src.core.models import EmbeddingSettings, WebSearchConfig, SharingSettings
from src.llm_providers.manager import LLMProviderManager
from src.utils.auth import AuthService


class SetupActionHandler:
    """Handlers for setup wizard actions routed through ActionGateway.

    Each method receives keyword arguments from ActionCommand.arguments
    and returns a dict wrapped into ActionResult.data.

    Dependencies (injected at composition root):
        settings_manager — SettingsManager (settings CRUD + save)
        auth_service — AuthService (password hashing)
        llm_manager — LLMManager (provider preset lookup)
        assistant — AIAssistant (reload on LLM changes)
    """

    def __init__(self, settings_manager: SettingsManager, auth_service: AuthService, llm_manager: LLMProviderManager, assistant: AIAssistant) -> None:
        self._sm = settings_manager
        self._auth = auth_service
        self._llm = llm_manager
        self._assistant = assistant

    async def setup_password(self, password: str = "", confirm: str = "") -> dict:
        """Set or clear the web password hash."""
        settings = self._sm.settings
        if password:
            settings.web_password_hash = self._auth.hash_password(password)
        else:
            settings.web_password_hash = None
        self._sm.save(settings)
        return {"status": "ok"}

    async def setup_paths(self, **kwargs: Any) -> dict:
        """Configure download directory and library paths."""
        settings = self._sm.settings
        if "download_dir" in kwargs and kwargs["download_dir"]:
            settings.download_dir = kwargs["download_dir"]
        if "category_settings" in kwargs:
            for cat_id, cat_props in dict(kwargs["category_settings"]).items():
                if cat_id not in settings.category_settings:
                    settings.category_settings[cat_id] = {}
                for prop_name, prop_val in cat_props.items():
                    settings.category_settings[cat_id][prop_name] = prop_val
        if "library_paths" in kwargs:
            for cat_id, path in dict(kwargs["library_paths"]).items():
                if cat_id not in settings.category_settings:
                    settings.category_settings[cat_id] = {}
                settings.category_settings[cat_id]["library_path"] = path
        self._sm.save(settings)
        return {"status": "ok"}

    async def setup_llm(self, **kwargs: Any) -> dict:
        """Configure LLM provider, model, API base, API key, and web search."""
        settings = self._sm.settings
        if kwargs.get("provider"):
            settings.llm.active_provider = kwargs["provider"]
            preset = self._llm.registry.get_preset(kwargs["provider"])
            if preset:
                settings.llm.api_base = preset.api_base
        if kwargs.get("model"):
            settings.llm.model = kwargs["model"]
        if kwargs.get("api_base"):
            settings.llm.api_base = kwargs["api_base"]
        if kwargs.get("api_key"):
            settings.llm.api_key = kwargs["api_key"]
            provider = settings.llm.active_provider
            self._llm.keys.add_key(provider, kwargs["api_key"], label="setup", set_active=True)
        web_payload = kwargs.get("web_search")
        if isinstance(web_payload, dict):
            settings.web_search = WebSearchConfig(**{**settings.web_search.model_dump(), **web_payload})
        self._sm.save(settings)
        self._assistant.update_settings(settings)
        return {"status": "ok"}


    async def setup_embeddings(self, **kwargs: Any) -> dict:
        """Configure semantic-memory embedding runtime during first-run setup."""
        settings = self._sm.settings
        existing = settings.embeddings.model_dump() if hasattr(settings.embeddings, "model_dump") else {}
        allowed = {"enabled", "provider", "builtin_model", "dimension", "cache_dir", "auto_download", "warmup_on_startup", "max_model_size_mb"}
        existing.update({k: v for k, v in kwargs.items() if k in allowed and v is not None})
        settings.embeddings = EmbeddingSettings(**existing)
        self._sm.save(settings)
        return {"status": "ok", "embeddings": settings.embeddings.model_dump()}

    async def setup_channels(self, **kwargs: Any) -> dict:
        """Configure Discord, Telegram, and WhatsApp integration tokens."""
        settings = self._sm.settings
        if "discord_token" in kwargs:
            settings.discord_token = kwargs["discord_token"] or None
        if "discord_channel_id" in kwargs:
            val = kwargs["discord_channel_id"]
            settings.discord_channel_id = int(val) if val else None
        if "telegram_token" in kwargs:
            settings.telegram_token = kwargs["telegram_token"] or None
        if "whatsapp_token" in kwargs:
            settings.whatsapp_token = kwargs["whatsapp_token"] or None
        if "whatsapp_phone_number_id" in kwargs:
            settings.whatsapp_phone_number_id = kwargs["whatsapp_phone_number_id"] or None
        if "whatsapp_verify_token" in kwargs:
            settings.whatsapp_verify_token = kwargs["whatsapp_verify_token"] or None
        self._sm.save(settings)
        return {"status": "ok"}

    async def setup_sharing(self, **kwargs: Any) -> dict:
        """Configure first-run library sharing preferences."""
        settings = self._sm.settings
        existing = settings.sharing.model_dump() if hasattr(settings.sharing, "model_dump") else {}
        existing.update({k: v for k, v in kwargs.items() if v is not None})
        settings.sharing = SharingSettings(**existing)
        self._sm.save(settings)
        return {"status": "ok", "sharing": settings.sharing.model_dump()}

    async def setup_startup(self, enabled: bool = False) -> dict:
        """Configure launch-at-login during first-run setup.

        This mirrors the Compass checkbox: LJS saves the user's preference only
        after attempting the platform-specific OS registration, avoiding a state
        where setup claims auto-start is enabled but no launch entry exists.
        """
        manager = AutoStartManager()
        result = manager.set_enabled(bool(enabled))
        settings = self._sm.settings
        settings.auto_start_at_login = bool(result.get("enabled"))
        self._sm.save(settings)
        return {"status": "ok" if result.get("ok") else "warning", "auto_start_at_login": settings.auto_start_at_login, "autostart": result}

    async def setup_language(self, language: str = "English") -> dict:
        """Set the application language."""
        settings = self._sm.settings
        settings.language = language
        self._sm.save(settings)
        return {"status": "ok", "language": language}

    async def setup_complete(self) -> dict:
        """Mark the setup wizard as complete."""
        settings = self._sm.settings
        settings.setup_complete = True
        self._sm.save(settings)
        return {"status": "ok", "setup_complete": True, "redirect": "/"}
