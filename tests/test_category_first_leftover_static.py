"""Static regression tests for category-first leftover cleanup."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GENERIC_FILES = [
    path for path in (ROOT / "src").rglob("*.py")
    if not str(path.relative_to(ROOT)).startswith(("src/core/categories/", "src/integrations/"))
]


def test_retired_tv_movie_metadata_models_are_not_used_in_generic_layers() -> None:
    """Old ShowMetadata/MovieMetadata names must not leak into generic code."""
    offenders = []
    for path in GENERIC_FILES:
        text = path.read_text(errors="ignore")
        if "ShowMetadata" in text or "MovieMetadata" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_category_metadata_enricher_lives_under_category_boundary() -> None:
    """The TMDB enricher belongs to category metadata, not generic core."""
    assert not (ROOT / "src/core/metadata_enricher.py").exists()
    assert (ROOT / "src/core/categories/metadata/enricher.py").exists()


def test_taste_profiler_does_not_branch_on_built_in_categories() -> None:
    """TasteProfiler must ask category hooks instead of choosing TV/movie enrichment itself."""
    text = (ROOT / "src/core/taste_profiler.py").read_text(errors="ignore")
    forbidden = [
        "enrich_feature",
        "enrich_series",
        "tmdb_movie",
        "tmdb_tv",
        "tmdb_feature",
        "tmdb_series",
        "category_id == 'movie'",
        'category_id == "movie"',
        "category_id == 'tv'",
        'category_id == "tv"',
    ]
    offenders = [symbol for symbol in forbidden if symbol in text]
    assert offenders == []


def test_built_in_categories_own_taste_metadata_hooks() -> None:
    """Built-in media categories should expose their own taste metadata enrichers."""
    movie_text = (ROOT / "src/core/categories/movie.py").read_text(errors="ignore")
    tv_text = (ROOT / "src/core/categories/tv.py").read_text(errors="ignore")
    assert "async def enrich_taste_metadata" in movie_text
    assert "async def enrich_taste_metadata" in tv_text
