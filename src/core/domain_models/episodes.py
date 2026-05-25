"""Episode, upgrade, suggestion, and taste profile models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_serializer, model_validator

from src.core.domain_models.downloads import EpisodeRecord
from src.core.domain_models.enums import ShowLifecycleState

# --- Episode & Show Management ---


class ShowEpisodes(BaseModel):
    """All episode data for a show: downloaded, airing, upcoming."""

    show_name: str
    seasons: dict[int, list[dict]] = Field(default_factory=dict)
    downloaded_episodes: list[EpisodeRecord] = Field(default_factory=list)
    total_downloaded: int = 0
    total_aired: int = 0


class UpgradeRecord(BaseModel):
    """An upgrade candidate stored for user approval."""

    id: int | None = None
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
    status: str = "pending"
    found_at: str = ""


class SuggestedAction(BaseModel):
    """A suggested action for a category item, shown in the UI."""

    category_id: str = ""
    item_id: str = ""
    item_name: str = ""
    action_type: str
    title: str
    description: str
    endpoint: str = ""
    method: str = "POST"
    body: dict | None = None


class SuggestedActionRecord(BaseModel):
    """A persisted suggested action stored in the database."""

    id: int | None = None
    category_id: str = ""
    item_id: str = ""
    item_name: str = ""
    action_type: str
    title: str
    description: str = ""
    endpoint: str = ""
    method: str = "POST"
    body_json: str = "{}"
    priority: int = 0
    status: str = "pending"
    metadata_json: str = "{}"
    created_at: str = ""
    approved_at: str | None = None
    denied_at: str | None = None


class EpisodeTarget(BaseModel):
    """The next episode to search for, resolved by category-owned episodic workflows.

    Carries the season and episode to look for, which guide resolved it,
    the expected air date (if known), and the show's lifecycle state
    so the scheduler can adapt its check frequency.
    """

    season: int
    episode: int
    source: str = "heuristic"
    air_date: str | None = None
    show_status: ShowLifecycleState = ShowLifecycleState.UNKNOWN


class CategoryMediaMetadata(BaseModel):
    """Category-owned external metadata stored in generic metadata envelopes.

    The model deliberately uses category-neutral field names so the core taste,
    recommendation, and prompt layers do not need TV-show or movie-specific
    schemas. Built-in categories may map provider-specific fields such as TMDB
    first-air dates or runtime into these generic slots before persisting.
    """

    category_id: str = ""
    item_id: str = ""
    display_name: str = ""
    provider: str = ""
    external_id: str = ""
    tmdb_id: int | None = None
    tvmaze_id: int | None = None
    genres: list[str] = Field(default_factory=list)
    overview: str = ""
    cast_names: list[str] = Field(default_factory=list)
    directors: list[str] = Field(default_factory=list)
    writers: list[str] = Field(default_factory=list)
    producers: list[str] = Field(default_factory=list)
    rating: float | None = None
    vote_count: int = 0
    lifecycle_status: str = ""
    seasons: list[dict[str, Any]] = Field(default_factory=list)
    number_of_seasons: int | None = None
    number_of_episodes: int | None = None
    first_release_date: str = ""
    last_release_date: str = ""
    network: str = ""
    runtime_minutes: int | None = None
    poster_path: str = ""
    poster_url: str = ""
    local_poster_path: str = ""
    local_poster_url: str = ""
    enriched_at: str = ""


class GenreProfile(BaseModel):
    """Aggregated genre preference vector from the user's library."""

    counts: dict[str, float] = Field(default_factory=dict)
    primary: list[str] = Field(default_factory=list)


class PeopleProfile(BaseModel):
    """Aggregated people (actors, directors, writers) from the user's library."""

    actors: dict[str, float] = Field(default_factory=dict)
    directors: dict[str, float] = Field(default_factory=dict)
    writers: dict[str, float] = Field(default_factory=dict)


class TasteProfile(BaseModel):
    """Complete inferred taste profile from category metadata and behavior.

    ``genres`` and ``metadata_dimensions`` remain compact numeric summaries for
    prompt efficiency.  The richer fields below keep the profile evidence-based:
    broad facets are allowed to have both positive and negative contexts instead
    of being collapsed into a naive scoreboard.
    """

    genres: GenreProfile = Field(default_factory=GenreProfile)
    people: PeopleProfile = Field(default_factory=PeopleProfile)
    category_counts: dict[str, float] = Field(default_factory=dict)
    metadata_dimensions: dict[str, dict[str, float]] = Field(default_factory=dict)
    """Category-defined dimensions such as platforms, studios, tags, mechanics, or moods."""
    facet_affinities: dict[str, dict[str, float]] = Field(default_factory=dict)
    """Derived signed affinity by facet type/value, rebuilt from raw evidence."""
    positive_contexts: dict[str, list[str]] = Field(default_factory=dict)
    """Positive evidence contexts for broad facets, e.g. genre -> grounded realism."""
    negative_contexts: dict[str, list[str]] = Field(default_factory=dict)
    """Negative evidence contexts for broad facets, e.g. war -> propaganda tone."""
    evidence_counts: dict[str, int] = Field(default_factory=dict)
    top_items: list[str] = Field(default_factory=list)
    summary: str = ""
    updated_at: str = ""

