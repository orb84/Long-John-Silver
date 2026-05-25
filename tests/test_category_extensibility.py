"""Category extensibility regression tests for LJS.

These tests prove that a new MediaCategory registered with CategoryRegistry
flows through generic scanning and torrent candidate normalization without
hardcoding TV or movie classes in the caller.
"""

from pathlib import Path

import pytest
from typing import Any

from src.ai.torrent_selection import TorrentSelectionService
from src.core.categories.base import MediaCategory
from src.core.models import CategoryProperty
from src.core.categories.registry import CategoryRegistry
from src.core.categories.types import ParsedMedia, ScannedEpisode, ScannedItem
from src.core.models import GenericMediaItem, ItemList, SearchResult, Settings
from src.utils.library_scanner import LibraryScanner


class MusicCategory(MediaCategory):
    """Minimal custom category used to prove registry-driven extension."""

    category_id = "music"
    display_name = "Music"
    default_folder = "Music"
    accepted_file_patterns = ["*.flac", "*.mp3"]

    def get_properties(self, settings: Settings) -> list[CategoryProperty]:
        return [
            CategoryProperty(
                name="library_path",
                value_type="string",
                description="Music Library Path",
                default_value=f"/mnt/media/{self.default_folder}",
                value=(settings.category_settings.get(self.category_id, {}) or {}).get("library_path")
            )
        ]

    def set_property_value(self, settings: Settings, name: str, value: Any) -> None:
        if name == "library_path":
            if self.category_id not in settings.category_settings:
                settings.category_settings[self.category_id] = {}
            settings.category_settings[self.category_id]["library_path"] = str(value)

    def parse_name(self, name: str) -> ParsedMedia:
        """Parse a simple artist-album-year filename."""
        stem = Path(name).stem.replace(".", " ")
        title, _, year_text = stem.rpartition(" ")
        year = int(year_text) if year_text.isdigit() else None
        return ParsedMedia(original_title=name, title=title or stem, year=year)

    async def scan(self, root_path: str, existing_keys: set[str] | None = None) -> list[ScannedItem]:
        """Return one fake scanned music item for scanner integration."""
        return [
            ScannedItem(
                name="Artist - Album",
                category_id=self.category_id,
                file_count=1,
                total_size_bytes=123,
                detailed_episodes=[
                    ScannedEpisode(
                        season=0,
                        episode=0,
                        file_path=f"{root_path}/Artist - Album.flac",
                        quality="flac",
                        size_bytes=123,
                    ),
                ],
            ),
        ]


class TestCategoryExtensibility:
    """Regression tests for adding a new media category."""

    @pytest.mark.asyncio
    async def test_library_scanner_preserves_custom_category_items(self) -> None:
        """A registered custom category appears in generic scan output."""
        registry = CategoryRegistry()
        registry.register(MusicCategory())

        result = await LibraryScanner(registry).full_scan(Settings())

        assert result.items[0].name == "Artist - Album"
        assert result.items[0].category_id == "music"
        assert result.total_files == 1

    def test_torrent_selection_uses_registered_custom_parser(self) -> None:
        """Torrent normalization parses custom categories through registry."""
        registry = CategoryRegistry()
        registry.register(MusicCategory())
        service = TorrentSelectionService(category_registry=registry)

        candidates = service.normalize_candidates(
            [
                SearchResult(
                    title="Artist.Album.2024.FLAC",
                    magnet="magnet:?xt=urn:btih:abc",
                    size="500 MB",
                    seeders=10,
                    source="test",
                    quality_score=0.9,
                ),
            ],
            category_id="music",
        )

        assert candidates[0].parsed_title == "Artist Album"

    def test_custom_category_tracked_items_round_trip_generically(self) -> None:
        """A custom category item persists without becoming TV or movie."""
        items = ItemList.model_validate({
            "items": [{"key": "Artist - Album", "item_type": "music"}],
        })

        assert isinstance(items.items[0], GenericMediaItem)
        assert items.items[0].item_type == "music"
