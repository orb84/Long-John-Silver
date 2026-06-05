"""Application settings and behavior tracking models."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_serializer, model_validator

from src.core.domain_models.categories import SecurityConfig, StorageConfig
from src.core.domain_models.llm import LLMConfig
from src.core.domain_models.media import ItemList, QualityProfile

# --- Behavior ---


class BehaviorEvent(BaseModel):
    """A recorded user behavior event for implicit preference learning."""

    id: int
    user_id: str
    action: str
    category_id: Optional[str] = None
    item_id: Optional[str] = None
    item_name: Optional[str] = None
    resolution: Optional[str] = None
    codec: Optional[str] = None
    release_group: Optional[str] = None
    file_size_mb: Optional[float] = None
    quality_score: Optional[float] = None
    created_at: datetime = Field(default_factory=datetime.now)


class BandwidthSchedule(BaseModel):
    """Throttling rules for a specific time window and set of days."""
    days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6])  # 0=Mon
    start_time: str = "00:00"
    end_time: str = "23:59"
    max_download_kbps: Optional[int] = None
    max_upload_kbps: Optional[int] = None


class WebSearchConfig(BaseModel):
    """Configuration for general web research providers used by the assistant."""

    enabled: bool = True
    provider: str = "searxng"
    mode: Literal["managed", "manual"] = "managed"
    api_key: str = ""
    api_base: str = ""
    max_results: int = 5
    allow_duckduckgo_fallback: bool = False
    auto_install: bool = True
    managed_port: int = 18888
    managed_source_ref: str = "master"
    default_language: str = "auto"
    default_categories: list[str] = Field(default_factory=lambda: ["general"])
    safe_search: int = 1
    request_timeout_seconds: float = 8.0
    status: str = "not_installed"
    status_message: str = ""
    last_health_check: str = ""

    @model_validator(mode="after")
    def _normalize_web_search(self) -> "WebSearchConfig":
        """Keep web-search settings safe and compatible with legacy configs."""
        provider = str(self.provider or "searxng").strip().lower()
        self.provider = provider or "searxng"
        if self.mode not in {"managed", "manual"}:
            self.mode = "manual" if self.api_base else "managed"
        if self.provider != "searxng" and self.mode == "managed":
            self.mode = "manual"
        if self.max_results < 1:
            self.max_results = 5
        self.managed_port = max(1, min(int(self.managed_port or 18888), 65535))
        self.managed_source_ref = str(self.managed_source_ref or "master").strip() or "master"
        self.safe_search = max(0, min(int(self.safe_search or 0), 2))
        if self.request_timeout_seconds <= 0:
            self.request_timeout_seconds = 8.0
        if not self.default_categories:
            self.default_categories = ["general"]
        self.default_categories = [str(c).strip() for c in self.default_categories if str(c).strip()] or ["general"]
        return self




class EmbeddingSettings(BaseModel):
    """Local semantic-memory embedding runtime settings.

    The default uses a small ONNX/FastEmbed model that is downloaded into the
    application cache on first use. Users may disable local embeddings or route
    embedding calls through the explicit LLM embedding task config instead.
    """

    enabled: bool = True
    provider: Literal["builtin", "llm", "disabled"] = "builtin"
    builtin_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    dimension: int = 384
    cache_dir: str = "./data/embedding_models"
    auto_download: bool = True
    warmup_on_startup: bool = True
    max_model_size_mb: int = 150

    @model_validator(mode="after")
    def _normalize_embedding_settings(self) -> "EmbeddingSettings":
        """Keep provider/enabled flags aligned and values safe."""
        if not self.enabled:
            self.provider = "disabled"
        if self.max_model_size_mb < 1:
            self.max_model_size_mb = 150
        if self.dimension < 1:
            self.dimension = 384
        if not self.builtin_model.strip():
            self.builtin_model = "sentence-transformers/all-MiniLM-L6-v2"
        return self


class LibrarySharingMode(str, Enum):
    """How torrent payloads are exposed to the media library.

    ``seed_in_place`` keeps the torrent payload as the library copy so the
    files remain byte-for-byte connected to their original swarm.
    """

    DISABLED = "disabled"
    SEED_IN_PLACE = "seed_in_place"


class SharingSettings(BaseModel):
    """User-configurable library seeding policy.

    The first implementation intentionally supports only seed-in-place mode:
    torrent payloads are downloaded into category library folders and kept
    untouched while seeding. Future overlay modes can extend this model without
    changing download queue semantics.
    """

    enabled: bool = False
    """Whether completed torrent-backed library files should remain shared."""
    mode: LibrarySharingMode = LibrarySharingMode.DISABLED
    """Selected library sharing mode. Only seed_in_place is active today."""
    library_upload_speed_kbps: Optional[int] = 0
    """Aggregate upload cap reserved for library seeding torrents, in kB/s."""
    active_seed_slots: int = 2
    """Maximum number of library torrents libtorrent should actively seed."""
    seed_ratio_target: float = 2.0
    """Default ratio target before LJS may stop seeding a library item."""
    seed_duration_hours: int = 168
    """Minimum time a library item should remain available to peers."""
    pause_when_downloading: bool = False
    """Whether library seeding should pause while downloads are active."""
    category_overrides: dict[str, bool] = Field(default_factory=dict)
    """Optional per-category opt-in/out map; missing values inherit enabled."""

    @model_validator(mode="after")
    def _normalize_mode(self) -> "SharingSettings":
        """Keep mode and enabled aligned for callers that only set one field."""
        if self.enabled and self.mode == LibrarySharingMode.DISABLED:
            self.mode = LibrarySharingMode.SEED_IN_PLACE
        if not self.enabled:
            self.mode = LibrarySharingMode.DISABLED
        self.active_seed_slots = max(0, int(self.active_seed_slots or 0))
        if self.library_upload_speed_kbps is not None:
            self.library_upload_speed_kbps = max(0, int(self.library_upload_speed_kbps or 0))
        self.seed_ratio_target = max(0.0, float(self.seed_ratio_target or 0.0))
        self.seed_duration_hours = max(0, int(self.seed_duration_hours or 0))
        return self

    def category_enabled(self, category_id: str | None) -> bool:
        """Return whether sharing is enabled for a specific category.

        Args:
            category_id: Category identifier such as ``tv`` or ``movie``.

        Returns:
            True when global sharing is enabled and the category is not
            explicitly disabled.
        """
        if not self.enabled or self.mode != LibrarySharingMode.SEED_IN_PLACE:
            return False
        key = str(category_id or "")
        if key in self.category_overrides:
            return bool(self.category_overrides[key])
        return True



class SoulseekShareMode(str, Enum):
    """How LJS tells slskd which local folders may be shared back."""

    DISABLED = "disabled"
    FULL_LIBRARY = "full_library"
    CUSTOM = "custom"


class SoulseekSettings(BaseModel):
    """Configuration for the optional slskd-backed Soulseek companion source.

    Soulseek is not torrent-like: downloads are queued against individual users
    through slskd, and sharing is a first-class part of the network.  These
    settings keep that source behind its own explicit boundary instead of
    pretending Soulseek candidates are magnet links.
    """

    enabled: bool = False
    managed: bool = True
    """Whether LJS owns the local slskd process lifecycle. Disable only for advanced remote slskd setups."""
    auto_install: bool = True
    """Whether enabling Soulseek should install the native slskd binary automatically."""
    host: str = "http://127.0.0.1:5030"
    url_base: str = "/"
    api_key: str = ""
    verify_ssl: bool = False
    web_username: str = ""
    web_password: str = ""
    jwt_key: str = ""
    soulseek_username: str = ""
    soulseek_password: str = ""
    app_dir: str = "./data/slskd"
    downloads_dir: str = ""
    incomplete_dir: str = ""
    managed_directory_mode: Literal["explicit", "slskd_default"] = "explicit"
    """Managed slskd storage mode. ``slskd_default`` is legacy-only and is migrated back to explicit mode at startup."""
    managed_runtime_app_dir: str = ""
    """Legacy Round 168 APP_DIR override; ignored in managed mode and cleared at startup."""
    share_mode: SoulseekShareMode = SoulseekShareMode.FULL_LIBRARY
    share_directories: list[str] = Field(default_factory=list)
    excluded_share_directories: list[str] = Field(default_factory=list)
    share_filters: list[str] = Field(default_factory=lambda: [
        r"\.DS_Store$",
        r"Thumbs\.db$",
        r"desktop\.ini$",
        r"\.ljs-trash(?:/|$)",
        r"settings\.local\.yaml$",
        r"security_audit\.jsonl$",
    ])
    search_enabled_categories: list[str] = Field(default_factory=lambda: ["music", "audiobooks", "ebooks", "tv", "movie", "general"])
    parallel_search_enabled: bool = True
    """Whether category torrent searches should also fetch a Soulseek companion result set when Soulseek is ready."""
    download_preference: Literal["torrent_first", "soulseek_first", "ask"] = "torrent_first"
    """Preferred first download backend when both torrent and Soulseek candidates look viable."""
    companion_when_no_torrent_results: bool = False
    auto_retry_unmatched_searches: bool = True
    """Automatically schedule recurring assistant checks when torrent/Soulseek searches find nothing."""
    retry_search_interval_minutes: int = 360
    """Cadence for automatic no-match retry searches. Six hours samples different Soulseek peer availability windows."""
    retry_search_max_runs: int = 12
    """Maximum automatic retry runs for one missed-search watch before it retires."""
    account_status: str = "not_checked"
    """Soulseek network account status: not_checked, needs_credentials, checking, ready, auth_failed, storage_unavailable, or error."""
    account_status_message: str = ""
    """Human-readable Soulseek setup/login status for setup and Compass."""
    account_checked_at: str = ""
    """ISO timestamp of the last managed slskd Soulseek account validation."""
    max_search_results: int = 20
    search_timeout_seconds: float = 12.0

    @field_validator("host", mode="before")
    @classmethod
    def _normalize_host(cls, value: Any) -> str:
        text = str(value or "http://127.0.0.1:5030").strip()
        if not text:
            return "http://127.0.0.1:5030"
        if not re.match(r"^[a-z][a-z0-9+.-]*://", text, re.I):
            text = "http://" + text
        return text.rstrip("/")

    @field_validator("url_base", mode="before")
    @classmethod
    def _normalize_url_base(cls, value: Any) -> str:
        text = str(value or "/").strip() or "/"
        if not text.startswith("/"):
            text = "/" + text
        return text.rstrip("/") or "/"

    @field_validator("share_directories", "excluded_share_directories", "search_enabled_categories", "share_filters", mode="before")
    @classmethod
    def _normalize_string_list(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            parts = [part.strip() for part in value.replace(";", "\n").splitlines()]
            return [part for part in parts if part]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    @model_validator(mode="after")
    def _normalize_soulseek(self) -> "SoulseekSettings":
        self.max_search_results = max(1, min(int(self.max_search_results or 20), 100))
        self.search_timeout_seconds = max(2.0, min(float(self.search_timeout_seconds or 12.0), 60.0))
        self.retry_search_interval_minutes = max(30, min(int(self.retry_search_interval_minutes or 360), 7 * 24 * 60))
        self.retry_search_max_runs = max(1, min(int(self.retry_search_max_runs or 12), 100))
        if self.managed:
            # Blank/legacy managed paths are resolved by slskd_config to the
            # user-selected LJS download_dir.  Managed slskd is a download
            # backend, not a project-local cache.
            if not str(self.downloads_dir or "").strip() or str(self.downloads_dir).strip().replace("\\", "/") == "./downloads/soulseek":
                self.downloads_dir = ""
            if not str(self.incomplete_dir or "").strip() or str(self.incomplete_dir).strip().replace("\\", "/") == "./downloads/soulseek-incomplete":
                self.incomplete_dir = ""
        if not self.share_filters:
            self.share_filters = [r"\.DS_Store$", r"Thumbs\.db$", r"desktop\.ini$"]
        self.search_enabled_categories = [str(cat).strip().lower() for cat in self.search_enabled_categories if str(cat).strip()]
        if self.download_preference not in {"torrent_first", "soulseek_first", "ask"}:
            self.download_preference = "torrent_first"
        if self.share_mode == SoulseekShareMode.DISABLED:
            self.share_directories = []
        return self

    @property
    def api_configured(self) -> bool:
        """Return whether LJS has enough information to call slskd."""
        return bool(self.enabled and self.host and self.api_key)

    @property
    def soulseek_credentials_configured(self) -> bool:
        """Return whether slskd can be configured with Soulseek credentials."""
        return bool(self.soulseek_username and self.soulseek_password)

    @property
    def account_ready(self) -> bool:
        """Return whether managed slskd has confirmed a Soulseek network login."""
        return self.account_status == "ready"


class Settings(BaseModel):
    """Main application settings."""
    llm: LLMConfig = Field(default_factory=LLMConfig)
    embeddings: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    web_search: WebSearchConfig = Field(default_factory=WebSearchConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    sharing: SharingSettings = Field(default_factory=SharingSettings)
    soulseek: SoulseekSettings = Field(default_factory=SoulseekSettings)
    tracked_items: ItemList = Field(default_factory=ItemList)
    bandwidth_schedules: list[BandwidthSchedule] = Field(default_factory=list)
    download_dir: str = "./downloads"
    library_root: str = "./library"
    library_paths: dict[str, str] = Field(default_factory=dict)
    category_settings: dict[str, dict[str, Any]] = Field(default_factory=dict)
    """Effective category settings loaded from ignored config/categories/<category_id>.yaml."""

    def category_config(self, category_id: str | None) -> dict[str, Any]:
        """Return the effective local configuration for one category.

        Category configuration is the authority for category-owned paths,
        external services, tool policy, LLM guidance, and download preferences.
        Concrete category configs may inherit values from abstract configs such
        as ``media`` during settings load.
        """
        if not category_id:
            return {}
        value = self.category_settings.get(str(category_id), {})
        return value if isinstance(value, dict) else {}

    def category_service_config(self, category_id: str | None, service_id: str | None) -> dict[str, Any]:
        """Return ``services.<service_id>`` for a category config."""
        if not category_id or not service_id:
            return {}
        services = self.category_config(category_id).get("services")
        if not isinstance(services, dict):
            return {}
        value = services.get(str(service_id), {})
        return value if isinstance(value, dict) else {}

    def category_service_value(
        self,
        category_id: str | None,
        service_id: str | None,
        key: str,
    ) -> Any:
        """Return one service value from effective category config."""
        value = self.category_service_config(category_id, service_id).get(key)
        if value not in (None, ""):
            return value
        return None

    def category_service_enabled(self, category_id: str | None, service_id: str | None, default: bool = True) -> bool:
        """Return whether a category-local service is enabled."""
        value = self.category_service_config(category_id, service_id).get("enabled")
        if value is None:
            metadata = self.category_config(category_id).get("metadata")
            providers = metadata.get("providers") if isinstance(metadata, dict) else {}
            provider_cfg = providers.get(str(service_id)) if isinstance(providers, dict) else None
            if isinstance(provider_cfg, dict) and "enabled" in provider_cfg:
                value = provider_cfg.get("enabled")
        return bool(default if value is None else value)

    def first_category_service_value(
        self,
        category_ids: list[str],
        service_id: str,
        key: str,
    ) -> Any:
        """Return the first configured service value among category configs."""
        for category_id in category_ids:
            value = self.category_service_value(category_id, service_id, key)
            if value not in (None, ""):
                return value
        return None
    language: str = "English"
    discord_token: Optional[str] = None
    discord_channel_id: Optional[int] = None
    telegram_token: Optional[str] = None
    whatsapp_token: Optional[str] = None
    whatsapp_phone_number_id: Optional[str] = None
    whatsapp_verify_token: Optional[str] = None
    active_persona: str = "default"
    jackett_url: Optional[str] = None
    jackett_api_key: Optional[str] = None
    web_username: str = "admin"
    web_password_hash: Optional[str] = None
    setup_complete: bool = False
    auto_start_at_login: bool = False
    """Whether LJS should register a user-level OS startup entry.

    The actual platform entry is managed by ``AutoStartManager`` from setup
    and Compass actions; this field records the user preference in settings.
    """
    web_port: int = 8088
    default_quality: QualityProfile = Field(default_factory=QualityProfile)
    subtitle_languages: list[str] = Field(default_factory=lambda: ["en"])
    max_concurrent_downloads: int = 5
    auto_delete_watched: bool = False
    auto_download: bool = False
    auto_discover: bool = True
    direct_scraper_fallback: bool = False
    last_library_scan_at: str = ""
    last_media_metadata_repair_at: str = ""
    """Last time the background stale stream-metadata repair was allowed to trigger a full scan."""
    stall_check_interval_minutes: int = 10
    stall_alternative_hours: float = 1.0
    stall_cancel_hours: float = 5.0
    stall_health_window_minutes: float = 30.0
    """No-byte-progress window before an active download is parked."""
    stall_test_interval_minutes: float = 60.0
    """How often a parked torrent gets a priority test window."""
    stall_test_duration_minutes: float = 15.0
    """How long a parked torrent is allowed to prove byte movement during a test."""
    stall_alternative_cooldown_minutes: float = 180.0
    """How often to look for replacement candidates for the same stalled item."""
    stall_min_progress_bytes: int = 524288
    """Minimum byte delta considered real progress by the health supervisor."""
    stall_idle_rate_bps: int = 1024
    """Instantaneous rate below which a no-progress torrent can be considered idle."""



class ScheduledTask(BaseModel):
    """A persisted assistant automation or reminder.

    The scheduler supports three user-facing shapes while keeping one storage
    model: lightweight reminders that simply notify the user, one-off scheduled
    prompts that run through the assistant at a future time, and recurring
    checks/reports that re-run on an interval.  Category-specific recurring
    media jobs stay in category lifecycle hooks; this model is for user-created
    assistant tasks such as "remind me in 7 days" or "check this torrent
    again in 3 weeks and tell me what you find".
    """
    id: str
    prompt: str
    interval_minutes: int = 10080
    user_id: Optional[str] = None
    channel: str = "web"
    enabled: bool = True
    last_run_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    task_type: str = "scheduled_prompt"
    schedule_type: str = "recurring"
    title: str = ""
    due_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None
    run_count: int = 0
    max_runs: Optional[int] = None
    session_id: Optional[str] = None
    last_error: str = ""


class WatchedItem(BaseModel):
    """A media item that has been watched (from Plex or manual tracking)."""
    title: str
    media_type: str = "episode"
    season: Optional[int] = None
    episode: Optional[int] = None
    year: Optional[int] = None
    watched_at: Optional[datetime] = None
    file_path: Optional[str] = None


# --- Web Search Models ---

