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
    joined = r"[\s_.\-]+".join(re.escape(part) for part in parts)
    pattern = re.compile(r"(?<![a-z0-9])" + joined + r"(?![a-z0-9])", re.IGNORECASE)
    return bool(pattern.search(normalized_text))


def count_router_matches(text: str, tokens: Iterable[object]) -> int:
    """Count unique router tokens that match ``text`` with boundaries."""
    return sum(1 for token in iter_router_tokens(tokens) if router_token_matches(text, token))
