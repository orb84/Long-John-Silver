"""Generic repair helpers for LLM-provided media titles.

The LLM sometimes optimizes a title for a search query and drops tiny title
words (for example "A Knight the Seven Kingdoms" instead of the literal user
phrase "A Knight of the Seven Kingdoms").  Search tooling should preserve the
user's literal title when it can recover it generically from the current prompt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class _Token:
    text: str
    norm: str
    start: int
    end: int


class MediaTitleRepair:
    """Recover literal user title spans from lossy LLM tool arguments."""

    _MAX_EXTRA_TOKENS = 5
    _MIN_QUERY_TOKENS = 2

    @classmethod
    def recover_literal_title(cls, llm_title: str, user_prompt: str | None) -> str:
        """Return a prompt-preserved title when the LLM dropped inner words.

        The method is intentionally generic: it does not know TV, books, movies,
        or any particular title.  It finds the smallest contiguous span in the
        current user prompt whose tokens contain the LLM title tokens in order.
        That preserves title stopwords inside the span while avoiding command
        words before/after it.
        """
        title = str(llm_title or "").strip()
        prompt = str(user_prompt or "").strip()
        if not title or not prompt:
            return title
        for variant in cls._candidate_title_variants(title):
            recovered = cls._recover_from_tokens(variant, prompt, significant_only=False)
            if recovered:
                if cls._compact(recovered) == cls._compact(title):
                    return title
                return recovered
        for variant in cls._candidate_title_variants(title):
            recovered = cls._recover_from_tokens(variant, prompt, significant_only=True)
            if recovered:
                if cls._compact(recovered) == cls._compact(title):
                    return title
                return recovered
        return title

    _TITLE_STOPWORDS = {"a", "an", "the", "of", "and", "or"}

    @classmethod
    def _candidate_title_variants(cls, title: str) -> list[str]:
        """Return progressively looser title candidates for prompt-span recovery.

        LLMs sometimes pass a search-shaped title such as
        ``A Knight the Seven Kingdoms Season 1`` while the user's prompt says
        ``A Knight of the Seven Kingdoms ... full first season``.  The actual
        media title should still be recoverable without knowing TV semantics.
        """
        base = str(title or "").strip()
        variants: list[str] = []
        for candidate in [base, re.sub(r"\b(?:season|series|full|complete|pack)\b\s*(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)?\s*$", "", base, flags=re.IGNORECASE), re.sub(r"\bS\d{1,2}(?:E\d{1,2})?(?:[-_. ]*E?\d{1,2})?\b.*$", "", base, flags=re.IGNORECASE)]:
            cleaned = str(candidate or "").strip(" \t\n\r.,;:!?()[]{}\"'")
            if cleaned and cleaned not in variants:
                variants.append(cleaned)
        return variants

    @classmethod
    def _recover_from_tokens(cls, title: str, prompt: str, *, significant_only: bool = False) -> str | None:
        query_tokens = cls._normalized_words(title)
        if significant_only:
            query_tokens = [token for token in query_tokens if token not in cls._TITLE_STOPWORDS]
        if len(query_tokens) < cls._MIN_QUERY_TOKENS:
            return None
        prompt_tokens = cls._prompt_tokens(prompt)
        if len(prompt_tokens) < len(query_tokens):
            return None
        best: tuple[int, int] | None = None
        best_width = 10**9
        for start_index, token in enumerate(prompt_tokens):
            token_norm = token.norm
            if significant_only and token_norm in cls._TITLE_STOPWORDS:
                continue
            if token_norm != query_tokens[0]:
                continue
            query_index = 1
            end_index = start_index
            for cursor in range(start_index + 1, len(prompt_tokens)):
                if query_index >= len(query_tokens):
                    break
                cursor_norm = prompt_tokens[cursor].norm
                if significant_only and cursor_norm in cls._TITLE_STOPWORDS:
                    continue
                if cursor_norm == query_tokens[query_index]:
                    query_index += 1
                    end_index = cursor
            if query_index < len(query_tokens):
                continue
            width = end_index - start_index + 1
            if width < len(query_tokens):
                continue
            if width > len(query_tokens) + cls._MAX_EXTRA_TOKENS + (3 if significant_only else 0):
                continue
            if width < best_width:
                best_width = width
                best = (prompt_tokens[start_index].start, prompt_tokens[end_index].end)
        if best is None:
            return None
        recovered = prompt[best[0]:best[1]].strip(" \t\n\r.,;:!?()[]{}\"'")
        return recovered or None

    @classmethod
    def _prompt_tokens(cls, text: str) -> list[_Token]:
        tokens: list[_Token] = []
        for match in re.finditer(r"[A-Za-z0-9]+", text):
            raw = match.group(0)
            norm = cls._normalize_word(raw)
            if norm:
                tokens.append(_Token(raw, norm, match.start(), match.end()))
        return tokens

    @classmethod
    def _normalized_words(cls, text: str) -> list[str]:
        return [cls._normalize_word(part) for part in re.findall(r"[A-Za-z0-9]+", text) if cls._normalize_word(part)]

    @staticmethod
    def _normalize_word(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())

    @classmethod
    def _compact(cls, value: str) -> str:
        return " ".join(cls._normalized_words(value))
