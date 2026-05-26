"""Category-boundary signature detection derived from category definitions.

This module answers one question for generic search/result validation: does a
candidate title strongly advertise another category's release/file vocabulary?
The terms are loaded from category definitions (``formats.release_terms`` and
``formats.accepted_file_patterns``), so a category like Music does not have to
know that 1080p or BluRay are movie/TV concepts. Those terms remain owned by
media/movie/TV definitions and this generic boundary layer merely compares
signatures.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re
from typing import Any

from loguru import logger

from src.core.category_config import CategoryConfigStore


@dataclass(frozen=True)
class BoundaryMatch:
    """A category-signature match for a release/candidate title."""

    category_id: str
    score: int
    matched_terms: tuple[str, ...]


def _normalize_term(value: object) -> str:
    """Return a compact lowercase term suitable for title matching."""
    term = str(value or "").strip().lower()
    if not term:
        return ""
    if term.startswith("*."):
        term = term[2:]
    elif term.startswith("."):
        term = term[1:]
    term = term.replace("_", " ").strip()
    return term


def _terms_from_definition(definition: dict[str, Any]) -> set[str]:
    """Return release/file signature terms declared by one definition."""
    formats = definition.get("formats") if isinstance(definition.get("formats"), dict) else {}
    raw_terms: list[Any] = []
    for key in ("release_terms", "accepted_file_patterns", "lossless_codecs", "lossy_codecs", "edition_terms"):
        value = formats.get(key)
        if isinstance(value, list):
            raw_terms.extend(value)
        elif value:
            raw_terms.append(value)

    terms: set[str] = set()
    for raw in raw_terms:
        term = _normalize_term(raw)
        if not term:
            continue
        # Single generic words like "album" or "book" are too broad for a hard
        # boundary signal. Extension/acronym/quality tokens and multi-word terms
        # are useful because they normally encode release format conventions.
        if len(term) < 3:
            continue
        if " " not in term and term.isalpha() and len(term) < 4:
            continue
        terms.add(term)
    return terms


def _term_pattern(term: str) -> re.Pattern[str]:
    """Build a release-title regex for one normalized term."""
    parts = [re.escape(part) for part in re.split(r"[\s\-_.]+", term) if part]
    if not parts:
        return re.compile(r"a^", re.IGNORECASE)
    joined = r"[\s\-_.]*".join(parts)
    return re.compile(r"(?<![a-z0-9])" + joined + r"(?![a-z0-9])", re.IGNORECASE)


class CategoryBoundarySignatureIndex:
    """Detect cross-category release signatures without hard-coding them.

    The index is intentionally conservative: it only reports a foreign boundary
    when another concrete category has at least ``min_score`` declared terms in
    the candidate and beats the active category's own signature score.
    """

    def __init__(self, definitions: dict[str, dict[str, Any]]) -> None:
        self._terms: dict[str, tuple[str, ...]] = {}
        self._patterns: dict[str, tuple[tuple[str, re.Pattern[str]], ...]] = {}
        for category_id, definition in sorted((definitions or {}).items()):
            if definition.get("abstract"):
                continue
            terms = tuple(sorted(_terms_from_definition(definition), key=lambda item: (len(item), item)))
            if not terms:
                continue
            self._terms[category_id] = terms
            self._patterns[category_id] = tuple((term, _term_pattern(term)) for term in terms)

    @classmethod
    def from_default_store(cls) -> "CategoryBoundarySignatureIndex":
        """Build an index from the tracked category definitions in this checkout."""
        try:
            definitions = CategoryConfigStore().load_definitions_only()
        except Exception as exc:  # pragma: no cover - defensive startup fallback
            logger.warning("Failed to load category boundary signatures: {}", exc)
            definitions = {}
        return cls(definitions)

    def score(self, category_id: str, title: str) -> BoundaryMatch:
        """Return the signature score for one category against a title."""
        matches: list[str] = []
        text = str(title or "")
        for term, pattern in self._patterns.get(category_id, ()):
            if pattern.search(text):
                matches.append(term)
        return BoundaryMatch(category_id=category_id, score=len(matches), matched_terms=tuple(matches))

    def strongest_foreign_match(
        self,
        *,
        active_category_id: str,
        title: str,
        min_score: int = 2,
        min_margin: int = 1,
    ) -> BoundaryMatch | None:
        """Return a strong foreign signature match, if the title has one.

        Args:
            active_category_id: Category currently handling the request.
            title: Candidate torrent/release title.
            min_score: Minimum number of foreign signature terms required.
            min_margin: Required score lead over the active category's own terms.
        """
        active_score = self.score(active_category_id, title).score
        best: BoundaryMatch | None = None
        for category_id in self._patterns:
            if category_id == active_category_id:
                continue
            candidate = self.score(category_id, title)
            if candidate.score < min_score:
                continue
            if candidate.score < active_score + min_margin:
                continue
            if best is None or candidate.score > best.score:
                best = candidate
        return best


@lru_cache(maxsize=1)
def default_boundary_signature_index() -> CategoryBoundarySignatureIndex:
    """Return the cached signature index for the default category definitions."""
    return CategoryBoundarySignatureIndex.from_default_store()
