"""
Language detection and search tag utilities for LJS.

Filename language hints are useful for torrent search and release-name parsing,
but local-library scans must prefer actual stream metadata.  The media probe
service owns ffprobe calls and serializes them; this module only provides a
lightweight fallback used by category helpers.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from src.core.categories.media_probe import probe_media_file

# Language detection patterns (case-insensitive).
# Each tuple is (regex pattern, language name).
_LANG_PATTERNS: list[tuple[str, str]] = [
    (r"\bITA\b|iTALiAN|Italiano", "Italian"),
    (r"\bENG\b|English", "English"),
    (r"\bFRE\b|French|Francais", "French"),
    (r"\bGER\b|German|Deutsch", "German"),
    (r"\bSPA\b|Spanish|Espanol", "Spanish"),
    (r"\bJPN\b|Japanese", "Japanese"),
]

# Language to torrent search code mapping.
# Torrent indexers use short codes (ITA, FRE, GER), not full language names.
_LANGUAGE_SEARCH_CODES: dict[str, str] = {
    "italian": "ITA",
    "french": "FRE",
    "german": "GER",
    "spanish": "SPA",
    "japanese": "JPN",
    "korean": "KOR",
    "chinese": "CHI",
    "russian": "RUS",
    "portuguese": "POR",
    "polish": "POL",
    "turkish": "TUR",
    "dutch": "NLD",
    "swedish": "SWE",
    "norwegian": "NOR",
    "danish": "DAN",
    "finnish": "FIN",
    "czech": "CZE",
    "hungarian": "HUN",
    "romanian": "ROM",
    "greek": "GRE",
    "hebrew": "HEB",
    "arabic": "ARA",
    "hindi": "HIN",
    "tamil": "TAM",
    "telugu": "TEL",
    "vietnamese": "VIE",
    "indonesian": "IND",
    "malay": "MAY",
    "thai": "THAI",
}


class LanguageSearchTagger:
    """Maps language names to torrent search tags."""

    @staticmethod
    def search_tag(language: str | None) -> str | None:
        """Return the torrent-tag form of a language, or None if not appendable."""
        if not language:
            return None
        key = language.strip().lower()
        if not key or key == "english":
            return None
        return _LANGUAGE_SEARCH_CODES.get(key, language.strip())

    @staticmethod
    def append_to_query(query: str, language: str | None) -> str:
        """Append the language tag (e.g. 'ITA') to a query if appropriate."""
        tag = LanguageSearchTagger.search_tag(language)
        return f"{query} {tag}" if tag else query


class LanguageDetector:
    """Detects media language from release names and serialized audio metadata."""

    @staticmethod
    def from_name(name: str) -> str | None:
        """Extract a language tag from a release or file name."""
        cleaned = name.replace(".", " ").replace("_", " ")
        for pattern, lang in _LANG_PATTERNS:
            if re.search(pattern, cleaned, re.IGNORECASE):
                return lang
        return None

    async def detect(self, name: str, filepath: Optional[Path] = None, default: str = "English") -> str:
        """Detect the likely language from a name and optionally its audio tracks.

        Actual audio streams win when a file is provided. Filename hints remain a
        fallback for torrent names or for files that cannot be probed.
        """
        if filepath and filepath.exists():
            probe = await probe_media_file(filepath)
            if probe and probe.audio_languages:
                return ", ".join(probe.audio_languages)
        name_lang = self.from_name(name)
        if name_lang:
            return name_lang
        return default
