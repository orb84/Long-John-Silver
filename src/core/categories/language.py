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


class LanguageTokenPolicy:
    """Shared language-token helpers for category/download prompt plumbing.

    This is intentionally small and category-neutral: it normalizes common
    torrent/indexer language aliases and checks bounded title tokens. Category
    code still decides whether language is relevant and whether subtitles,
    audio, translation, or format-language evidence satisfies a request.
    """

    _ALIASES: dict[str, str] = {
        "italian": "italian", "italiano": "italian", "ita": "italian", "it": "italian",
        "english": "english", "inglese": "english", "eng": "english", "en": "english",
        "french": "french", "francais": "french", "français": "french", "fre": "french", "fra": "french", "fr": "french",
        "german": "german", "deutsch": "german", "ger": "german", "deu": "german", "de": "german",
        "spanish": "spanish", "espanol": "spanish", "español": "spanish", "spa": "spanish", "esp": "spanish", "es": "spanish",
        "japanese": "japanese", "jpn": "japanese", "ja": "japanese",
        "korean": "korean", "kor": "korean", "ko": "korean",
        "multi": "multi", "multilanguage": "multi", "multi-language": "multi", "multi_audio": "multi", "multi-audio": "multi", "dual": "multi", "dual-audio": "multi",
    }

    _TITLE_TOKENS: dict[str, tuple[str, ...]] = {
        "italian": ("ita", "italian", "italiano"),
        "english": ("eng", "english", "inglese"),
        "french": ("fre", "fra", "french", "francais", "français"),
        "german": ("ger", "deu", "german", "deutsch"),
        "spanish": ("spa", "esp", "spanish", "espanol", "español"),
        "japanese": ("jpn", "japanese"),
        "korean": ("kor", "korean"),
        "multi": ("multi", "multilanguage", "multi-language", "dual", "dual-audio"),
    }

    @classmethod
    def canonical_token(cls, value: object) -> str:
        """Return a compact canonical token for language/status comparisons."""
        token = str(value or "").strip().lower().replace("_", "-")
        return cls._ALIASES.get(token, token)

    @classmethod
    def canonical_tokens(cls, values: object) -> set[str]:
        """Normalize a scalar/list language value into comparable tokens."""
        if values is None:
            return set()
        raw = values if isinstance(values, (list, tuple, set)) else [values]
        return {cls.canonical_token(value) for value in raw if str(value or "").strip()}

    @classmethod
    def title_has_language_token(cls, title: str, language: object) -> bool:
        """Return true when a bounded release-title token names the language."""
        canonical = cls.canonical_token(language)
        if not canonical:
            return False
        terms = cls._TITLE_TOKENS.get(canonical, (canonical,))
        escaped = "|".join(re.escape(term.lower()) for term in terms if term)
        if not escaped:
            return False
        return bool(re.search(rf"(?:^|[\s._\-\[\]()])(?:{escaped})(?:$|[\s._\-\[\]()])", str(title or "").lower(), re.IGNORECASE))

    @classmethod
    def title_has_multi_language_signal(cls, title: str) -> bool:
        """Return true when the title advertises a multi/dual-language release."""
        return cls.title_has_language_token(title, "multi")


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
