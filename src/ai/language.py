"""Small user-language helpers for prompt and bridge behavior.

The assistant must answer in the language of the current user message.  Media
items can have configured download languages, but those are search constraints,
not conversational language.  This module gives the system prompt a conservative
hint without turning language detection into routing logic.
"""

from __future__ import annotations

import re


_LANGUAGE_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Italian",
        (
            " il ", " lo ", " la ", " gli ", " le ", " una ", " un ",
            " grazie", "ciao", "puntata", "puntate", "stagione", "episodio",
            "andare in onda", "uscirà", "uscita", "scarica", "mancano",
            "quanti", "quando", "per favore", "ancora",
        ),
    ),
    (
        "English",
        (
            " the ", " a ", " an ", " thanks", "thank you", "episode",
            "episodes", "season", "aired", "air", "release", "released",
            "please", "can you", "how many", "when", "still need", "grab me",
        ),
    ),
    (
        "Spanish",
        (
            " el ", " la ", " los ", " las ", " gracias", "episodio",
            "temporada", "emitir", "saldrá", "cuánt", "cuando", "por favor",
        ),
    ),
    (
        "French",
        (
            " le ", " la ", " les ", " merci", "épisode", "saison",
            "diffus", "sortira", "combien", "quand", "s'il te plaît",
        ),
    ),
)


def detect_user_language_label(text: str) -> str | None:
    """Return a conservative language label for the current user message.

    The function intentionally returns ``None`` when confidence is weak.  The
    LLM still receives the global rule to mirror the user's current language;
    this hint just makes the rule harder to miss when item/download language
    metadata is also present in the prompt.
    """
    raw = f" {(text or '').strip().casefold()} "
    if not raw.strip():
        return None

    # Strong short-message catches first, before overlapping stopwords matter.
    if re.fullmatch(r"\s*(thanks|thank you|thx|ty)[!.\s]*", raw):
        return "English"
    if re.fullmatch(r"\s*(grazie|grazie mille)[!.\s]*", raw):
        return "Italian"

    scores: dict[str, int] = {}
    for language, markers in _LANGUAGE_MARKERS:
        score = 0
        for marker in markers:
            if marker in raw:
                score += 2 if " " in marker.strip() else 1
        if score:
            scores[language] = score

    if not scores:
        return None
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    if len(ranked) > 1 and ranked[0][1] <= ranked[1][1]:
        return None
    return ranked[0][0]
