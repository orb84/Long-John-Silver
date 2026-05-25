"""Tests for category-first persistence contracts.

These tests document the intended database shape for user-defined categories:
category-specific fields live in JSON payloads and optional property-index rows,
not in schema columns named after TV/movie concepts.
"""

from pathlib import Path

from src.core.models import CategoryItem, DownloadItem


def test_category_item_accepts_arbitrary_properties() -> None:
    """Custom category items can carry unique properties without model changes."""
    item = CategoryItem(
        key="beethoven-9",
        display_name="Beethoven: Symphony No. 9",
        properties={
            "composer": "Ludwig van Beethoven",
            "movement_count": 4,
            "preferred_conductor": "Karajan",
        },
    )

    data = item.model_dump()

    assert data["properties"]["composer"] == "Ludwig van Beethoven"
    assert data["properties"]["movement_count"] == 4
    assert data["item_type"] == "base"


def test_download_item_uses_category_neutral_name_with_tv_alias() -> None:
    """Downloads have a generic item_name while existing TV callers still parse."""
    download = DownloadItem(id="d1", item_name="Example Item", category_id="music", magnet="magnet:?xt=urn:btih:x")
    tv_style = DownloadItem(id="d2", item_name="Example Show", category_id="tv", magnet="magnet:?xt=urn:btih:y")

    assert download.item_name == "Example Item"
    assert download.item_id == "Example Item"
    assert tv_style.item_name == "Example Show"
    assert tv_style.item_name == "Example Show"


def test_fresh_schema_has_no_legacy_media_tables() -> None:
    """Fresh installs rely on category tables instead of TV/movie-specific tables."""
    schema_text = Path("src/core/database.py").read_text(encoding="utf-8")

    assert "category_items" in schema_text
    assert "category_item_units" in schema_text
    assert "category_item_metadata" in schema_text
    assert "category_property_index" in schema_text
    assert "CREATE TABLE IF NOT EXISTS episodes" not in schema_text
    assert "CREATE TABLE IF NOT EXISTS show_metadata" not in schema_text
    assert "CREATE TABLE IF NOT EXISTS movie_metadata" not in schema_text
    assert "item_name TEXT" not in schema_text
