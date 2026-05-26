"""
Setup action handlers for LJS.

Provides SetupActionHandler: the single place for setup wizard
mutation logic invoked via ActionGateway from UI endpoints.
"""

from pathlib import Path
from typing import Any

from loguru import logger

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
        """Configure download directory and category-owned library paths.

        The setup wizard uses the same ignored category config files as Compass.
        It may receive the older flat ``library_paths`` shape from the UI, but
        save-time inflation writes those paths to ``paths.library_path`` inside
        ``config/categories/<category_id>.yaml``.
        """
        settings = self._sm.settings
        if "download_dir" in kwargs and kwargs["download_dir"]:
            settings.download_dir = kwargs["download_dir"]
        if "library_root" in kwargs and kwargs["library_root"]:
            settings.library_root = kwargs["library_root"]
        self._merge_category_settings(kwargs.get("category_settings"))
        if "library_paths" in kwargs:
            path_payload = {
                str(cat_id): {"library_path": path}
                for cat_id, path in dict(kwargs["library_paths"]).items()
                if cat_id and path is not None
            }
            self._merge_category_settings(path_payload)
        self._prepare_library_directories(settings)
        self._sm.save(settings)
        return {"status": "ok", "library_root": settings.library_root}

    async def setup_category_config(self, **kwargs: Any) -> dict:
        """Save first-run category-local services and preferences.

        Initial setup must not write media API keys, library paths, or download
        preferences into global settings.  This action accepts the same
        ``category_settings`` payload shape as Compass and deep-merges it into
        ``Settings.category_settings`` so partial updates such as "only the TMDB
        key" do not erase inherited media defaults or other services.
        """
        category_payload = kwargs.get("category_settings")
        if not isinstance(category_payload, dict):
            return {"status": "ok", "updated": []}
        self._merge_category_settings(category_payload)
        self._sm.save(self._sm.settings)
        return {"status": "ok", "updated": sorted(str(key) for key in category_payload.keys())}


    def _prepare_library_directories(self, settings) -> None:
        """Best-effort creation of the global and category library roots.

        Setup is the right place to create user-facing folders: read-only code
        paths can still call ``get_root_path`` without side effects, while write
        flows start from directories that normally exist.  Failures are logged
        instead of aborting setup because users may point to temporarily offline
        mounts and fix permissions later.
        """
        roots: set[Path] = {Path(getattr(settings, "library_root", "./library") or "./library")}
        try:
            from src.core.categories.registry import CategoryRegistry

            registry = CategoryRegistry.with_defaults()
            for category in registry.list_all():
                if getattr(category, "category_id", ""):
                    roots.add(Path(category.get_root_path(settings)))
        except Exception as exc:
            logger.warning(f"Could not enumerate category roots during setup: {exc}")

        for root in sorted(roots, key=lambda item: str(item)):
            try:
                root.expanduser().mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                logger.warning(f"Could not create library directory {root}: {exc}")

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


    def _merge_category_settings(self, payload: object) -> None:
        """Deep-merge category settings into the current in-memory settings.

        Category payloads are partial by design: setup may save only a TMDB key,
        later save only a media language preference, and Compass may toggle one
        provider.  A shallow assignment would silently drop sibling services or
        inherited defaults until the next reload, so all category writes go
        through this small merge helper before ``SettingsManager.save`` filters
        and persists the private YAML.
        """
        if not isinstance(payload, dict):
            return
        settings = self._sm.settings
        for category_id, values in payload.items():
            key = str(category_id or "").strip()
            if not key or not isinstance(values, dict):
                continue
            existing = settings.category_settings.get(key)
            if not isinstance(existing, dict):
                existing = {}
            settings.category_settings[key] = self._deep_merge(existing, values)

    @classmethod
    def _deep_merge(cls, base: dict, override: dict) -> dict:
        """Recursively merge setup category config without losing siblings."""
        merged = dict(base or {})
        for key, value in (override or {}).items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = cls._deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged

    async def setup_complete(self) -> dict:
        """Mark the setup wizard as complete."""
        settings = self._sm.settings
        settings.setup_complete = True
        self._sm.save(settings)
        return {"status": "ok", "setup_complete": True, "redirect": "/"}
