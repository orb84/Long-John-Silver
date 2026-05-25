"""Application settings and behavior tracking models."""

from __future__ import annotations

from datetime import datetime
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
    """Configuration for general web search providers used by the assistant."""

    enabled: bool = True
    provider: str = "brave"
    api_key: str = ""
    api_base: str = ""
    max_results: int = 5
    allow_duckduckgo_fallback: bool = False






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

class Settings(BaseModel):
    """Main application settings."""
    llm: LLMConfig = Field(default_factory=LLMConfig)
    embeddings: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    web_search: WebSearchConfig = Field(default_factory=WebSearchConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    sharing: SharingSettings = Field(default_factory=SharingSettings)
    tracked_items: ItemList = Field(default_factory=ItemList)
    bandwidth_schedules: list[BandwidthSchedule] = Field(default_factory=list)
    download_dir: str = "./downloads"
    library_root: str = "./library"
    library_paths: dict[str, str] = Field(default_factory=dict)
    category_settings: dict[str, dict[str, Any]] = Field(default_factory=dict)
    """Effective category settings loaded from ignored config/categories/<category_id>.yaml."""
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
    opensubtitles_api_key: Optional[str] = None
    trakt_client_id: Optional[str] = "42bc6ba1535878e40f4773d3e064809f8caf7347e4ba2b3f3ddc61b32f1ab2ac"
    """The default hardcoded Trakt Client ID for LJS integration, so the user does not need to configure it."""
    trakt_access_token: Optional[str] = None
    trakt_refresh_token: Optional[str] = None
    tmdb_api_key: Optional[str] = None
    plex_url: Optional[str] = None
    plex_token: Optional[str] = None
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
    direct_scraper_fallback: bool = True
    last_library_scan_at: str = ""
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
    """A recurring natural-language task processed by the AI assistant.

    Examples: 'weekly TV show report', 'daily download check',
    'notify me when new episodes of X air'.
    """
    id: str
    prompt: str
    interval_minutes: int = 10080
    user_id: Optional[str] = None
    channel: str = "web"
    enabled: bool = True
    last_run_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.now)


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

