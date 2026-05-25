"""
Data model types for media categories in LJS.

Shared dataclasses used across category implementations for scan
results, parsed media info, and structured file observations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScannedFileObservation:
    """Detailed info for one scanned local media file.

    ``season`` and ``episode`` are optional structured-unit coordinates.  They
    are meaningful only to categories that define such coordinates in their
    object spec; flat-file categories use zero values.
    """
    season: int = 0
    episode: int = 0
    file_path: str = ""
    quality: str = "unknown"
    size_bytes: int = 0
    detected_language: str = ""
    audio_languages: list[str] = field(default_factory=list)
    audio_tracks: list[dict[str, Any]] = field(default_factory=list)
    subtitle_languages: list[str] = field(default_factory=list)
    subtitle_tracks: list[dict[str, Any]] = field(default_factory=list)
    media_probe: dict[str, Any] = field(default_factory=dict)


# Backwards-compatible alias for older category code.  New code should use the
# category-neutral ScannedFileObservation name.
ScannedEpisode = ScannedFileObservation


@dataclass
class ScannedItem:
    """A single item found during a category library scan.

    Category scanners return this lightweight dataclass, while the public
    scanner facade converts it to ``ScannedLibraryItem`` for persistence.
    ``detailed_episodes`` is the historical field name; ``files`` is the
    canonical neutral alias that new category object builders should use.
    """
    name: str
    category_id: str
    resolutions: list[str] = field(default_factory=list)
    codecs: list[str] = field(default_factory=list)
    episodes: dict[int, list[int]] = field(default_factory=dict)
    detailed_episodes: list[ScannedFileObservation] = field(default_factory=list)
    seasons: int = 0
    file_count: int = 0
    total_size_bytes: int = 0
    detected_language: str = "English"
    detected_languages: list[str] = field(default_factory=list)
    subtitle_languages: list[str] = field(default_factory=list)
    year: int | None = None

    @property
    def files(self) -> list[ScannedFileObservation]:
        """Return scanned local file observations under the neutral name.

        Older category scanners populated ``detailed_episodes`` even for flat
        categories.  Canonical object builders use ``files`` so they can treat
        scan output as local file evidence without depending on that legacy
        episodic name.
        """
        return self.detailed_episodes


@dataclass
class ParsedMedia:
    """Structured representation of a parsed media torrent/file name."""
    title: str = ''
    season: int | None = None
    episode: int | None = None
    year: int | None = None
    resolution: str | None = None
    codec: str | None = None
    language: str | None = None
    release_group: str | None = None
    is_anime: bool = False
    original_title: str = ''
    quality_score: float = 0.0
