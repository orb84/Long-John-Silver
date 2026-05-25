"""Round 74 audit tests for category-owned search and cleanup boundaries."""

from __future__ import annotations

from pathlib import Path

from src.core.categories.movie import MovieCategory
from src.core.categories.tv import TvShowCategory
from src.core.categories.types import ScannedFileObservation, ScannedItem


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_search_pipeline_no_longer_interprets_tv_unit_labels() -> None:
    """SearchPipeline must delegate query/validation/duplicate logic to categories."""
    source = (project_root() / "src/core/search_pipeline.py").read_text(encoding="utf-8")
    assert "def _parse_episode_label" not in source
    assert "Season {" not in source
    assert "S{s:" not in source
    assert "target_episode_size_mb" not in source
    assert "55-minute" not in source
    assert "validate_search_result_for_request" in source
    assert "build_alternative_search_queries" in source
    assert "discovery_already_satisfied" in source
    assert "download_coordinates_from_search_result" in source


def test_tv_category_owns_search_label_semantics() -> None:
    """TV-specific query formats and coordinates belong in the TV category."""
    tv = TvShowCategory()

    class Item:
        key = "Pluribus"

    assert tv.build_search_query(Item(), "S01E03", "English") == "Pluribus S01E03"
    assert "1x03" in " ".join(tv.build_alternative_search_queries(Item(), "S01E03", "English"))
    assert tv.download_coordinates_from_search_result(type("R", (), {"title": "Pluribus.S01E03.1080p"})(), Item(), "S01E03") == {
        "season": 1,
        "episode": 3,
    }


def test_content_cleanup_resolves_external_types_through_categories() -> None:
    """Cleanup core should not map Plex types to built-in categories itself."""
    source = (project_root() / "src/core/content_cleanup.py").read_text(encoding="utf-8")
    assert 'return "tv"' not in source
    assert 'return "movie"' not in source
    assert "matches_external_media_type" in source
    assert "library_file_records_from_scan" in source
    assert "file_record_matches_selector" in source


def test_category_file_listing_records_are_category_owned(tmp_path: Path) -> None:
    """TV/Movie categories expose cleanup records with their own selectors."""
    media = tmp_path / "Pluribus.S01E01.1080p.mkv"
    media.write_bytes(b"x")
    scanned_tv = ScannedItem(
        name="Pluribus",
        category_id="tv",
        detailed_episodes=[ScannedFileObservation(season=1, episode=1, file_path=str(media), quality="1080p", size_bytes=1024)],
        file_count=1,
        total_size_bytes=1024,
    )
    tv = TvShowCategory()
    records = tv.library_file_records_from_scan(scanned_tv)
    assert records[0]["season"] == 1
    assert tv.file_record_matches_selector(records[0], season=1, episode=1)
    assert not tv.file_record_matches_selector(records[0], season=1, episode=2)

    movie_file = tmp_path / "Arrival.2016.2160p.mkv"
    movie_file.write_bytes(b"x")
    scanned_movie = ScannedItem(
        name="Arrival",
        category_id="movie",
        detailed_episodes=[ScannedFileObservation(file_path=str(movie_file), quality="2160p", size_bytes=2048)],
        year=2016,
        file_count=1,
        total_size_bytes=2048,
    )
    movie = MovieCategory()
    movie_records = movie.library_file_records_from_scan(scanned_movie)
    assert movie_records[0]["year"] == 2016
    assert movie.file_record_matches_selector(movie_records[0], year=2016)
    assert not movie.file_record_matches_selector(movie_records[0], year=1999)


def test_tv_agent_no_longer_queries_old_episode_unit_type() -> None:
    """TV agent inference must read physical file units, not retired episode rows."""
    source = (project_root() / "src/core/categories/tv_agent.py").read_text(encoding="utf-8")
    assert 'unit_type="episode"' not in source
    assert "list_category_units" in source
