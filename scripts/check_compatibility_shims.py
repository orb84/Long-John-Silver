#!/usr/bin/env python3
"""Audit that retired legacy tools stay removed from active code paths.

Round 22 removes the old TV-specific episode-status alias completely.  The only
supported local media status path is the generic ``enquire_about_media`` tool,
which delegates to category-owned enquiry services.  This guard intentionally
keeps the retired symbol names in one place so accidental reintroduction is
caught early during development and packaging.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ai.tool_registry import ToolRegistry
from src.ai.tools.downloads import DownloadToolProvider
from src.ai.tools.library import LibraryToolProvider

RETIRED_SYMBOL = "Show" + "Episodes" + "Tool"
RETIRED_TOOL_NAME = "show" + "_episodes"


class CompatibilityShimAudit:
    """Verify retired aliases are gone and the generic replacement is active."""

    def run(self) -> None:
        """Run every compatibility invariant and raise on failure."""
        self.require_retired_download_tool_absent()
        self.require_generic_media_enquiry_active()
        self.require_registry_rejects_retired_tool_name()
        print("compatibility shim audit passed")

    def require_retired_download_tool_absent(self) -> None:
        """Ensure the retired TV-specific tool cannot be imported or provided."""
        downloads = importlib.import_module("src.ai.tools.downloads")
        if hasattr(downloads, RETIRED_SYMBOL):
            raise AssertionError(f"{RETIRED_SYMBOL} must not be exported by src.ai.tools.downloads")
        provider_names = [tool.name for tool in DownloadToolProvider().get_tools()]
        if RETIRED_TOOL_NAME in provider_names:
            raise AssertionError(f"DownloadToolProvider must not expose {RETIRED_TOOL_NAME}")

    def require_generic_media_enquiry_active(self) -> None:
        """Ensure the replacement media enquiry tool is the supported public path."""
        library = importlib.import_module("src.ai.tools.library")
        if not hasattr(library, "EnquireAboutMediaTool"):
            raise AssertionError("library tools should export EnquireAboutMediaTool")
        provider_names = [tool.name for tool in LibraryToolProvider().get_tools()]
        if "enquire_about_media" not in provider_names:
            raise AssertionError("LibraryToolProvider must expose enquire_about_media")

    def require_registry_rejects_retired_tool_name(self) -> None:
        """Ensure direct registration of a retired tool-shaped object is rejected."""

        class RetiredTool:
            """Small local stand-in for the removed tool name."""

            name = RETIRED_TOOL_NAME
            compatibility_only = True

        registry = ToolRegistry()
        registry.register_tool(RetiredTool())
        if RETIRED_TOOL_NAME in registry.get_tool_names():
            raise AssertionError(f"ToolRegistry must not expose {RETIRED_TOOL_NAME}")


if __name__ == "__main__":
    try:
        CompatibilityShimAudit().run()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
