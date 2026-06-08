"""Recovery helpers for DOWNLOAD turns that fail to emit a tool call.

The normal path is LLM-led tool calling.  This module is only a fail-safe for
models/providers that answer with prose even after being explicitly reprompted
to use tools.  The recovery does not pick a torrent and does not queue
anything; it only creates the first generic search_media_torrents call so the
category and candidate-adjudication layers can do the real work.
"""

from __future__ import annotations

import re
from typing import Any


class DownloadToolRecovery:
    """Build a conservative search_media_torrents call after tool-call failure."""

    _LANGUAGE_PATTERNS: tuple[tuple[str, str], ...] = (
        (r"\b(?:italian|italiano|italiana|ita)\b", "Italian"),
        (r"\b(?:english|inglese|eng)\b", "English"),
        (r"\b(?:spanish|spagnolo|espanol|español|spa)\b", "Spanish"),
        (r"\b(?:french|francese|fre)\b", "French"),
        (r"\b(?:german|tedesco|ger)\b", "German"),
    )
    _ORDINAL_SEASONS: dict[str, int] = {
        "first": 1,
        "1st": 1,
        "prima": 1,
        "primo": 1,
        "second": 2,
        "2nd": 2,
        "seconda": 2,
        "secondo": 2,
        "third": 3,
        "3rd": 3,
        "terza": 3,
        "terzo": 3,
        "fourth": 4,
        "4th": 4,
        "quarta": 4,
        "quarto": 4,
        "fifth": 5,
        "5th": 5,
        "quinta": 5,
        "quinto": 5,
    }
    _ACQUIRE_PREFIX_RE = re.compile(
        r"^\s*(?:hi|hey|ciao|ahoy|captain|please|pls|per favore|can\s+you\s+please|could\s+you\s+please|can\s+you|could\s+you|mi\s+scarichi|mi\s+prendi)?\s*"
        r"(?:grab|download|get|fetch|find|search\s+for|look\s+for|queue|add|scarica|scaricami|prendi|cerca|trova|metti)\s+"
        r"(?:me\s+|mi\s+)?",
        re.IGNORECASE,
    )
    _QUALIFIER_RE = re.compile(
        r"\s+(?:in\s+(?:italian|italiano|italiana|english|inglese|spanish|spagnolo|french|francese|german|tedesco)|"
        r"(?:ita|eng|spa)\b|full\s+(?:first|second|third|\d+)(?:\s+season)?|"
        r"(?:first|second|third|fourth|fifth|\d+)(?:st|nd|rd|th)?\s+season|"
        r"season\s+\d+|s\d{1,2}\b|complete\s+season|full\s+season|season\s+pack|pack\b).*$",
        re.IGNORECASE,
    )

    @classmethod
    def build_search_media_torrents_args(
        cls,
        *,
        user_prompt: str | None,
        active_category_id: str | None,
    ) -> dict[str, Any] | None:
        """Return a minimal safe search_media_torrents argument dict.

        This parser is intentionally conservative.  It only runs after the LLM
        has twice failed to use the tool channel on a DOWNLOAD turn.  It
        extracts the obvious title/constraints from the user's own words and
        leaves semantic torrent selection to category search + the LLM candidate
        adjudicator.
        """
        prompt = str(user_prompt or "").strip()
        if not prompt:
            return None
        name = cls._extract_name(prompt)
        if not name:
            return None
        args: dict[str, Any] = {"name": name}
        if active_category_id:
            args["category_id"] = active_category_id
        language = cls._extract_language(prompt)
        if language:
            args["language"] = language
            args["language_is_explicit"] = True
        season = cls._extract_season(prompt)
        if season:
            args["season"] = season
        episode = cls._extract_episode(prompt)
        if episode:
            args["episode"] = episode
        if season and not episode and cls._looks_like_bundle_request(prompt):
            args["search_scope"] = "bundle_preferred"
        return args

    @classmethod
    def _extract_name(cls, prompt: str) -> str:
        text = cls._ACQUIRE_PREFIX_RE.sub("", prompt).strip()
        text = cls._QUALIFIER_RE.sub("", text).strip()
        text = re.sub(r"\s+", " ", text).strip(" \t\n\r.,;:!?()[]{}\"'")
        # Remove leading particles left by awkward phrasing, but never remove
        # inner title words such as the 'of' in 'A Knight of the Seven Kingdoms'.
        text = re.sub(r"^(?:of|about|for)\s+", "", text, flags=re.IGNORECASE).strip()
        # Avoid turning the whole polite request into a fake title if prefix
        # extraction failed completely.
        if len(text.split()) > 12:
            return ""
        return text

    @classmethod
    def _extract_language(cls, prompt: str) -> str | None:
        for pattern, language in cls._LANGUAGE_PATTERNS:
            if re.search(pattern, prompt, re.IGNORECASE):
                return language
        return None

    @classmethod
    def _extract_season(cls, prompt: str) -> int | None:
        if match := re.search(r"\bS(\d{1,2})(?:E\d{1,2})?\b", prompt, re.IGNORECASE):
            return int(match.group(1))
        if match := re.search(r"\bseason\s+(\d{1,2})\b", prompt, re.IGNORECASE):
            return int(match.group(1))
        for word, value in cls._ORDINAL_SEASONS.items():
            if re.search(rf"\b{re.escape(word)}\s+season\b|\bfull\s+{re.escape(word)}\b", prompt, re.IGNORECASE):
                return value
        return None

    @classmethod
    def _extract_episode(cls, prompt: str) -> int | None:
        if match := re.search(r"\bS\d{1,2}E(\d{1,3})\b", prompt, re.IGNORECASE):
            return int(match.group(1))
        if match := re.search(r"\b(?:episode|ep|e)\s*(\d{1,3})\b", prompt, re.IGNORECASE):
            return int(match.group(1))
        return None

    @classmethod
    def _looks_like_bundle_request(cls, prompt: str) -> bool:
        return bool(re.search(r"\b(?:full|complete|season\s+pack|pack|entire|whole|tutta|completa)\b", prompt, re.IGNORECASE))
