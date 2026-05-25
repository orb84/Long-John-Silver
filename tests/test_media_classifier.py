"""
Tests for the MediaClassifier utility in LJS.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from src.utils.media_classifier import MediaClassifier


class TestMediaClassifier:
    """Test suite for the MediaClassifier class."""

    @pytest.mark.asyncio
    async def test_heuristic_classify_tv(self) -> None:
        """Verify keyword heuristics correctly identify TV shows."""
        settings_manager = MagicMock()
        settings_manager.settings.tmdb_api_key = None
        classifier = MediaClassifier(settings_manager)

        assert await classifier.classify("Breaking Bad Season 1") == "tv"
        assert await classifier.classify("Friends S03E04") == "tv"
        assert await classifier.classify("The Office Complete Series") == "tv"

    @pytest.mark.asyncio
    async def test_heuristic_classify_movie(self) -> None:
        """Verify keyword heuristics default to movie for non-tv titles."""
        settings_manager = MagicMock()
        settings_manager.settings.tmdb_api_key = None
        classifier = MediaClassifier(settings_manager)

        assert await classifier.classify("Inception 2010") == "movie"
        assert await classifier.classify("The Matrix") == "movie"

    @pytest.mark.asyncio
    async def test_tmdb_classify_movie(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify TMDB multi-search accurately classifies a movie query."""
        settings_manager = MagicMock()
        settings_manager.settings.tmdb_api_key = "dummy_key"
        classifier = MediaClassifier(settings_manager)

        mock_tmdb_client_class = MagicMock()
        mock_client_instance = AsyncMock()
        mock_client_instance.search.return_value = [{"type": "movie", "title": "Gladiator"}]
        mock_tmdb_client_class.return_value = mock_client_instance

        monkeypatch.setattr("src.integrations.tmdb.TMDBClient", mock_tmdb_client_class)

        result = await classifier.classify("Gladiator")
        assert result == "movie"
        mock_client_instance.search.assert_called_once_with("Gladiator", media_type="multi")
        mock_client_instance.close.assert_called_once()
