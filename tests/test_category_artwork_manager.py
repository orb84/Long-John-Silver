"""Tests for category-owned artwork cache path behavior."""

from pathlib import Path

from src.core.categories.artwork import CategoryArtworkManager


def test_poster_source_url_converts_tmdb_path() -> None:
    """TMDB relative poster paths become full image URLs."""
    assert CategoryArtworkManager.poster_source_url("/abc.jpg") == "https://image.tmdb.org/t/p/w500/abc.jpg"


def test_public_url_is_category_scoped(tmp_path: Path) -> None:
    """Cached local poster paths are exposed under /category-data by category."""
    manager = CategoryArtworkManager(base_dir=tmp_path / "categories")
    local = tmp_path / "categories" / "tv" / "metadata" / "artwork" / "item" / "poster.jpg"
    url = manager.public_url_for(local)
    assert url == "/category-data/tv/metadata/artwork/item/poster.jpg"
