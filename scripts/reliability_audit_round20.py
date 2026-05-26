#!/usr/bin/env python3
"""Round 20 startup compatibility audit.

This audit prevents regressions where the domain-model refactor keeps compile
checks green but breaks the runtime startup import path through
``src.core.config`` and ``main.py``.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class StartupCompatibilityAudit:
    """Verify startup imports and legacy facade compatibility after refactors."""

    def run(self) -> None:
        """Execute Round 20 invariants and raise on failure."""
        self.require_model_facade_private_helper()
        self.require_startup_imports()
        self.require_library_tool_exports()
        self.require_download_tool_exports()
        self.require_compatibility_modules()
        self.require_behavior_item_name_fallbacks()
        self.require_model_facade_audit()
        print("round20 startup compatibility audit passed")

    def require_model_facade_private_helper(self) -> None:
        """Ensure legacy config imports can still access ``_deserialize_item``."""
        models = importlib.import_module("src.core.models")
        if not hasattr(models, "_deserialize_item"):
            raise AssertionError("src.core.models must re-export _deserialize_item for config compatibility")

    def require_startup_imports(self) -> None:
        """Import launch-critical modules without starting the web server.

        Some optional provider libraries create background workers during import.
        The check therefore runs the heavyweight ``main`` import in a child
        process and exits with ``os._exit(0)`` immediately after validation so
        background threads cannot hang CI.
        """
        config = importlib.import_module("src.core.config")
        if not hasattr(config, "SettingsManager"):
            raise AssertionError("src.core.config should export SettingsManager")
        command = (
            "import os; "
            "import main; "
            "assert hasattr(main, 'main'), 'main.py should expose async main'; "
            "os._exit(0)"
        )
        result = subprocess.run(
            [sys.executable, "-c", command],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(result.stdout + result.stderr)

    def require_library_tool_exports(self) -> None:
        """Ensure category enquiry survived library-tool refactors."""
        library = importlib.import_module("src.ai.tools.library")
        if not hasattr(library, "EnquireAboutMediaTool"):
            raise AssertionError("library tools should export EnquireAboutMediaTool")
        provider = library.LibraryToolProvider()
        names = [tool.name for tool in provider.get_tools()]
        if "enquire_about_media" not in names:
            raise AssertionError("LibraryToolProvider should include enquire_about_media")

    def require_download_tool_exports(self) -> None:
        """Ensure download tools expose only active, supported tool classes."""
        downloads = importlib.import_module("src.ai.tools.downloads")
        retired_symbol = "Show" + "Episodes" + "Tool"
        retired_name = "show" + "_episodes"
        if hasattr(downloads, retired_symbol):
            raise AssertionError(f"download tools must not export retired {retired_symbol}")
        names = [tool.name for tool in downloads.DownloadToolProvider().get_tools()]
        if retired_name in names:
            raise AssertionError(f"DownloadToolProvider must not expose retired {retired_name}")

    def require_compatibility_modules(self) -> None:
        """Ensure historical smoke-test import paths remain available."""
        upgrade = importlib.import_module("src.core.upgrade_detector")
        if not hasattr(upgrade, "UpgradeDetector"):
            raise AssertionError("src.core.upgrade_detector should expose UpgradeDetector")

    def require_behavior_item_name_fallbacks(self) -> None:
        """Ensure category pause/resume behavior records human-readable names."""
        gateway_text = (ROOT / "src/core/actions/gateway.py").read_text(encoding="utf-8")
        for action in ("category_item_pause", "category_item_resume"):
            if f"'{action}'" not in gateway_text or "a.get('name')" not in gateway_text:
                raise AssertionError(f"{action} should fall back to item_name/item_id/name")

    def require_model_facade_audit(self) -> None:
        """Run the project-wide model facade import guard."""
        result = subprocess.run(
            [sys.executable, "scripts/check_model_facade_imports.py"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(result.stdout + result.stderr)


if __name__ == "__main__":
    StartupCompatibilityAudit().run()
