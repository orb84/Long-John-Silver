"""
Settings action handlers for LJS.

Provides SettingsActionHandler: the single place for settings mutation
logic invoked via ActionGateway from UI endpoints.
"""

from pathlib import Path
from typing import Any

from src.ai.assistant import AIAssistant
from src.core.config import SettingsManager
from src.core.autostart import AutoStartManager
from src.core.downloader import DownloadManager
from src.core.models import WebSearchConfig, SizeLimitMode, BandwidthSchedule, SharingSettings, EmbeddingSettings, SoulseekSettings
from src.llm_providers.manager import LLMProviderManager
from src.llm_providers.context_limits import MIN_USER_CONTEXT_LIMIT
from src.utils.auth import AuthService
from src.integrations.slskd_manager import SlskdManager


class SettingsActionHandler:
    """Handlers for settings update actions routed through ActionGateway.

    Each method receives keyword arguments from ActionCommand.arguments
    and returns a dict wrapped into ActionResult.data.

    Dependencies (injected at composition root):
        settings_manager — SettingsManager (settings CRUD + save)
        assistant — AIAssistant (reload on LLM/tier changes)
        downloader — DownloadManager (apply speed limits)
        auth_service — AuthService (password hashing)
        llm_manager — LLMManager (provider preset lookup)
    """

    def __init__(self, settings_manager: SettingsManager, assistant: AIAssistant, downloader: DownloadManager, auth_service: AuthService, llm_manager: LLMProviderManager, slskd_manager: SlskdManager | None = None) -> None:
        self._sm = settings_manager
        self._assistant = assistant
        self._downloader = downloader
        self._auth = auth_service
        self._llm = llm_manager
        self._slskd = slskd_manager

    async def update_llm(self, **kwargs: Any) -> dict:
        """Update LLM configuration (model, api_base, provider, api_key)."""
        settings = self._sm.settings
        if kwargs.get("model"):
            settings.llm.model = kwargs["model"]
        if "api_base" in kwargs:
            settings.llm.api_base = kwargs["api_base"] or None
        if "api_key" in kwargs:
            settings.llm.api_key = kwargs["api_key"] or None
        if kwargs.get("provider"):
            settings.llm.active_provider = kwargs["provider"]
            if not settings.llm.api_base:
                preset = self._llm.registry.get_preset(kwargs["provider"])
                if preset:
                    settings.llm.api_base = preset.api_base
            if "api_key" not in kwargs or not kwargs["api_key"]:
                active_key = self._llm.keys.get_active_key(kwargs["provider"])
                if active_key:
                    settings.llm.api_key = active_key.key

        if "max_context_tokens" in kwargs:
            value = kwargs["max_context_tokens"]
            settings.llm.max_context_tokens = None if value is None else max(MIN_USER_CONTEXT_LIMIT, int(value))
        if "context_budget_percent" in kwargs and kwargs["context_budget_percent"] is not None:
            settings.llm.context_budget_percent = max(0, min(100, int(kwargs["context_budget_percent"])))
        if "reserved_output_tokens" in kwargs:
            value = kwargs["reserved_output_tokens"]
            settings.llm.reserved_output_tokens = None if value is None else max(0, int(value))
        if "raw_recent_context_percent" in kwargs and kwargs["raw_recent_context_percent"] is not None:
            settings.llm.raw_recent_context_percent = max(0, min(100, int(kwargs["raw_recent_context_percent"])))
        if "max_recent_conversation_turns" in kwargs and kwargs["max_recent_conversation_turns"] is not None:
            settings.llm.max_recent_conversation_turns = max(0, int(kwargs["max_recent_conversation_turns"]))
        if "auto_compress_context" in kwargs:
            settings.llm.auto_compress_context = bool(kwargs["auto_compress_context"])
        if "conversation_summary_max_tokens" in kwargs and kwargs["conversation_summary_max_tokens"] is not None:
            settings.llm.conversation_summary_max_tokens = max(0, int(kwargs["conversation_summary_max_tokens"]))
        self._sm.save(settings)
        self._assistant.update_settings(settings)
        return {"status": "ok"}

    async def update_quality(self, **kwargs: Any) -> dict:
        """Update default quality settings and apply speed limits."""
        settings = self._sm.settings
        q = settings.default_quality
        if "size_limit_mode" in kwargs:
            q.size_limit_mode = SizeLimitMode(kwargs["size_limit_mode"])
        if "max_bitrate_kbps" in kwargs:
            q.max_bitrate_kbps = kwargs["max_bitrate_kbps"] or None
        if "max_file_size_mb" in kwargs:
            q.max_file_size_mb = kwargs["max_file_size_mb"] or None
        if "preferred_resolution" in kwargs:
            q.preferred_resolution = kwargs["preferred_resolution"]
        if "max_download_speed_kbps" in kwargs:
            q.max_download_speed_kbps = kwargs["max_download_speed_kbps"] or None
        if "max_upload_speed_kbps" in kwargs:
            q.max_upload_speed_kbps = kwargs["max_upload_speed_kbps"] or None
        if "language" in kwargs:
            settings.language = kwargs["language"]
        self._sm.save(settings)
        await self._downloader.apply_speed_limits(q)
        return {"status": "ok", "quality": q.model_dump()}

    async def update_tokens(self, **kwargs: Any) -> dict:
        """Update bridge tokens (discord, telegram)."""
        settings = self._sm.settings
        if kwargs.get("discord_token"):
            settings.discord_token = kwargs["discord_token"]
        if kwargs.get("telegram_token"):
            settings.telegram_token = kwargs["telegram_token"]
        self._sm.save(settings)
        return {"status": "ok"}

    async def update_auto_download(self, **kwargs: Any) -> dict:
        """Update auto-download and auto-discover flags."""
        settings = self._sm.settings
        if "auto_download" in kwargs:
            settings.auto_download = bool(kwargs["auto_download"])
        if "auto_discover" in kwargs:
            settings.auto_discover = bool(kwargs["auto_discover"])
        self._sm.save(settings)
        return {
            "status": "ok",
            "auto_download": settings.auto_download,
            "auto_discover": settings.auto_discover,
        }

    async def update_tiers(self, **kwargs: Any) -> dict:
        """Update LLM tier configurations (lightweight, standard, heavy)."""
        settings = self._sm.settings
        for tier_key in ("lightweight", "standard", "heavy"):
            if tier_key in kwargs:
                tier_data = kwargs[tier_key]
                existing = getattr(settings.llm, tier_key)
                if isinstance(tier_data, dict):
                    for field in ("model", "api_base", "api_key", "max_tokens", "temperature", "provider"):
                        if field in tier_data and tier_data[field]:
                            setattr(existing, field, tier_data[field])
                        elif field in tier_data and tier_data[field] is None:
                            setattr(existing, field, None)
        self._sm.save(settings)
        self._assistant.update_settings(settings)
        return {"status": "ok"}

    async def update_persona(self, **kwargs: Any) -> dict:
        """Switch the active assistant persona package.

        Persona ids resolve through ``PersonaRegistry`` before they are saved,
        which means invalid ids fall back cleanly and the assistant/UI always
        agree on the package that actually exists on disk.
        """
        from src.ai.persona_registry import PersonaRegistry

        requested = str(kwargs.get("active_persona") or kwargs.get("persona_id") or "default").strip() or "default"
        registry = PersonaRegistry()
        package = registry.load(requested)
        if package.id != requested and requested != "default":
            raise ValueError(f"Persona package not found: {requested}")
        settings = self._sm.settings
        settings.active_persona = package.id
        self._sm.save(settings)
        self._assistant.update_settings(settings)
        return {"status": "ok", "active_persona": package.id, "active": package.api_summary(active=True)}


    async def update_embeddings(self, **kwargs: Any) -> dict:
        """Update local semantic-memory embedding settings."""
        settings = self._sm.settings
        existing = settings.embeddings.model_dump() if hasattr(settings.embeddings, "model_dump") else {}
        allowed = {
            "enabled", "provider", "builtin_model", "dimension", "cache_dir",
            "auto_download", "warmup_on_startup", "max_model_size_mb",
        }
        existing.update({k: v for k, v in kwargs.items() if k in allowed and v is not None})
        settings.embeddings = EmbeddingSettings(**existing)
        self._sm.save(settings)
        # LLM chat config does not need reload; vector store picks settings on next process start.
        return {"status": "ok", "embeddings": settings.embeddings.model_dump()}

    async def update_settings_library(self, **kwargs: Any) -> dict:
        """Update library paths, naming templates, and download directory."""
        settings = self._sm.settings
        if "download_dir" in kwargs:
            settings.download_dir = kwargs["download_dir"]
        if "library_root" in kwargs:
            settings.library_root = kwargs["library_root"] or "./library"
        new_max_concurrent = None
        if "max_concurrent" in kwargs:
            new_max_concurrent = max(1, int(kwargs["max_concurrent"]))
            settings.max_concurrent_downloads = new_max_concurrent
        if "category_settings" in kwargs:
            self._merge_category_settings(settings, kwargs.get("category_settings"))
        if "library_paths" in kwargs:
            path_payload = {
                str(cat_id): {"library_path": path}
                for cat_id, path in dict(kwargs["library_paths"]).items()
                if cat_id and path is not None
            }
            self._merge_category_settings(settings, path_payload)
        if "stall_check_interval_minutes" in kwargs:
            settings.stall_check_interval_minutes = int(kwargs["stall_check_interval_minutes"])
        if "stall_alternative_hours" in kwargs:
            settings.stall_alternative_hours = float(kwargs["stall_alternative_hours"])
        if "stall_cancel_hours" in kwargs:
            settings.stall_cancel_hours = float(kwargs["stall_cancel_hours"])
        for field in (
            "stall_health_window_minutes",
            "stall_test_interval_minutes",
            "stall_test_duration_minutes",
            "stall_alternative_cooldown_minutes",
            "stall_min_progress_bytes",
            "stall_idle_rate_bps",
        ):
            if field in kwargs:
                current = getattr(settings, field)
                setattr(settings, field, int(kwargs[field]) if isinstance(current, int) else float(kwargs[field]))
        self._prepare_library_directories(settings)
        self._sm.save(settings)
        if new_max_concurrent is not None:
            await self._downloader.set_max_concurrent(new_max_concurrent)
        return {"status": "ok", "max_concurrent_downloads": settings.max_concurrent_downloads, "library_root": settings.library_root}


    def _prepare_library_directories(self, settings) -> None:
        """Best-effort creation of global/category library roots after path edits."""
        from loguru import logger

        roots: set[Path] = {Path(getattr(settings, "library_root", "./library") or "./library")}
        try:
            from src.core.categories.registry import CategoryRegistry

            registry = CategoryRegistry.with_defaults()
            for category in registry.list_all():
                if getattr(category, "category_id", ""):
                    roots.add(Path(category.get_root_path(settings)))
        except Exception as exc:
            logger.warning(f"Could not enumerate category roots while saving settings: {exc}")
        for root in sorted(roots, key=lambda item: str(item)):
            try:
                root.expanduser().mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                logger.warning(f"Could not create library directory {root}: {exc}")

    async def update_sharing(self, **kwargs: Any) -> dict:
        """Update library seed-in-place sharing settings and apply quotas."""
        settings = self._sm.settings
        payload = dict(kwargs)
        existing = settings.sharing.model_dump() if hasattr(settings.sharing, "model_dump") else {}
        existing.update({k: v for k, v in payload.items() if v is not None})
        settings.sharing = SharingSettings(**existing)
        self._sm.save(settings)
        if hasattr(self._downloader, "apply_sharing_settings"):
            await self._downloader.apply_sharing_settings()
        return {"status": "ok", "sharing": settings.sharing.model_dump()}

    async def update_startup(self, enabled: bool = False) -> dict:
        """Toggle the user-level OS auto-start entry for LJS.

        The checkbox is intentionally simple, but the handler records both the
        user preference and the result returned by AutoStartManager.  If the OS
        write fails, settings are aligned to the actual detected state so the UI
        does not lie about boot behavior.
        """
        manager = AutoStartManager()
        result = manager.set_enabled(bool(enabled))
        settings = self._sm.settings
        settings.auto_start_at_login = bool(result.get("enabled"))
        self._sm.save(settings)
        return {"status": "ok" if result.get("ok") else "warning", "auto_start_at_login": settings.auto_start_at_login, "autostart": result}


    @classmethod
    def _merge_category_settings(cls, settings: Any, payload: object) -> None:
        """Deep-merge partial category-local settings into ``settings``.

        Compass controls usually save one field at a time.  Deep merging keeps
        sibling service credentials, paths, scheduler toggles, and inherited
        media preferences alive in memory until ``SettingsManager.save`` writes
        the ignored local category files.
        """
        if not isinstance(payload, dict):
            return
        for category_id, values in payload.items():
            key = str(category_id or "").strip()
            if not key or not isinstance(values, dict):
                continue
            existing = settings.category_settings.get(key)
            if not isinstance(existing, dict):
                existing = {}
            settings.category_settings[key] = cls._deep_merge(existing, values)

    @classmethod
    def _deep_merge(cls, base: dict, override: dict) -> dict:
        """Recursively merge mappings while replacing scalars/lists."""
        merged = dict(base or {})
        for key, value in (override or {}).items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = cls._deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged

    async def update_bandwidth(self, **kwargs: Any) -> dict:
        """Update bandwidth scheduling rules and apply the active profile."""
        settings = self._sm.settings
        if "bandwidth_schedules" in kwargs:
            settings.bandwidth_schedules = [BandwidthSchedule(**s) for s in kwargs["bandwidth_schedules"]]
        self._sm.save(settings)
        await self._downloader.refresh_bandwidth_limits()
        return {"status": "ok"}

    async def update_search(self, **kwargs: Any) -> dict:
        """Update torrent and general web search provider settings."""
        settings = self._sm.settings
        if "jackett_url" in kwargs:
            settings.jackett_url = kwargs["jackett_url"] or None
        if "jackett_api_key" in kwargs:
            settings.jackett_api_key = kwargs["jackett_api_key"] or None
        if "direct_scraper_fallback" in kwargs:
            settings.direct_scraper_fallback = bool(kwargs["direct_scraper_fallback"])
        web_payload = kwargs.get("web_search")
        if isinstance(web_payload, dict):
            settings.web_search = WebSearchConfig(**{**settings.web_search.model_dump(), **web_payload})
        soulseek_payload = kwargs.get("soulseek")
        soulseek_result: dict[str, Any] | None = None
        if isinstance(soulseek_payload, dict):
            settings.soulseek = SoulseekSettings(**{**settings.soulseek.model_dump(mode="json"), **soulseek_payload})
            if self._slskd and settings.soulseek.managed:
                if settings.soulseek.enabled:
                    if settings.soulseek.soulseek_username and settings.soulseek.soulseek_password:
                        try:
                            ok = await self._slskd.start(settings, login_timeout_seconds=5.0)
                        except Exception as exc:
                            ok = False
                            settings.soulseek.account_status = "error"
                            settings.soulseek.account_status_message = f"Soulseek start failed while saving settings: {exc}"
                        self._slskd.save_to_settings(settings)
                        soulseek_result = {
                            "status": settings.soulseek.account_status if ok else (settings.soulseek.account_status or "error"),
                            "running": bool(ok),
                            "ready": bool(ok and settings.soulseek.account_ready),
                            "account_status": settings.soulseek.account_status,
                            "account_status_message": settings.soulseek.account_status_message,
                            "error": None if (ok or settings.soulseek.account_status == "checking") else (settings.soulseek.account_status_message or self._slskd.last_error),
                        }
                    else:
                        settings.soulseek.account_status = "needs_credentials"
                        settings.soulseek.account_status_message = "Soulseek username and password are required. Use an existing account, or enter a new unique username/password and LJS will validate it."
                        soulseek_result = {
                            "status": "needs_credentials",
                            "running": False,
                            "ready": False,
                            "account_status": settings.soulseek.account_status,
                            "account_status_message": settings.soulseek.account_status_message,
                            "error": settings.soulseek.account_status_message,
                        }
                else:
                    await self._slskd.stop()
                    settings.soulseek.account_status = "not_checked"
                    settings.soulseek.account_status_message = "Soulseek/slskd is disabled."
                    soulseek_result = {"status": "disabled", "running": False, "ready": False}
        self._sm.save(settings)
        return {"status": "ok", "soulseek": soulseek_result}

    async def update_integrations(self, **kwargs: Any) -> dict:
        """Update category-owned service settings from a structured payload.

        The settings UI sends ``category_services`` so the action layer does not
        need to know about global TMDB/Trakt/Plex fields.  Each category entry is
        merged into ``Settings.category_settings[category_id].services`` and is
        persisted by ``SettingsManager`` into ignored ``config/categories`` YAML.
        """
        settings = self._sm.settings
        category_services = kwargs.get("category_services")
        if not isinstance(category_services, dict):
            category_services = {}

        for category_id, category_payload in category_services.items():
            if not isinstance(category_payload, dict):
                continue
            category_config = settings.category_settings.setdefault(str(category_id), {})
            services = category_config.setdefault("services", {})
            if not isinstance(services, dict):
                services = {}
                category_config["services"] = services
            incoming_services = category_payload.get("services")
            if not isinstance(incoming_services, dict):
                continue
            for service_id, service_payload in incoming_services.items():
                if not isinstance(service_payload, dict):
                    continue
                service_config = services.setdefault(str(service_id), {})
                if not isinstance(service_config, dict):
                    service_config = {}
                    services[str(service_id)] = service_config
                previous_effective_trakt_id = None
                if str(category_id) == "media" and str(service_id) == "trakt":
                    from src.integrations.trakt_defaults import resolve_trakt_client_id
                    previous_effective_trakt_id = resolve_trakt_client_id(settings)

                for key, value in service_payload.items():
                    service_config[str(key)] = value or None

                if str(category_id) == "media" and str(service_id) == "trakt":
                    from src.integrations.trakt_defaults import resolve_trakt_client_id
                    next_effective_trakt_id = resolve_trakt_client_id(settings)
                    if next_effective_trakt_id != previous_effective_trakt_id:
                        service_config["access_token"] = None
                        service_config["refresh_token"] = None

        self._sm.save(settings)
        from src.integrations.trakt_defaults import resolve_trakt_client_id
        configured = {
            "tmdb": bool(settings.category_service_value("media", "tmdb", "api_key")),
            "trakt": bool(resolve_trakt_client_id(settings)),
            "plex": bool(settings.category_service_value("media", "plex", "url") and settings.category_service_value("media", "plex", "token")),
            "opensubtitles": bool(settings.category_service_value("media", "opensubtitles", "api_key")),
        }
        from loguru import logger
        logger.info("Shared media service settings updated from category payload: {}", configured)
        return {"status": "ok", "configured": configured}

    async def update_bridges(self, **kwargs: Any) -> dict:
        """Update chat bridge settings (Discord, Telegram, WhatsApp)."""
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

    async def update_password(self, new_password: str) -> dict:
        """Update the web UI password hash."""
        settings = self._sm.settings
        settings.web_password_hash = self._auth.hash_password(new_password)
        self._sm.save(settings)
        return {"status": "ok"}

    async def update_whatsapp(self, **kwargs: Any) -> dict:
        """Update WhatsApp bridge credentials."""
        settings = self._sm.settings
        if "whatsapp_token" in kwargs:
            settings.whatsapp_token = kwargs["whatsapp_token"] or None
        if "whatsapp_phone_number_id" in kwargs:
            settings.whatsapp_phone_number_id = kwargs["whatsapp_phone_number_id"] or None
        if "whatsapp_verify_token" in kwargs:
            settings.whatsapp_verify_token = kwargs["whatsapp_verify_token"] or None
        self._sm.save(settings)
        return {"status": "ok"}
