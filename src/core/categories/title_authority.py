"""Evidence-backed title authority helpers for category-owned search matching.

The torrent/search layer must not rely on ad-hoc singular/plural or fuzzy title
rules as its primary identity check.  Categories can pass provider metadata,
localized names, original titles, and stored aliases through this helper to get
bounded query titles and exact release-title validation against known aliases.
"""

from __future__ import annotations

import re
from typing import Any

from src.core.categories.identity import clean_display_title, clean_release_title, canonical_item_key


class CategoryTitleAuthority:
    """Build and apply provider-backed title aliases for one category item."""

    @classmethod
    def query_titles_for_item(cls, item: Any, *, preferred_language: str | None = None, limit: int = 6) -> list[str]:
        """Return bounded search titles, preferring provider/localized aliases."""
        aliases = cls.aliases_for_item(item, preferred_language=preferred_language, include_user_key=True)
        return aliases[: max(1, int(limit or 1))]

    @classmethod
    def authoritative_aliases_for_item(cls, item: Any, *, preferred_language: str | None = None) -> list[str]:
        """Return aliases that came from metadata/provider evidence.

        The user/library key is intentionally excluded.  Callers use this to
        decide whether exact provider-backed matching is available; if it is not,
        they may fall back to conservative category heuristics.
        """
        return cls._dedupe_titles(
            cls._metadata_title_values(cls._metadata(item), preferred_language=preferred_language)
        )

    @classmethod
    def aliases_for_item(
        cls,
        item: Any,
        *,
        preferred_language: str | None = None,
        include_user_key: bool = True,
    ) -> list[str]:
        """Return provider aliases plus optional user/display names."""
        values: list[Any] = []
        metadata = cls._metadata(item)
        values.extend(cls._metadata_title_values(metadata, preferred_language=preferred_language))
        values.append(getattr(item, "display_name", None))
        if include_user_key:
            values.append(getattr(item, "key", None))
        return cls._dedupe_titles(values)

    @classmethod
    def matches_any_alias(cls, candidate_title: str, aliases: list[str]) -> bool:
        """Return true when a release title contains a known alias phrase."""
        normalized_candidate = cls.normalized_phrase(candidate_title)
        if not normalized_candidate:
            return False
        padded_candidate = f" {normalized_candidate} "
        for alias in aliases or []:
            normalized_alias = cls.normalized_phrase(alias)
            if not normalized_alias:
                continue
            if f" {normalized_alias} " in padded_candidate:
                return True
        return False

    @staticmethod
    def normalized_phrase(value: object) -> str:
        """Return a token-spaced phrase suitable for exact title containment."""
        cleaned = clean_release_title(value, fallback="", media_hint="tv")
        cleaned = canonical_item_key(cleaned)
        return re.sub(r"\s+", " ", cleaned).strip()

    @classmethod
    def _metadata(cls, item: Any) -> dict[str, Any]:
        metadata = getattr(item, "metadata", None)
        return metadata if isinstance(metadata, dict) else {}

    @classmethod
    def _metadata_title_values(cls, metadata: dict[str, Any], *, preferred_language: str | None = None) -> list[Any]:
        values: list[Any] = []
        preferred = cls._language_token(preferred_language)

        # Canonical/provider titles first.
        for key in ("display_name", "title", "name", "original_title", "original_name"):
            values.append(metadata.get(key))

        for key in ("title_aliases", "aliases", "alternative_titles"):
            values.extend(cls._flatten_title_values(metadata.get(key)))

        # Localized titles can be strings or provider dicts carrying language / country.
        localized = metadata.get("localized_titles") or metadata.get("translations") or []
        preferred_rows: list[Any] = []
        other_rows: list[Any] = []
        for row in localized if isinstance(localized, list) else []:
            title_values = cls._flatten_title_values(row)
            if not title_values:
                continue
            lang = ""
            country = ""
            if isinstance(row, dict):
                lang = cls._language_token(row.get("language") or row.get("iso_639_1"))
                country = str(row.get("country") or row.get("iso_3166_1") or "").strip().lower()
            target = preferred_rows if preferred and (lang == preferred or cls._country_matches_language(country, preferred)) else other_rows
            target.extend(title_values)
        values.extend(preferred_rows)
        values.extend(other_rows)

        for nested_key in ("tmdb", "tvmaze"):
            nested = metadata.get(nested_key)
            if isinstance(nested, dict):
                for key in ("display_name", "title", "name", "original_title", "original_name"):
                    values.append(nested.get(key))
                values.extend(cls._flatten_title_values(nested.get("title_aliases")))
                values.extend(cls._flatten_title_values(nested.get("localized_titles")))
        return values

    @classmethod
    def _flatten_title_values(cls, value: Any) -> list[str]:
        out: list[str] = []
        if value is None:
            return out
        if isinstance(value, str):
            out.append(value)
        elif isinstance(value, dict):
            for key in ("title", "name", "display_name", "original_title", "original_name", "english_name"):
                raw = value.get(key)
                if isinstance(raw, str) and raw.strip():
                    out.append(raw)
            data = value.get("data")
            if isinstance(data, dict):
                out.extend(cls._flatten_title_values(data))
        elif isinstance(value, list):
            for row in value:
                out.extend(cls._flatten_title_values(row))
        return out

    @classmethod
    def _dedupe_titles(cls, values: list[Any]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for value in values or []:
            if value is None:
                continue
            title = clean_display_title(str(value), fallback="").strip()
            if not title:
                continue
            key = cls.normalized_phrase(title).casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(title)
        return out

    @staticmethod
    def _language_token(value: object) -> str:
        text = str(value or "").strip().lower()
        aliases = {
            "ita": "italian", "it": "italian", "italiano": "italian", "italian": "italian",
            "eng": "english", "en": "english", "inglese": "english", "english": "english",
        }
        return aliases.get(text, text)

    @staticmethod
    def _country_matches_language(country: str, language: str) -> bool:
        return bool((language == "italian" and country == "it") or (language == "english" and country in {"gb", "us", "ca", "au"}))
