#!/usr/bin/env python3
"""Round 22 retired-tool removal audit.

The previous architecture cleanup still kept a deprecated TV-specific status
alias as an importable class.  Round 22 removes that alias outright.  This audit
makes the removal explicit while preserving the replacement path through generic
media enquiry.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RETIRED_SYMBOL = "Show" + "Episodes" + "Tool"
RETIRED_TOOL_NAME = "show" + "_episodes"


class Round22RetiredToolRemovalAudit:
    """Verify retired TV-specific status aliases are absent everywhere active."""

    def run(self) -> None:
        """Run every Round 22 invariant and raise on failure."""
        self.require_downloads_do_not_export_retired_tool()
        self.require_active_tool_graph_excludes_retired_tool()
        self.require_prompts_do_not_reference_retired_tool()
        self.run_script("scripts/check_compatibility_shims.py")
        print("round22 retired-tool removal audit passed")

    def require_downloads_do_not_export_retired_tool(self) -> None:
        """Ensure the download module no longer exposes the retired class."""
        downloads = importlib.import_module("src.ai.tools.downloads")
        if hasattr(downloads, RETIRED_SYMBOL):
            raise AssertionError(f"src.ai.tools.downloads must not export {RETIRED_SYMBOL}")

    def require_active_tool_graph_excludes_retired_tool(self) -> None:
        """Ensure provider and startup smoke checks do not expose the retired name."""
        downloads = importlib.import_module("src.ai.tools.downloads")
        names = [tool.name for tool in downloads.DownloadToolProvider().get_tools()]
        if RETIRED_TOOL_NAME in names:
            raise AssertionError(f"DownloadToolProvider must not expose {RETIRED_TOOL_NAME}")
        self.run_script("scripts/startup_smoke_round21.py")

    def require_prompts_do_not_reference_retired_tool(self) -> None:
        """Ensure prompt and planner guidance use only the generic replacement path."""
        for rel_path in ("src/ai/prompt_builder.py", "src/ai/reasoning.py", "src/ai/tools/library.py"):
            text = (ROOT / rel_path).read_text(encoding="utf-8")
            if RETIRED_TOOL_NAME in text or RETIRED_SYMBOL in text:
                raise AssertionError(f"{rel_path} still references the retired TV-specific status tool")
            if "enquire_about_media" not in text:
                raise AssertionError(f"{rel_path} should mention enquire_about_media")

    def run_script(self, rel_path: str) -> None:
        """Run a project script with captured output and raise on failure."""
        result = subprocess.run(
            [sys.executable, rel_path],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(result.stdout + result.stderr)


if __name__ == "__main__":
    Round22RetiredToolRemovalAudit().run()
