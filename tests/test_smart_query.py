"""Tests for category-based search query generation."""

from src.core.categories.tv import TvSearchPatterns, TvShowCategory
from src.core.categories.movie import MovieSearchPatterns


class TestTvSearchQuery:
    """Tests for TV-specific search query patterns."""

    def setup_method(self):
        self.patterns = TvSearchPatterns()

    def test_basic_query_with_progress(self):
        query = self.patterns.build_primary_query("Severance", "English", {"last_season": 1, "last_episode": 4})
        assert "Severance" in query
        assert "S01E05" in query

    def test_query_without_progress(self):
        query = self.patterns.build_primary_query("The Bear", "English", None)
        assert "The Bear" in query

    def test_non_english_query_includes_language(self):
        query = self.patterns.build_primary_query("Dark", "German", None)
        assert "GER" in query

    def test_season_pack_query(self):
        query = self.patterns.build_pack_query("Test Show", "English", season=5)
        assert "S05" in query
        assert "Complete" in query


class TestMovieSearchQuery:
    """Tests for movie-specific search query patterns."""

    def setup_method(self):
        self.patterns = MovieSearchPatterns()

    def test_basic_movie_query(self):
        query = self.patterns.build_primary_query("The Matrix", "English", None)
        assert "The Matrix" in query

    def test_non_english_movie_query(self):
        query = self.patterns.build_primary_query("Parasite", "Korean", None)
        assert "Parasite" in query
        assert "KOR" in query
