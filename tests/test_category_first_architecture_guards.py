"""Architecture guards for the category-first implementation.

These tests intentionally check structural contracts rather than one runtime
path, so old movie/TV/show globals do not silently reappear.
"""

from pathlib import Path
from typing import Any

from scripts.check_category_architecture import CategoryArchitectureGuard

import pytest

from src.ai.tool_policy import AgentToolPolicy
from src.core.categories.base import MediaCategory
from src.core.categories.registry import CategoryRegistry
from src.core.categories.types import ParsedMedia, ScannedItem
from src.core.models import (
    CategoryProperty,
    CategoryWorkflowDeclaration,
    Intent,
    Settings,
)


LEGACY_GLOBAL_SYMBOLS = {
    "get_tmdb_details",
    "get_tmdb_season",
    "get_tvmaze_show",
    "get_rotten_tomatoes_and_reviews",
    "add_show_to_watch",
    "remove_show_from_watch",
    "delete_episode",
    "delete_movie",
    "/api/shows",
    "ShowsRouter",
    "ShowActionHandler",
    "show_tracking",
    "search_web",
    "sync_tracked_shows",
    "tracked_shows",
    "paused_shows",
    "show_progress",
    "shows_data",
    "total_shows",
}


class MusicCategory(MediaCategory):
    """Test-only custom category proving extensibility without app changes."""

    category_id = "music"
    display_name = "Music"
    default_folder = "Music"
    icon = "music"
    capabilities = ["metadata", "downloadable", "tracks"]
    supported_operations = ["search", "download", "scan"]

    def get_properties(self, settings: Settings) -> list[CategoryProperty]:
        """Return category-specific settings for the test category."""
        return [
            CategoryProperty(
                name="library_path",
                value_type="string",
                description="Music library path.",
                default_value="",
            )
        ]

    def declare_workflows(self) -> list[CategoryWorkflowDeclaration]:
        """Expose a custom workflow that should appear through policy."""
        return [
            CategoryWorkflowDeclaration(
                name="download_album",
                description="Download a music album.",
                parameters={"type": "object", "properties": {"artist": {"type": "string"}}},
                intent=Intent.DOWNLOAD,
                risk_level="write",
                requires_confirmation=False,
                tool_name="music.download_album",
            )
        ]

    def parse_name(self, name: str) -> ParsedMedia:
        """Parse music names generically for the architecture guard."""
        return ParsedMedia(original_title=name, title=name)

    async def scan(self, root_path: str, existing_keys: set[str] | None = None) -> list[ScannedItem]:
        """Return no files; scanning is not under test here."""
        return []


def test_legacy_global_symbols_do_not_reappear_in_source() -> None:
    """Old global tool, route, and persistence symbols stay out as real tokens."""
    root = Path(__file__).resolve().parents[1]
    offenders = CategoryArchitectureGuard(root).scan()
    scoped = {symbol: paths for symbol, paths in offenders.items() if symbol in LEGACY_GLOBAL_SYMBOLS}
    assert scoped == {}


def test_custom_category_manifest_and_tool_policy_need_no_app_special_case() -> None:
    """A new category can register workflows and be exposed through policy only."""
    registry = CategoryRegistry()
    music = MusicCategory()
    registry.register(music)

    manifest = music.manifest(settings=Settings())
    policy_tools = AgentToolPolicy().allowed_tool_names(Intent.DOWNLOAD, category=music)

    assert registry.get("music") is music
    assert manifest.category_id == "music"
    assert manifest.workflows[0].tool_name == "music.download_album"
    assert "music.download_album" in policy_tools
    assert "queue_download" in policy_tools
    assert "get_tmdb_details" not in policy_tools
