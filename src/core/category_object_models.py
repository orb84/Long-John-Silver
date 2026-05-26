"""Canonical object models for definition-backed Music/Book categories.

These dataclasses are intentionally provider-neutral.  Provider adapters should
normalize MusicBrainz/Open Library/LibriVox/etc. rows into these shapes before
asking the LLM to choose between candidates.  The model keeps stable identifiers
and ambiguity-relevant facets explicit instead of burying them in provider raw
JSON.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ExternalIdentity:
    """One stable provider identifier attached to a canonical object."""

    provider: str
    key: str
    value: str
    entity_type: str = ""

    def as_dict(self) -> dict[str, str]:
        """Return this identity as a JSON-serializable mapping."""
        return asdict(self)


@dataclass
class MusicTrack:
    """Track/recording entry inside a music medium/release."""

    title: str
    position: str = ""
    artist_credit: list[str] = field(default_factory=list)
    duration_ms: int | None = None
    recording_id: str = ""
    isrcs: list[str] = field(default_factory=list)


@dataclass
class MusicMedium:
    """One disc/medium in a music release."""

    position: int = 1
    format: str = ""
    title: str = ""
    track_count: int | None = None
    tracks: list[MusicTrack] = field(default_factory=list)


@dataclass
class MusicReleaseModel:
    """Album/single/EP/compilation release model.

    MusicBrainz separates artist, release-group, release, medium, track, and
    recording.  LJS follows that hierarchy enough for stable IDs and torrent
    matching, while allowing providers with flatter data to populate only the
    known fields.
    """

    model_type: str = "music_release"
    title: str = ""
    artist_credit: list[str] = field(default_factory=list)
    release_group_title: str = ""
    release_type: str = ""
    release_status: str = ""
    country: str = ""
    date: str = ""
    year: str = ""
    label: str = ""
    barcode: str = ""
    catalog_number: str = ""
    disc_count: int | None = None
    total_track_count: int | None = None
    aliases: list[str] = field(default_factory=list)
    media: list[MusicMedium] = field(default_factory=list)
    identities: list[ExternalIdentity] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Return this release as a JSON-serializable mapping."""
        payload = asdict(self)
        payload["identities"] = [identity.as_dict() for identity in self.identities]
        return payload


@dataclass
class BookEditionModel:
    """Book work/edition model for ebooks and audiobook source metadata."""

    model_type: str = "book_edition"
    title: str = ""
    subtitle: str = ""
    work_title: str = ""
    authors: list[str] = field(default_factory=list)
    translators: list[str] = field(default_factory=list)
    illustrators: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    first_publish_year: str = ""
    published_date: str = ""
    publisher: str = ""
    edition_name: str = ""
    isbn_10: list[str] = field(default_factory=list)
    isbn_13: list[str] = field(default_factory=list)
    subjects: list[str] = field(default_factory=list)
    series: str = ""
    series_index: str = ""
    source_level: str = ""
    formats: list[str] = field(default_factory=list)
    page_count: int | None = None
    identities: list[ExternalIdentity] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Return this book edition as a JSON-serializable mapping."""
        payload = asdict(self)
        payload["identities"] = [identity.as_dict() for identity in self.identities]
        return payload


@dataclass
class AudiobookChapterModel:
    """Chapter/section metadata for a narrated book."""

    title: str
    index: int
    duration_seconds: int | None = None
    reader: str = ""
    source_url: str = ""


@dataclass
class AudiobookEditionModel(BookEditionModel):
    """Narrated edition model with narrator/abridgement/chapter facets."""

    model_type: str = "audiobook_edition"
    narrators: list[str] = field(default_factory=list)
    readers: list[str] = field(default_factory=list)
    abridgement: str = ""
    cast: list[str] = field(default_factory=list)
    duration_seconds: int | None = None
    chapter_count: int | None = None
    has_chapters: bool = False
    audio_formats: list[str] = field(default_factory=list)
    chapters: list[AudiobookChapterModel] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Return this audiobook edition as a JSON-serializable mapping."""
        payload = asdict(self)
        payload["identities"] = [identity.as_dict() for identity in self.identities]
        return payload
