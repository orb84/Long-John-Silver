"""
Media Classification Utility for LJS.

Helps to dynamically classify untracked search queries into either 'movie'
or 'tv' categories. Uses TMDB search API and text-based heuristics.
"""

from typing import TYPE_CHECKING
from loguru import logger

if TYPE_CHECKING:
    from src.core.config import SettingsManager


class MediaClassifier:
    """Intelligently classifies a query or search title into 'movie' or 'tv'.

    Utilizes TMDB multi-search with query matching to yield accurate classification.
    """

    def __init__(self, settings_manager: "SettingsManager") -> None:
        """Initialize the media classifier with settings.

        Args:
            settings_manager: Configured settings manager for the application.
        """
        self._settings_manager = settings_manager

    async def classify(self, name: str) -> str:
        """Classify the media name into 'movie' or 'tv'.

        Args:
            name: The title/name of the media.

        Returns:
            "movie" or "tv".
        """
        api_key = self._settings_manager.settings.tmdb_api_key if self._settings_manager else None
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
        """Fallback text-based keyword heuristic classification.

        Args:
            name: The title/name of the media.

        Returns:
            "movie" or "tv".
        """
        lower_name = name.lower()
        # Common TV show keywords/formats
        tv_indicators = [
            "tv show", "series", "season", "episode", "s0", "s1", "s2", "s3",
            "s4", "s5", "s6", "s7", "s8", "s9", "complete series", "boxset",
            "s01", "s02", "s03", "s04", "s05", "s06", "s07", "s08", "s09"
        ]
        if any(indicator in lower_name for indicator in tv_indicators):
            return "tv"

        # Default to movie if no episode indicators are present
        return "movie"
