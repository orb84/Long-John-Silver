"""Unit tests for the Comms Registry dynamic auto-discovery loader."""

import os
import sys
from pathlib import Path
import pytest

from src.web.comms import CommsRegistry, CommsBridge
from src.core.models import Settings

MOCK_BRIDGE_CODE = """
from src.web.comms import CommsBridge
from src.core.models import Settings

class MockDiscoveryBridge(CommsBridge):
    name = "mock_discovery"
    display_name = "Mock Discovery"
    description = "A mock discovery bridge for testing"
    icon = "⚓"
    package_name = "pytest"
    settings_token_field = "mock_token"

    @classmethod
    def get_formatting_instructions(cls) -> str:
        return "MOCK FORMATTING RULES"
"""

@pytest.fixture
def mock_bridge_file():
    """Fixture to write a temporary bridge file and remove it after testing."""
    web_dir = Path(__file__).parent.parent / "src" / "web"
    temp_file = web_dir / "test_mock_bridge.py"
    
    # Write mock bridge module
    temp_file.write_text(MOCK_BRIDGE_CODE)
    
    yield temp_file
    
    # Cleanup file
    if temp_file.exists():
        temp_file.unlink()
        
    # Unload module from sys.modules to prevent pollution
    sys.modules.pop("src.web.test_mock_bridge", None)

def test_comms_registry_discovery(mock_bridge_file):
    """Test that CommsRegistry auto-discovers and dynamically registers new modules."""
    registry = CommsRegistry()
    registry.discover_bridges()
    
    # Assert mock bridge is loaded
    mock_info = registry.get_registered_info("mock_discovery")
    assert mock_info is not None
    assert mock_info["display_name"] == "Mock Discovery"
    assert mock_info["description"] == "A mock discovery bridge for testing"
    assert mock_info["icon"] == "⚓"
    assert mock_info["package_name"] == "pytest"
    assert mock_info["settings_token_field"] == "mock_token"
    
    # Assert formatting instructions work
    factory = mock_info["factory"]
    assert factory.get_formatting_instructions() == "MOCK FORMATTING RULES"
