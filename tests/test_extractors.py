"""
Tests for domain-specific page extractors (Rotten Tomatoes, IMDb, Metacritic).

Verifies that extractors correctly classify rendered pages and extract
structured facts from static HTML fixtures.
"""

import pytest
from src.core.models import BrowserFetchResult


class TestRottenTomatoesExtractor:
    """Tests for the Rotten Tomatoes extractor."""

    def test_can_extract_recognizes_domain(self):
        from src.integrations.rotten_tomatoes import RottenTomatoesExtractor
        e = RottenTomatoesExtractor()
        result = BrowserFetchResult(
            ok=True, url="https://example.com",
            final_url="https://www.rottentomatoes.com/m/some_movie",
            status=200, title="Some Movie - Rotten Tomatoes",
        )
        assert e.can_extract(result)

    def test_can_extract_rejects_other_domains(self):
        from src.integrations.rotten_tomatoes import RottenTomatoesExtractor
        e = RottenTomatoesExtractor()
        result = BrowserFetchResult(
            ok=True, url="https://example.com",
            final_url="https://www.imdb.com/title/tt123",
            status=200, title="Some Movie - IMDb",
        )
        assert not e.can_extract(result)

    def test_extract_fallback_snippet_when_no_scores(self):
        from src.integrations.rotten_tomatoes import RottenTomatoesExtractor
        e = RottenTomatoesExtractor()
        result = BrowserFetchResult(
            ok=True, url="https://x.com",
            final_url="https://www.rottentomatoes.com/broken",
            status=200, title="404 - Not Found",
            text="No review data available on this page.",
            html="<html><body><p>No review data available on this page.</p></body></html>",
        )
        import asyncio
        loop = asyncio.new_event_loop()
        facts = loop.run_until_complete(e.extract(result))
        loop.close()
        assert len(facts.facts) == 1
        assert facts.facts[0].label == "Page Snippet"


class TestIMDbExtractor:
    """Tests for the IMDb extractor."""

    def test_can_extract_recognizes_domain(self):
        from src.integrations.imdb_extractor import IMDbExtractor
        e = IMDbExtractor()
        result = BrowserFetchResult(
            ok=True, url="https://example.com",
            final_url="https://www.imdb.com/title/tt123",
            status=200, title="Some Movie - IMDb",
        )
        assert e.can_extract(result)

    def test_can_extract_rejects_other_domains(self):
        from src.integrations.imdb_extractor import IMDbExtractor
        e = IMDbExtractor()
        result = BrowserFetchResult(
            ok=True, url="https://example.com",
            final_url="https://www.rottentomatoes.com/m/test",
            status=200, title="Test",
        )
        assert not e.can_extract(result)

    def test_extracts_rating_from_text(self):
        from src.integrations.imdb_extractor import IMDbExtractor
        e = IMDbExtractor()
        result = BrowserFetchResult(
            ok=True, url="https://x.com",
            final_url="https://www.imdb.com/title/tt123/",
            status=200, title="Test Movie",
            text="Test Movie 2024  8.4/10  15,000 IMDb ratings  Action  Adventure  2h 10m",
        )
        import asyncio
        loop = asyncio.new_event_loop()
        facts = loop.run_until_complete(e.extract(result))
        loop.close()
        assert facts.facts[0].label == "IMDb Rating"
        assert "8.4/10" in facts.facts[0].value

    def test_extract_fallback_when_no_rating(self):
        from src.integrations.imdb_extractor import IMDbExtractor
        e = IMDbExtractor()
        result = BrowserFetchResult(
            ok=True, url="https://x.com",
            final_url="https://www.imdb.com/title/tt123/",
            status=200, title="Error",
            text="Page not found.",
        )
        import asyncio
        loop = asyncio.new_event_loop()
        facts = loop.run_until_complete(e.extract(result))
        loop.close()
        assert len(facts.facts) == 1
        assert facts.facts[0].label == "Page Snippet"


class TestMetacriticExtractor:
    """Tests for the Metacritic extractor."""

    def test_can_extract_recognizes_domain(self):
        from src.integrations.rotten_tomatoes import MetacriticExtractor
        e = MetacriticExtractor()
        result = BrowserFetchResult(
            ok=True, url="https://example.com",
            final_url="https://www.metacritic.com/movie/some-movie",
            status=200, title="Some Movie - Metacritic",
        )
        assert e.can_extract(result)

    def test_extract_fallback_when_no_scores(self):
        from src.integrations.rotten_tomatoes import MetacriticExtractor
        e = MetacriticExtractor()
        result = BrowserFetchResult(
            ok=True, url="https://x.com",
            final_url="https://www.metacritic.com/broken",
            status=200, title="Error",
            text="Nothing here.",
        )
        import asyncio
        loop = asyncio.new_event_loop()
        facts = loop.run_until_complete(e.extract(result))
        loop.close()
        assert len(facts.facts) == 1
        assert facts.facts[0].label == "Page Snippet"
