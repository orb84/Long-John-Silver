
"""Tests for category manifests and LLM profiles."""

from src.core.categories.registry import CategoryRegistry
from src.core.models import Settings


def test_builtin_categories_expose_manifests():
    """Built-in categories should expose UI, action, and LLM manifest data."""
    registry = CategoryRegistry.with_defaults()
    settings = Settings()

    manifests = registry.manifests(settings=settings)
    ids = {manifest.category_id for manifest in manifests}

    assert "tv" in ids
    assert "movie" in ids
    for manifest in manifests:
        assert manifest.display_name
        assert manifest.description
        assert manifest.router_brief is not None
        assert manifest.ui_sections
        assert manifest.actions
        assert isinstance(manifest.tool_names, list)


def test_tv_profile_is_episodic_and_movie_profile_is_standalone():
    """TV and Movie LLM profiles should describe different domains."""
    registry = CategoryRegistry.with_defaults()
    tv = registry.get("tv")
    movie = registry.get("movie")

    assert tv is not None
    assert movie is not None
    assert "episodic" in tv.capabilities
    assert "episode" in tv.llm_profile().domain_vocabulary
    assert "movie" in movie.llm_profile().domain_vocabulary
    assert "year" in movie.llm_profile().identifiers
