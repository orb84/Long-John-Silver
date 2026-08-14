"""Category-router token matching helpers.

Router vocabulary comes from category definitions and profiles.  Matching it as
raw substrings is dangerous: short tokens such as ``ep`` should not match words
like ``please``.  These helpers provide conservative, boundary-aware matching
for deterministic category hints before LLM routing takes over.
"""

from __future__ import annotations

import re
from typing import Iterable


def normalize_router_token(token: object) -> str:
    """Return a lowercase router token with surrounding whitespace removed."""
    return str(token or "").strip().lower()


def iter_router_tokens(tokens: Iterable[object]) -> list[str]:
    """Return de-duplicated, non-empty router tokens preserving order."""
    result: list[str] = []
    seen: set[str] = set()
    for raw in tokens:
        token = normalize_router_token(raw)
        if token and token not in seen:
            seen.add(token)
            result.append(token)
    return result


def router_token_matches(text: str, token: object) -> bool:
    """Return whether ``token`` appears in ``text`` as a meaningful phrase.

    Single-word tokens require alphanumeric boundaries.  Very short tokens are
    allowed only as exact bounded tokens, never as substrings. Multi-word tokens
    allow common release-title separators between words.
    """
    normalized_text = str(text or "").lower()
    normalized_token = normalize_router_token(token)
    if not normalized_text or not normalized_token:
        return False
    parts = [part for part in re.split(r"[\s_.\-]+", normalized_token) if part]
    if not parts:
        return False
    for variant in _router_phrase_variants(parts):
        joined = r"[\s_.\-]+".join(re.escape(part) for part in variant)
        pattern = re.compile(r"(?<![a-z0-9])" + joined + r"(?![a-z0-9])", re.IGNORECASE)
        if pattern.search(normalized_text):
            return True
    return False


def _router_phrase_variants(parts: list[str]) -> list[list[str]]:
    """Return exact and conservative regular-plural variants for a token.

    Category definitions deliberately use compact singular vocabulary such as
    ``episode``, ``movie``, ``album``, and ``document``.  Natural requests very
    often use their plural forms.  Treating those as unrelated words caused a
    resolved DOWNLOAD request to lose its category and fall through to the
    abstract ``media`` search path.

    Only the final alphabetic word is inflected, and short router tokens such
    as ``ep`` or ``tv`` remain exact-only.  This preserves the boundary-safety
    guarantee that prevents ``ep`` from matching the middle of ``please``.
    """
    variants = [list(parts)]
    last = parts[-1]
    if len(last) < 4 or not last.isalpha() or last.endswith("s"):
        return variants

    if last.endswith("y") and len(last) > 1 and last[-2] not in "aeiou":
        plural = f"{last[:-1]}ies"
    elif last.endswith(("x", "z", "ch", "sh")):
        plural = f"{last}es"
    else:
        plural = f"{last}s"

    plural_parts = list(parts)
    plural_parts[-1] = plural
    variants.append(plural_parts)
    return variants


def count_router_matches(text: str, tokens: Iterable[object]) -> int:
    """Count unique router tokens that match ``text`` with boundaries."""
    return sum(1 for token in iter_router_tokens(tokens) if router_token_matches(text, token))
