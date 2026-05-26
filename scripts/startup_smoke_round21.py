#!/usr/bin/env python3
"""Round 21 startup smoke test for the real tool composition surface.

The compile checks catch syntax issues and import smoke tests catch broad module
availability, but neither proves that the application can compose its active
LLM tool graph.  This script builds the same provider family used by main.py
with lightweight real collaborators where possible and verifies that modern
category-owned tools are active while compatibility shims stay hidden.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from src.ai.tool_catalog import AgentToolCatalog
from src.ai.tools.categories import CategoryToolProvider
from src.ai.tools.downloads import DownloadToolProvider
from src.ai.tools.library import LibraryToolProvider
from src.ai.tools.preferences import PreferencesToolProvider
from src.ai.tools.research import ResearchToolProvider
from src.ai.tools.scheduling import SchedulingToolProvider
from src.ai.tools.storage import StorageToolProvider
from src.ai.tools.web import WebToolProvider
from src.core.categories.registry import CategoryRegistry
from src.core.config import SettingsManager


class ToolGraphStartupSmoke:
    """Build and validate the launch-time agent tool graph."""

    def __init__(self) -> None:
        """Create an isolated settings path so the smoke test is side-effect free."""
        self._tmp = tempfile.TemporaryDirectory(prefix="ljs_round21_smoke_")
        root = Path(self._tmp.name)
        self._settings_manager = SettingsManager(
            yaml_path=str(root / "settings.yaml"),
            category_config_dir=str(root / "categories"),
        )
        self._category_registry = CategoryRegistry.with_defaults()

    def run(self) -> None:
        """Build the registry and assert active/legacy tool invariants."""
        try:
            registry = AgentToolCatalog(self._build_providers()).build_registry()
            names = set(registry.get_tool_names())
            required = {
                "list_downloads",
                "manage_downloads",
                "queue_download",
                "enquire_about_media",
                "metadata_lookup",
                "tv.find_missing_episodes",
                "tv.download_specific_episode",
                "get_storage_status",
            }
            missing = sorted(required - names)
            if missing:
                raise AssertionError(f"Active tool graph is missing required tools: {missing}")
            forbidden = {"show" + "_episodes"}
            leaked = sorted(forbidden & names)
            if leaked:
                raise AssertionError(f"Compatibility-only tools leaked into active graph: {leaked}")
            print(f"round21 startup smoke passed with {len(names)} active tools")
        finally:
            self._tmp.cleanup()

    def _build_providers(self) -> list[object]:
        """Return provider instances matching the main composition root shape."""
        return [
            DownloadToolProvider(),
            LibraryToolProvider(
                settings_manager=self._settings_manager,
                database=object(),
                category_registry=self._category_registry,
            ),
            PreferencesToolProvider(),
            ResearchToolProvider(settings_manager=self._settings_manager),
            SchedulingToolProvider(settings_manager=self._settings_manager),
            WebToolProvider(settings_manager=self._settings_manager),
            CategoryToolProvider(
                category_registry=self._category_registry,
                settings_manager=self._settings_manager,
                database=object(),
            ),
            StorageToolProvider(),
        ]


if __name__ == "__main__":
    try:
        ToolGraphStartupSmoke().run()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
