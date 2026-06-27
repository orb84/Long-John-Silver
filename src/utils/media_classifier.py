"""
Media Classification Utility for LJS.

Legacy helper for narrow TMDB-backed movie/TV classification.

New category routing should prefer ``CategoryRegistry.resolve_from_text`` and
``CategoryRegistry.classify`` so custom categories participate without generic
movie/TV defaults. This class remains for compatibility callers that explicitly
want a TMDB movie/TV hint.
"""

from typing import TYPE_CHECKING
from loguru import logger

if TYPE_CHECKING:
    from src.core.config import SettingsManager


class MediaClassifier:
    """Return a narrow movie/TV hint when TMDB or bounded markers support it."""

    def __init__(self, settings_manager: "SettingsManager") -> None:
        """Initialize the media classifier with settings.

        Args:
            settings_manager: Configured settings manager for the application.
        """
        self._settings_manager = settings_manager

    async def classify(self, name: str) -> str:
        """Classify the media name into ``movie``, ``tv``, or neutral ``media``."""
        api_key = self._settings_manager.settings.first_category_service_value(["tv", "movie", "media"], "tmdb", "api_key") if self._settings_manager else None
        if not api_key:
            logger.debug(f"No TMDB API key configured; falling back to keyword heuristics for '{name}'")
            return self._heuristic_classify(name)

        try:
            from src.integrations.tmdb import TMDBClient
            client = TMDBClient(api_key)
            results = await client.search(name, media_type="multi")
            await client.close()

            if not results:
                logger.debug(f"No TMDB search results found for '{name}'; using heuristics")
                return self._heuristic_classify(name)

            # Inspect the top matching result type
            top_result = results[0]
            media_type = top_result.get("type")
            if media_type in ("movie", "tv"):
                logger.info(f"Classified '{name}' as '{media_type}' via TMDB multi-search")
                return media_type

        except Exception as e:
            logger.warning(f"Error classifying '{name}' via TMDB: {e}; using heuristics")

        return self._heuristic_classify(name)

    def _heuristic_classify(self, name: str) -> str:
        """Fallback to TV only for bounded episodic markers, otherwise neutral."""
        import re

        lower_name = name.lower()
        tv_patterns = (
            r"\btv\s+show\b",
            r"\bcomplete\s+series\b",
            r"\bbox\s*set\b",
            r"\bseason\s+\d{1,2}\b",
            r"\bepisode\s+\d{1,3}\b",
            r"\bs\d{1,2}(?:e\d{1,3})?\b",
            r"\b\d{1,2}x\d{1,3}\b",
        )
        if any(re.search(pattern, lower_name) for pattern in tv_patterns):
            return "tv"
        return "media"
