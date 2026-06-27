"""Recovery helpers for DOWNLOAD turns that fail to emit a tool call.

The normal path is LLM-led tool calling. This module is only a fail-safe for
models/providers that answer with prose even after being explicitly reprompted
to use tools. The recovery does not pick a torrent and does not queue anything;
it only creates the first generic ``search_media_torrents`` call so category
search and candidate-adjudication layers can do the real work.
"""

from __future__ import annotations

import re
from typing import Any


class DownloadToolRecovery:
    """Build a conservative search_media_torrents call after tool-call failure.

    This fallback must remain category-neutral. It intentionally does not parse
    release languages, season/episode coordinates, packs, editions, or formats
    from free text. Those meanings belong to the LLM plan and category context.
    When the LLM fails to call tools, the safest recovery is one broad literal
    search under the active category; the reviewer still receives the original
    user prompt and category skill before anything can be queued.
    """

    _ACQUIRE_PREFIX_RE = re.compile(
        r"^\s*(?:hi|hey|ciao|ahoy|captain|please|pls|per\s+favore|can\s+you\s+please|could\s+you\s+please|can\s+you|could\s+you|mi\s+scarichi|mi\s+prendi)?\s*"
        r"(?:grab|download|get|fetch|find|search\s+for|look\s+for|queue|add|scarica|scaricami|prendi|cerca|trova|metti)\s+"
        r"(?:me\s+|mi\s+)?",
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

        The output deliberately contains only the literal search text and active
        category. It must not synthesize category-specific constraints from
        English/Italian keywords because that caused hidden drift between the
        prompt skills, category hooks, and generic recovery code.
        """
        prompt = str(user_prompt or "").strip()
        if not prompt:
            return None
        name = cls._extract_search_text(prompt)
        if not name:
            return None
        args: dict[str, Any] = {"name": name}
        if active_category_id:
            args["category_id"] = active_category_id
        return args

    @classmethod
    def _extract_search_text(cls, prompt: str) -> str:
        """Return a conservative literal search string from the current prompt."""
        text = cls._ACQUIRE_PREFIX_RE.sub("", prompt).strip()
        text = re.sub(r"\s+", " ", text).strip(" \t\n\r.,;:!?()[]{}\"'")
        text = re.sub(r"^(?:of|about|for)\s+", "", text, flags=re.IGNORECASE).strip()
        # Avoid turning a long paragraph into a fake media title. The live LLM
        # path should handle complex instructions; recovery is only a narrow
        # trigger to force a first search instead of silently answering.
        if len(text.split()) > 16:
            return ""
        return text
