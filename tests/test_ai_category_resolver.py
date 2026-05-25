
"""Tests for category-aware assistant run context resolution."""

from src.ai.category_resolver import CategoryResolver
from src.core.categories.registry import CategoryRegistry
from src.core.models import Intent, Settings


def test_resolver_detects_tv_language():
    """Episode/season wording should resolve to the TV category."""
    resolver = CategoryResolver(CategoryRegistry.with_defaults(), Settings())
    category = resolver.resolve("Download Severance season 2 episode 4", Intent.DOWNLOAD)
    assert category is not None
    assert category.category_id == "tv"


def test_resolver_detects_movie_language():
    """Movie/film wording should resolve to the Movie category."""
    resolver = CategoryResolver(CategoryRegistry.with_defaults(), Settings())
    category = resolver.resolve("Download the movie Dune 2021", Intent.DOWNLOAD)
    assert category is not None
    assert category.category_id == "movie"
