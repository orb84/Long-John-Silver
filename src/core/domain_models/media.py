"""Polymorphic category media item models and quality profiles."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_serializer, model_validator

from src.core.domain_models.enums import SizeLimitMode


# --- Media items ---


class QualityProfile(BaseModel):
    """Desired quality constraints for media downloads."""
    preferred_resolution: str = "1080p"
    preferred_bitrate_kbps: Optional[int] = None
    max_bitrate_kbps: Optional[int] = None
    max_file_size_mb: Optional[int] = None
    size_limit_mode: SizeLimitMode = SizeLimitMode.SMART
    preferred_codecs: list[str] = Field(default_factory=lambda: ["h264", "h265", "hevc"])
    prefer_hdr: bool = False
    max_download_speed_kbps: Optional[int] = None
    max_upload_speed_kbps: Optional[int] = None
    seed_ratio_target: float = 2.0
    seed_duration_hours: int = 48


class CategoryItem(BaseModel):
    """Abstract base for a tracked item.

    Each category (media or otherwise) extends this base. The category system
    dispatches updates polymorphically — no hardcoded 'tv'/'movie'
    string checks anywhere in the scheduler or API.
    """

    key: str
    """Category-local stable item identifier used as the database key."""

    display_name: str | None = None
    """Human-readable item name shown in the UI; defaults to ``key``."""

    enabled: bool = True
    discovered: bool = False
    properties: dict[str, Any] = Field(default_factory=dict)
    """Category-defined custom properties validated by the owning category."""
    metadata: dict[str, Any] = Field(default_factory=dict)
    """Provider or scanner metadata cached for the owning category."""
    state: dict[str, Any] = Field(default_factory=dict)
    """Mutable category-owned runtime state that is not part of settings."""
    
    last_checked_at: str | None = None
    """ISO 8601 timestamp of when this item was last checked for updates."""

    check_interval_days: int = 7
    """Base check interval. Overridden by lifecycle-aware subclasses."""

    @property
    def item_type(self) -> str:
        """Type identifier used for polymorphic (de)serialization."""
        return "base"

    @model_serializer(mode="wrap")
    def _serialize_with_type(self, handler) -> dict[str, Any]:
        """Ensure item_type is always included in the serialized output."""
        data = handler(self)
        data["item_type"] = self.item_type
        return data

    @property
    def is_episodic(self) -> bool:
        """Whether this item has seasons/episodes."""
        return False

    @property
    def needs_periodic_checks(self) -> bool:
        """Whether the scheduler should periodically call update() on this item."""
        return False

    @property
    def update_interval_seconds(self) -> int:
        """Seconds between update() calls from the scheduler."""
        return 0

    def format_progress(self) -> str:
        """Human-readable progress string for UI display."""
        return "—"


class MediaCategoryItem(CategoryItem):
    """Intermediate class for media-based categories (TV, Movies, etc.)."""
    
    language: str = "English"
    subtitle_languages: list[str] = Field(default_factory=list)
    quality: QualityProfile = Field(default_factory=QualityProfile)
    auto_download: bool | None = None
    """Per-item override. None means 'use the global auto_download setting'."""

    last_upgrade_scan_at: str | None = None
    """ISO 8601 timestamp of when this item was last scanned for quality upgrades."""

    tmdb_id: int | None = None
    genres: list[str] = Field(default_factory=list)
    overview: str = ""
    cast_names: list[str] = Field(default_factory=list)

    @field_validator("cast_names", mode="before")
    @classmethod
    def _parse_cast_names(cls, v: Any) -> list[str]:
        """Ensure cast_names is a list of strings even if input is list of dicts."""
        if not isinstance(v, list):
            return []
        parsed_cast = []
        for entry in v:
            if isinstance(entry, dict) and "name" in entry:
                parsed_cast.append(entry["name"])
            elif isinstance(entry, str):
                parsed_cast.append(entry)
        return parsed_cast


class TvShowItem(MediaCategoryItem):
    """A television show being tracked for new episodes.

    TV treats new-episode automation as item-owned and default-on.  Users can
    disable it from the TV show inspector, but missing/null legacy values are
    normalized to True so tracked active shows keep following newly aired
    episodes after upgrades.
    """

    auto_download: bool | None = True
    """Whether release-watch automation may auto-download new TV episodes."""

    @model_validator(mode="after")
    def _default_new_episode_auto_download(self) -> "TvShowItem":
        """Normalize legacy null automation values to the TV default.

        Returns:
            The normalized TV show item.
        """
        if self.auto_download is None:
            self.auto_download = True
        return self

    @property
    def item_type(self) -> str:
        """Execute the public TvShowItem.item_type behavior.

        This method is a supported extension point for callers outside the
        class.  Keep its input/output contract stable and move specialized
        logic into collaborators or protected helpers as the feature grows.
        """
        return "tv"

    last_season: int | None = None
    last_episode: int | None = None
    tvmaze_id: int | None = None
    
    _lifecycle: str = "unknown"
    """Internal: set by the episode guide after lifecycle lookup."""

    @property
    def is_episodic(self) -> bool:
        """Return whether TvShowItem satisfies this condition.

        Use this method as a read-only capability check.  Avoid side effects
        so callers can safely use it in routing, health checks, and tests.
        """
        return True

    @property
    def needs_periodic_checks(self) -> bool:
        """Return whether TvShowItem satisfies this condition.

        Use this method as a read-only capability check.  Avoid side effects
        so callers can safely use it in routing, health checks, and tests.
        """
        return True

    @property
    def update_interval_seconds(self) -> int:
        """Lifecycle-aware check interval."""
        if self._lifecycle == "active_airing":
            return 7 * 86400
        if self._lifecycle == "between_seasons":
            return 90 * 86400
        if self._lifecycle == "hiatus":
            return 90 * 86400
        if self._lifecycle == "ended":
            return 180 * 86400
        return self.check_interval_days * 86400

    def format_progress(self) -> str:
        """Format data for the progress surface.

        Return presentation-ready text or values without mutating domain
        objects.  Keep formatting stable because chat, UI, and tests may rely
        on the resulting shape.
        """
        if self.last_season is not None and self.last_episode is not None:
            return f"S{self.last_season:02d}E{self.last_episode:02d}"
        return "—"


class MovieItem(MediaCategoryItem):
    """A movie in the library."""

    @property
    def item_type(self) -> str:
        """Execute the public MovieItem.item_type behavior.

        This method is a supported extension point for callers outside the
        class.  Keep its input/output contract stable and move specialized
        logic into collaborators or protected helpers as the feature grows.
        """
        return "movie"

    year: int | None = None
    resolution: str | None = None
    codec: str | None = None

    @property
    def needs_periodic_checks(self) -> bool:
        """Return whether MovieItem satisfies this condition.

        Use this method as a read-only capability check.  Avoid side effects
        so callers can safely use it in routing, health checks, and tests.
        """
        return False

    @property
    def update_interval_seconds(self) -> int:
        """Execute the public MovieItem.update_interval_seconds behavior.

        This method is a supported extension point for callers outside the
        class.  Keep its input/output contract stable and move specialized
        logic into collaborators or protected helpers as the feature grows.
        """
        return 0

    def format_progress(self) -> str:
        """Format data for the progress surface.

        Return presentation-ready text or values without mutating domain
        objects.  Keep formatting stable because chat, UI, and tests may rely
        on the resulting shape.
        """
        return "Downloaded" if self.resolution else "Missing"


class GenericMediaItem(MediaCategoryItem):
    """A tracked item for media categories without a dedicated model class."""

    category_id: str = "media"

    @property
    def item_type(self) -> str:
        """Execute the public GenericMediaItem.item_type behavior.

        This method is a supported extension point for callers outside the
        class.  Keep its input/output contract stable and move specialized
        logic into collaborators or protected helpers as the feature grows.
        """
        return self.category_id


# ── Union type for polymorphic deserialization ────────────────

def _deserialize_item(data: dict) -> CategoryItem:
    """Factory: construct the correct CategoryItem subclass from raw dict."""
    # Skip if already a typed CategoryItem (avoids double-processing)
    if isinstance(data, CategoryItem):
        return data

    # Priority 1: Explicitly persisted item_type (The Truth)
    item_type = data.get("item_type") or data.get("category")
    if item_type == "base":
        item_type = None
    
    if not item_type:
        data["category_id"] = data.get("category_id", "media")
        return GenericMediaItem(**data)

    if item_type == "movie":
        return MovieItem(**data)
    if item_type and item_type != "tv":
        data["category_id"] = item_type
        return GenericMediaItem(**data)

    return TvShowItem(**data)


class ItemList(BaseModel):
    """A list of CategoryItem subclasses with polymorphic (de)serialization.

    Used as the container type for `Settings.tracked_items`. Pydantic would
    otherwise lose the subclass and deserialize everything as plain
    CategoryItem (losing season/episode/etc.).
    """

    items: list[CategoryItem] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _deserialize_items(cls, data: Any) -> Any:
        if isinstance(data, dict) and "items" in data:
            data["items"] = [_deserialize_item(i) for i in data["items"]]
        elif isinstance(data, list):
            return {"items": [_deserialize_item(i) for i in data]}
        return data

    @model_serializer
    def _serialize(self) -> dict[str, Any]:
        """Serialize each item using its actual subclass, not the base."""
        return {
            "items": [item.model_dump() for item in self.items]
        }

    def __iter__(self):
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index: int) -> CategoryItem:
        return self.items[index]

    def append(self, item: CategoryItem) -> None:
        """Add items to this collection while preserving its public contract.

        Use this method instead of mutating internal lists directly so future
        validation, deduplication, or event hooks can be added centrally.
        """
        self.items.append(item)

    def extend(self, items: list[CategoryItem]) -> None:
        """Add items to this collection while preserving its public contract.

        Use this method instead of mutating internal lists directly so future
        validation, deduplication, or event hooks can be added centrally.
        """
        self.items.extend(items)

