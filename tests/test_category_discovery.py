"""Unit tests for the Category Registry dynamic auto-discovery loader."""

import os
import sys
from pathlib import Path

import pytest

from src.core.categories.registry import CategoryRegistry
from src.core.models import Settings


MOCK_CATEGORY_CODE = """
from typing import Any
from src.core.categories.base import MediaCategory
from src.core.models import CategoryProperty
from src.core.categories.types import ParsedMedia, ScannedItem
from src.core.models import Settings

class MockDiscoveryCategory(MediaCategory):
    category_id = "mock_discovery"
    display_name = "Mock Discovery"
    default_folder = "MockDiscovery"
    accepted_file_patterns = ["*.mock"]

    def get_properties(self, settings: Settings) -> list[CategoryProperty]:
        return [
            CategoryProperty(
                name="library_path",
                value_type="string",
                description="Mock Library Path",
                default_value="./library/MockDiscovery",
                value=(settings.category_settings.get(self.category_id, {}) or {}).get("library_path")
            )
        ]

    def parse_name(self, name: str) -> ParsedMedia:
        return ParsedMedia(original_title=name, title=name.replace(".mock", "").upper())

    async def scan(self, root_path: str, existing_keys: set[str] | None = None) -> list[ScannedItem]:
        return []
"""


@pytest.fixture
def mock_category_file():
    """Fixture to write a temporary category file and remove it after testing."""
    categories_dir = Path(__file__).parent.parent / "src" / "core" / "categories"
    temp_file = categories_dir / "test_mock_cat.py"
    
    # Write mock category module
    temp_file.write_text(MOCK_CATEGORY_CODE)
    
    yield temp_file
    
    # Cleanup file
    if temp_file.exists():
        temp_file.unlink()
        
    # Unload module from sys.modules to prevent pollution
    sys.modules.pop("src.core.categories.test_mock_cat", None)


def test_category_registry_discovery(mock_category_file):
    """Test that CategoryRegistry auto-discovers and dynamically registers new modules."""
    registry = CategoryRegistry()
    registry.register_defaults()
    
    # Assert mock category is loaded
    mock_cat = registry.get("mock_discovery")
    assert mock_cat is not None
    assert mock_cat.display_name == "Mock Discovery"
    assert mock_cat.category_id == "mock_discovery"
    
    # Assert it works for parsing names
    parsed = registry.parse("hello_world.mock", category_id="mock_discovery")
    assert parsed.title == "HELLO_WORLD"
    
    # Test setting and getting dynamic properties
    settings = Settings()
    assert mock_cat.get_property_value("library_path", settings) == "./library/MockDiscovery"
    
    mock_cat.set_property_value(settings, "library_path", "/mnt/custom_mock")
    assert mock_cat.get_property_value("library_path", settings) == "/mnt/custom_mock"
