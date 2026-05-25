"""Torrent download, scan, notification, and user session models."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_serializer, model_validator

from src.core.domain_models.enums import DownloadStatus, DownloadPriority

# --- Downloads ---


class SearchResult(BaseModel):
    """A single torrent search result."""
    title: str
    magnet: Optional[str] = None
    size: str = "Unknown"
    size_bytes: Optional[int] = None
    seeders: Optional[int] = None
    source: str = "unknown"
    url: Optional[str] = None
    quality_score: float = 0.0

    @model_validator(mode="after")
    def _populate_size_bytes(self) -> "SearchResult":
        """Populate size_bytes from the size string if empty."""
        if (self.size_bytes and self.size_bytes > 0) or not self.size or self.size == "Unknown":
            return self
        size_str = self.size.strip().lower()
        try:
            import re
            m = re.search(r"(\d+(?:\.\d+)?)\s*(gb|mb|tb|kb|gib|mib|tib|kib)", size_str)
            if not m:
                self.size_bytes = int(float(size_str))
                return self
            value, unit = float(m.group(1)), m.group(2)
            multipliers = {
                ("tb", "tib"): 1024 ** 4,
                ("gb", "gib"): 1024 ** 3,
                ("mb", "mib"): 1024 ** 2,
                ("kb", "kib"): 1024,
            }
            for units, mult in multipliers.items():
                if unit in units:
                    self.size_bytes = int(value * mult)
                    break
        except (ValueError, TypeError):
            pass
        return self



class DownloadImportContext(BaseModel):
    """Stable media identity captured when a torrent is selected.

    Downloads must not re-derive their identity from a display title at import
    time.  This snapshot follows the torrent from search/selection through
    duplicate checks, target-path planning, library import, and metadata refresh.
    Category-specific meaning stays in JSON fields, but common provider identity
    fields are explicit so ambiguous shows such as same-title revivals never
    collapse to ``title + season + episode``.
    """

    category_id: str = ""
    item_id: str = ""
    provider: str = ""
    provider_media_type: str = ""
    provider_id: str = ""
    external_ids: dict[str, Any] = Field(default_factory=dict)
    canonical_title: str = ""
    display_title: str = ""
    localized_title: str = ""
    original_title: str = ""
    series_start_year: int | None = None
    release_year: int | None = None
    season_order_type: str = "official"
    season: int | None = None
    episode: int | None = None
    episode_title: str = ""
    unit_descriptor: dict[str, Any] = Field(default_factory=dict)
    """Category-owned unit descriptor captured at queue time.

    The core keeps legacy ``season``/``episode`` fields during the migration for
    old rows and agent schemas, but new duplicate checks, batch grouping, and
    user-facing receipts should prefer this opaque descriptor. Its structure is
    declared by the owning category; the download layer only treats
    ``stable_key``, ``granularity``, ``label``, and ``coordinates`` as optional
    conventional fields.
    """
    language: str = ""
    release_title: str = ""
    selected_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata_snapshot: dict[str, Any] = Field(default_factory=dict)
    candidate_snapshot: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _normalize_aliases(cls, value: Any) -> Any:
        """Accept legacy/provider aliases without leaking category specifics."""
        if not isinstance(value, dict):
            return value
        data = dict(value)
        metadata = data.get("metadata_snapshot") if isinstance(data.get("metadata_snapshot"), dict) else {}
        candidate = data.get("candidate_snapshot") if isinstance(data.get("candidate_snapshot"), dict) else {}

        def first(*keys: str) -> Any:
            """Return the first non-empty value across direct, metadata, and candidate payloads."""
            for key in keys:
                if data.get(key) not in (None, ""):
                    return data.get(key)
                if metadata.get(key) not in (None, ""):
                    return metadata.get(key)
                if candidate.get(key) not in (None, ""):
                    return candidate.get(key)
            return None

        provider = first("provider", "metadata_provider")
        if not provider:
            if first("tmdb_id") not in (None, ""):
                provider = "tmdb"
            elif first("tvdb_id") not in (None, ""):
                provider = "tvdb"
            elif first("tvmaze_id") not in (None, ""):
                provider = "tvmaze"
            elif first("imdb_id") not in (None, ""):
                provider = "imdb"
        if provider:
            data["provider"] = str(provider).lower()
        provider_id = first("provider_id", "external_id", "tmdb_id", "tvdb_id", "imdb_id", "tvmaze_id")
        if provider_id is None and provider:
            # Some provider detail payloads use plain ``id``.  Only trust that
            # alias from item metadata or direct overrides, never from torrent
            # candidate payloads where ``id``/``candidate_id`` identifies the
            # release option rather than the media item.
            provider_id = data.get("id") if data.get("id") not in (None, "") else metadata.get("id")
        if provider_id is not None:
            data["provider_id"] = str(provider_id)
        media_type = first("provider_media_type", "media_type", "type")
        if media_type:
            data["provider_media_type"] = str(media_type)
        title = first("canonical_title", "display_title", "title", "name", "item_name")
        if title and not data.get("canonical_title"):
            data["canonical_title"] = str(title)
        if title and not data.get("display_title"):
            data["display_title"] = str(title)

        year = cls._year_from(first("series_start_year", "first_air_date", "first_air_year", "year", "release_year"))
        if year and data.get("series_start_year") is None:
            data["series_start_year"] = year
        release_year = cls._year_from(first("release_year", "release_date", "year"))
        if release_year and data.get("release_year") is None:
            data["release_year"] = release_year
        return data

    @staticmethod
    def _year_from(value: Any) -> int | None:
        """Extract a plausible four-digit release/start year."""
        if isinstance(value, int):
            return value if 1800 <= value <= 2200 else None
        text = str(value or "")
        match = re.search(r"\b(18\d{2}|19\d{2}|20\d{2}|21\d{2}|2200)\b", text)
        return int(match.group(1)) if match else None

    @property
    def stable_provider_key(self) -> str:
        """Return provider/media/id identity, or an empty string if incomplete."""
        if not self.provider or not self.provider_id:
            return ""
        media_type = self.provider_media_type or self.category_id
        return f"{self.provider}:{media_type}:{self.provider_id}"

    @property
    def stable_unit_key(self) -> str:
        """Provider identity plus episode-order namespace and unit coordinates."""
        if not self.stable_provider_key:
            return ""
        season = "-" if self.season is None else str(self.season)
        episode = "-" if self.episode is None else str(self.episode)
        order = self.season_order_type or "official"
        descriptor_key = str((self.unit_descriptor or {}).get("stable_key") or "").strip()
        if descriptor_key:
            return f"{self.stable_provider_key}:{order}:{descriptor_key}"
        return f"{self.stable_provider_key}:{order}:S{season}:E{episode}"

    @property
    def descriptor_sort_key(self) -> tuple[tuple[int, str], ...]:
        """Return a category-neutral ordering key for this unit descriptor.

        Categories may expose ``unit_descriptor.sort_key`` as numbers or text.
        Generic services are allowed to sort by that conventional field, but
        they must not interpret descriptor coordinates such as season, episode,
        volume, track, version, or DLC.  Legacy season/episode fallback remains
        for existing rows that predate descriptors.
        """
        descriptor = self.unit_descriptor or {}
        raw_key = descriptor.get("sort_key")
        if raw_key not in (None, "", [], {}):
            values = raw_key if isinstance(raw_key, list) else [raw_key]
            return tuple(self._sort_component(value) for value in values)
        if self.season is not None or self.episode is not None:
            return (self._sort_component(self.season or 0), self._sort_component(self.episode or 0))
        label = descriptor.get("label") or descriptor.get("stable_key") or ""
        return (self._sort_component(label),)

    @staticmethod
    def _sort_component(value: Any) -> tuple[int, str]:
        """Normalize descriptor ordering values without knowing their domain."""
        if isinstance(value, bool):
            return (0, "1" if value else "0")
        if isinstance(value, (int, float)):
            return (0, f"{float(value):020.6f}")
        text = str(value or "").strip()
        match = re.fullmatch(r"-?\d+(?:\.\d+)?", text)
        if match:
            return (0, f"{float(text):020.6f}")
        return (1, text.casefold())

    @property
    def unit_scope(self) -> str:
        """Return descriptor granularity for duplicate checks.

        The labels ``season``/``episode`` may still appear for legacy TV rows,
        but callers should treat the value as an opaque category granularity.
        """
        granularity = str((self.unit_descriptor or {}).get("granularity") or "").strip()
        if granularity:
            return granularity
        if self.season is None:
            return "item"
        if self.episode is None:
            return "season"
        return "episode"

    @property
    def planning_title(self) -> str:
        """Best title to use for import paths without re-querying providers."""
        return self.display_title or self.canonical_title or self.localized_title or self.original_title

    @property
    def planning_year(self) -> int | None:
        """Best disambiguating year for library folder templates."""
        return self.series_start_year or self.release_year

    @classmethod
    def from_selection(
        cls,
        *,
        category_id: str = "",
        item_id: str = "",
        item_name: str = "",
        season: int | None = None,
        episode: int | None = None,
        unit_descriptor: dict[str, Any] | None = None,
        language: str = "",
        release_title: str = "",
        metadata: dict[str, Any] | None = None,
        candidate: dict[str, Any] | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> "DownloadImportContext":
        """Create a stable import context from resolver/search/cache data."""
        metadata = dict(metadata or {})
        candidate = dict(candidate or {})
        payload = {
            "category_id": category_id,
            "item_id": item_id,
            "canonical_title": item_name,
            "display_title": item_name,
            "season": season,
            "episode": episode,
            "unit_descriptor": dict(unit_descriptor or {}),
            "language": language,
            "release_title": release_title or str(candidate.get("title") or ""),
            "metadata_snapshot": metadata,
            "candidate_snapshot": candidate,
        }
        if overrides:
            payload.update({key: value for key, value in overrides.items() if value not in (None, "", {}, [])})
        return cls(**payload)


class DownloadFileInfo(BaseModel):
    """Per-file tracking within a torrent payload.

    One torrent can contain one file, many category units, extras, samples,
    subtitle sidecars, archives, or alternate releases.  Category-owned unit
    descriptors identify which files are meaningful; generic downloader code
    only tracks bytes, priority, and organization state.
    """

    file_index: int
    """Index in the libtorrent file list."""
    file_path: str
    """Relative path within the torrent (e.g. 'Show.S05/S05E01.mkv')."""
    size: int = 0
    """Total file size in bytes."""
    downloaded_bytes: int = 0
    """Bytes downloaded so far."""
    priority: int = 4
    """Libtorrent file priority: 0=ignore, 4=normal, 7=maximum."""
    season: int | None = None
    """Extracted season number, if parseable."""
    episode: int | None = None
    """Extracted episode number, if parseable."""
    episode_title: str | None = None
    """Episode title, if available."""
    unit_descriptor: dict[str, Any] = Field(default_factory=dict)
    """Category-owned descriptor for this physical file within a torrent."""
    status: str = 'pending'
    """pending → downloading → complete → organized"""
    organized_path: str | None = None
    """Target path in the library after organization."""


class DownloadItem(BaseModel):
    """Tracks the state of a single category-aware download."""

    id: str
    item_name: str
    """Human-readable title of the item being downloaded."""
    magnet: str
    status: DownloadStatus = DownloadStatus.QUEUED
    priority: DownloadPriority = DownloadPriority.NORMAL
    reason: str = ""
    season: int | None = None
    episode: int | None = None
    progress: float = 0.0
    download_rate: float = 0.0
    upload_rate: float = 0.0
    num_peers: int = 0
    num_seeds: int = 0
    total_size: int = 0
    downloaded_bytes: int = 0
    eta_seconds: float = 0.0
    file_path: Optional[str] = None
    user_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    files: list[DownloadFileInfo] = Field(default_factory=list)
    """Per-file tracking for multi-file torrent payloads."""
    language: str = ""
    """Language of the downloaded content (from the search query or tracked item)."""
    category_id: str = ""
    """Category identifier (tv, movie, music, etc.)."""
    item_id: str = ""
    """Category-local item identifier. Defaults to item_name when omitted."""
    torrent_title: str = ""
    """Original torrent/title from the search result."""
    import_context: DownloadImportContext | None = None
    """Stable provider/import identity captured at queue time."""
    save_path: str = ""
    """Absolute root path passed to libtorrent for this torrent payload."""
    sharing_enabled: bool = False
    """Whether this torrent is intended to remain available as a library seed."""
    uploaded_bytes: int = 0
    """Cumulative bytes uploaded according to libtorrent when available."""
    seed_ratio: float = 0.0
    """Cumulative upload/download ratio for library sharing UI reporting."""
    source_seeders: int | None = None
    """Seeder count reported by the search provider/tracker when the candidate was selected.

    This is a snapshot from discovery time and is intentionally separate from
    live libtorrent seed/peer counts.
    """
    stalled_notified: bool = False
    """Whether the user has been notified about this download stalling."""
    stalled_cancel_asked: bool = False
    """Whether the user has been asked if they want to cancel this stalled download."""

    @model_validator(mode="after")
    def _default_item_id(self) -> "DownloadItem":
        """Default item_id to the display name when callers omit it."""
        if not self.item_id:
            self.item_id = self.item_name
        return self

    @property
    def unit_descriptor(self) -> dict[str, Any]:
        """Return the category-owned unit descriptor captured at queue time."""
        return dict(getattr(self.import_context, "unit_descriptor", {}) or {}) if self.import_context else {}

    @property
    def unit_sort_key(self) -> tuple[tuple[int, str], ...]:
        """Return descriptor-first ordering for queue and agent presentation.

        Generic code may sort by this key because it is produced from the
        descriptor's conventional ``sort_key`` field.  It must not inspect
        category-specific descriptor coordinates directly.
        """
        if self.import_context is not None:
            return self.import_context.descriptor_sort_key
        if self.season is not None or self.episode is not None:
            return (
                DownloadImportContext._sort_component(self.season or 0),
                DownloadImportContext._sort_component(self.episode or 0),
            )
        return (DownloadImportContext._sort_component(""),)

    @property
    def unit_label(self) -> str:
        """Return a human label for this category unit without interpreting it."""
        descriptor = self.unit_descriptor
        return str(descriptor.get("label") or descriptor.get("stable_key") or "").strip()

    @property
    def stable_unit_identity(self) -> str:
        """Return the descriptor/provider-backed unit key when available."""
        if self.import_context is not None:
            return self.import_context.stable_unit_key
        descriptor = self.unit_descriptor
        return str(descriptor.get("stable_key") or "").strip()


class BlacklistEntry(BaseModel):
    """An entry in the release group / torrent blacklist."""
    pattern: str
    reason: str = ""
    added_at: datetime = Field(default_factory=datetime.now)


class SubtitleResult(BaseModel):
    """A subtitle search result."""
    title: str
    language: str
    download_url: str
    file_name: str
    source: str


class NotificationMessage(BaseModel):
    """A notification to be sent to users."""
    title: str
    body: str
    level: str = "info"
    timestamp: datetime = Field(default_factory=datetime.now)


class EpisodeRecord(BaseModel):
    """A single downloaded episode tracked in the database."""

    show_name: str
    season: int
    episode: int
    title: str = ""
    quality: str = ""
    language: str = ""
    file_path: str = ""
    download_id: str | None = None
    downloaded_at: str = ""


class ScannedMediaFile(BaseModel):
    """A single media file discovered by a category library scan."""

    season: int | None = None
    episode: int | None = None
    file_path: str = ""
    quality: str = "unknown"
    size_bytes: int = 0
    detected_language: str = ""
    audio_languages: list[str] = Field(default_factory=list)
    audio_tracks: list[dict] = Field(default_factory=list)
    subtitle_languages: list[str] = Field(default_factory=list)
    subtitle_tracks: list[dict] = Field(default_factory=list)
    media_probe: dict = Field(default_factory=dict)


class ScannedLibraryItem(BaseModel):
    """A category-neutral library item discovered during scanning."""

    name: str
    category_id: str
    files: list[ScannedMediaFile] = Field(default_factory=list)
    episodes: dict[int, list[int]] = Field(default_factory=dict)
    seasons: int = 0
    file_count: int = 0
    total_size_bytes: int = 0
    avg_file_size_mb: float = 0.0
    avg_bitrate_kbps: int | None = None
    codecs: list[str] = Field(default_factory=list)
    resolutions: list[str] = Field(default_factory=list)
    detected_language: str = "English"
    detected_languages: list[str] = Field(default_factory=list)
    subtitle_languages: list[str] = Field(default_factory=list)
    year: int | None = None

    @property
    def detailed_episodes(self) -> list[ScannedMediaFile]:
        """Return scanned files for episodic callers that need episode fields."""
        return self.files


class LibraryScanResult(BaseModel):
    """Result of scanning a library directory."""
    items: list[ScannedLibraryItem] = Field(default_factory=list)
    total_files: int = 0
    total_size_bytes: int = 0
    scanned_at: datetime = Field(default_factory=datetime.now)

    def by_category(self, category_id: str) -> list[ScannedLibraryItem]:
        """Return scanned items belonging to one registered category."""
        return [item for item in self.items if item.category_id == category_id]


# --- Suggestions/upgrades ---


class UpgradeCandidate(BaseModel):
    """A category item that has a significantly better quality version available."""

    category_id: str = ""
    item_id: str = ""
    item_name: str = ""
    current_resolution: str = ""
    current_codecs: list[str] = Field(default_factory=list)
    best_upgrade_resolution: str = ""
    best_upgrade_codecs: list[str] = Field(default_factory=list)
    best_upgrade_title: str = ""
    best_upgrade_magnet: str = ""
    quality_improvement: str = ""


# --- Users/sessions ---

