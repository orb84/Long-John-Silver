"""Round 71 regression tests for canonical category library objects."""

from __future__ import annotations

from pathlib import Path

from src.core.categories.movie import MovieCategory
from src.core.categories.tv import TvShowCategory
from src.core.models import ScannedLibraryItem, ScannedMediaFile


def test_tv_category_builds_canonical_file_units_with_episode_coordinates(tmp_path: Path) -> None:
    """TV scan normalization should preserve files while exposing episode coordinates."""
    episode_path = tmp_path / "Pluribus.S01E01.1080p.H265.mkv"
    episode_path.write_bytes(b"x" * 1024)
    sub_path = tmp_path / "Pluribus.S01E01.1080p.H265.srt"
    sub_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nHi\n", encoding="utf-8")
    scanned = ScannedLibraryItem(
        name="Pluribus",
        category_id="tv",
        files=[ScannedMediaFile(season=1, episode=1, file_path=str(episode_path), quality="1080p/h265", size_bytes=1024)],
        episodes={1: [1]},
        seasons=1,
        file_count=1,
        total_size_bytes=1024,
    )
    category = TvShowCategory()
    units = category.library_units_from_scan(scanned)
    assert units[0]["unit_key"].startswith("file:")
    assert units[0]["logical_key"] == "S01E01"
    assert units[0]["unit_type"] == "file"
    assert units[0]["role"] == "episode_payload"
    assert units[0]["size_bytes"] == 1024
    assert units[0]["estimated_bitrate_kbps"] is not None
    assert str(sub_path) in units[0]["subtitle_files"]


def test_tv_canonical_object_keeps_multiple_files_for_one_episode(tmp_path: Path) -> None:
    """Two local releases for the same episode must not overwrite each other."""
    paths = [
        tmp_path / "Pluribus.S01E01.720p.mkv",
        tmp_path / "Pluribus.S01E01.1080p.mkv",
    ]
    for index, path in enumerate(paths, start=1):
        path.write_bytes(b"x" * (1024 * index))
    scanned = ScannedLibraryItem(
        name="Pluribus",
        category_id="tv",
        files=[
            ScannedMediaFile(season=1, episode=1, file_path=str(paths[0]), quality="720p", size_bytes=1024),
            ScannedMediaFile(season=1, episode=1, file_path=str(paths[1]), quality="1080p", size_bytes=2048),
        ],
        episodes={1: [1]},
        seasons=1,
        file_count=2,
        total_size_bytes=3072,
    )
    category = TvShowCategory()
    units = category.library_units_from_scan(scanned)
    assert len({unit["unit_key"] for unit in units}) == 2
    assert {unit["logical_key"] for unit in units} == {"S01E01"}

    class Context:
        category_id = "tv"
        item_id = "Pluribus"
        item = {"display_name": "Pluribus", "item_type": "tv"}
        metadata_rows = []
        settings_item = None

        def __init__(self, units):
            self.units = units

    canonical = category.build_library_object(Context(units))
    assert canonical["computed"]["downloaded_episode_count"] == 1
    assert canonical["computed"]["downloaded_file_count"] == 2
    assert canonical["seasons"][0]["episodes"][0]["file_count"] == 2


def test_movie_category_builds_canonical_file_units(tmp_path: Path) -> None:
    """Movie scan normalization should create file units without core movie logic."""
    movie_path = tmp_path / "Arrival.2016.2160p.x265.mkv"
    movie_path.write_bytes(b"x" * 2048)
    scanned = ScannedLibraryItem(
        name="Arrival",
        category_id="movie",
        files=[ScannedMediaFile(file_path=str(movie_path), quality="2160p", size_bytes=2048)],
        file_count=1,
        total_size_bytes=2048,
        year=2016,
    )
    category = MovieCategory()
    units = category.library_units_from_scan(scanned)
    assert units[0]["unit_type"] == "file"
    assert units[0]["resolution"] == "2160p"
    assert units[0]["codec"] == "x265"
    assert units[0]["estimated_bitrate_kbps"] is not None


def test_core_suggestion_compiler_has_no_category_workflow_import() -> None:
    """The generic compiler must not import concrete category workflows."""
    source = Path("src/core/suggestion_compiler.py").read_text(encoding="utf-8")
    assert "workflows.tv_suggestions" not in source
    assert "create_suggestion_workflow" in source
