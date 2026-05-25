"""Tests for flat library scanning and robust folder structuring in Movie and TV categories."""

from pathlib import Path
import pytest

from src.core.categories.movie import MovieCategory
from src.core.categories.tv import TvShowCategory


@pytest.mark.asyncio
async def test_movie_category_flat_and_nested_scanning(tmp_path: Path):
    # Create mock movie root
    movie_root = tmp_path / "Movies"
    movie_root.mkdir()
    
    # 1. Flat movie file (e.g. Inception.mkv)
    flat_movie = movie_root / "Inception (2010) 1080p.mkv"
    flat_movie.write_text("dummy video content")
    
    # 2. Folder-based movie (e.g. The Matrix (1999)/The Matrix.mkv)
    matrix_dir = movie_root / "The Matrix (1999)"
    matrix_dir.mkdir()
    matrix_file = matrix_dir / "The Matrix 1080p.mkv"
    matrix_file.write_text("dummy video content")
    
    category = MovieCategory()
    scanned_items = await category.scan(str(movie_root))
    
    names = {item.name for item in scanned_items}
    assert "Inception" in names
    assert "The Matrix" in names
    
    # Check details
    inception_item = next(i for i in scanned_items if "Inception" in i.name)
    assert inception_item.file_count == 1
    assert inception_item.detailed_episodes[0].file_path == str(flat_movie)


@pytest.mark.asyncio
async def test_tv_category_flat_and_nested_scanning(tmp_path: Path):
    tv_root = tmp_path / "TV Shows"
    tv_root.mkdir()
    
    # 1. Flat TV Show directory with flat episodes
    silicon_valley = tv_root / "Silicon Valley"
    silicon_valley.mkdir()
    ep1 = silicon_valley / "Silicon.Valley.S01E01.mkv"
    ep1.write_text("dummy video")
    ep2 = silicon_valley / "Silicon.Valley.S01E02.mkv"
    ep2.write_text("dummy video")
    
    # 2. Nested TV Show directory with Season folders
    masters = tv_root / "Masters of the Air"
    masters.mkdir()
    season1 = masters / "Season 01"
    season1.mkdir()
    masters_ep1 = season1 / "Masters.Of.The.Air.S01E01.mkv"
    masters_ep1.write_text("dummy video")
    
    category = TvShowCategory()
    scanned_items = await category.scan(str(tv_root))
    
    names = {item.name for item in scanned_items}
    assert "Silicon Valley" in names
    assert "Masters of the Air" in names
    
    sv_item = next(i for i in scanned_items if i.name == "Silicon Valley")
    assert sv_item.file_count == 2
    assert 1 in sv_item.episodes
    assert set(sv_item.episodes[1]) == {1, 2}
    
    masters_item = next(i for i in scanned_items if i.name == "Masters of the Air")
    assert masters_item.file_count == 1
    assert set(masters_item.episodes[1]) == {1}
