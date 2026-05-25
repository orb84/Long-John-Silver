"""Round 73 review tests for canonical library boundary seams."""

from __future__ import annotations

from pathlib import Path

from src.core.categories.movie import MovieCategory
from src.core.categories.tv import TvShowCategory
from src.core.categories.types import ScannedFileObservation, ScannedItem


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_category_scan_dataclass_exposes_neutral_files_alias(tmp_path: Path) -> None:
    """Category scan dataclasses and public scan models should share a file seam."""
    media = tmp_path / "Pluribus.S01E01.1080p.mkv"
    media.write_bytes(b"x")
    scanned = ScannedItem(
        name="Pluribus",
        category_id="tv",
        episodes={1: [1]},
        seasons=1,
        file_count=1,
        total_size_bytes=1,
        detailed_episodes=[ScannedFileObservation(season=1, episode=1, file_path=str(media), quality="1080p", size_bytes=1)],
    )

    assert scanned.files is scanned.detailed_episodes
    units = TvShowCategory().library_units_from_scan(scanned)
    assert len(units) == 1
    assert units[0]["logical_key"] == "S01E01"
    assert units[0]["unit_type"] == "file"


def test_movie_units_accept_category_scan_dataclass(tmp_path: Path) -> None:
    """Movie canonical unit builders must accept the scanner's native dataclass."""
    media = tmp_path / "Arrival.2016.2160p.mkv"
    media.write_bytes(b"x")
    scanned = ScannedItem(
        name="Arrival",
        category_id="movie",
        file_count=1,
        total_size_bytes=1,
        detailed_episodes=[ScannedFileObservation(file_path=str(media), quality="2160p", size_bytes=1)],
        year=2016,
    )

    units = MovieCategory().library_units_from_scan(scanned)
    assert len(units) == 1
    assert units[0]["unit_type"] == "file"
    assert units[0]["resolution"] == "2160p"


def test_search_pipeline_delegates_category_preparation() -> None:
    """Generic search must not branch on concrete categories for search prep."""
    source = (project_root() / "src/core/search_pipeline.py").read_text(encoding="utf-8")
    assert "prepare_search_item" in source
    assert "item.item_type == 'movie'" not in source
    assert 'item.item_type == "movie"' not in source


def test_download_handler_asks_category_for_target_path() -> None:
    """Download completion should pass raw context to the category target hook."""
    source = (project_root() / "src/core/download_handler.py").read_text(encoding="utf-8")
    assert "download_target_for_item" in source
    assert 'category_id = item.category_id or "tv"' not in source
    assert 'category_id = item.category_id or "movie"' not in source


def test_generic_consolidation_uses_field_based_path_planning() -> None:
    """Generic consolidation should not inject structured-unit defaults."""
    base = (project_root() / "src/core/categories/base.py").read_text(encoding="utf-8")
    consolidator_method = base.split("def consolidation_target_for_file", 1)[1].split("def fallback_library_path", 1)[0]
    assert "compute_target_path_from_fields" in consolidator_method
    assert "parsed.season or 1" not in consolidator_method
    assert "parsed.episode or 1" not in consolidator_method
