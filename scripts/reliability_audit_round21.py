#!/usr/bin/env python3
"""Round 21 architecture cleanup audit.

This audit verifies that the Round 20 hotfix shims were converted into explicit
compatibility adapters, that the active tool surface uses category-owned media
enquiry, and that the startup smoke test covers the real tool provider graph.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Round21ArchitectureAudit:
    """Coordinate Round 21 cleanup invariants."""

    def run(self) -> None:
        """Run all Round 21 checks and raise on failure."""
        self.require_prompt_guidance()
        self.run_script("scripts/check_compatibility_shims.py")
        self.run_script("scripts/startup_smoke_round21.py")
        print("round21 architecture cleanup audit passed")

    def require_prompt_guidance(self) -> None:
        """Ensure planner and prompt text direct agents to the generic status tool."""
        prompt_text = (ROOT / "src/ai/prompt_builder.py").read_text(encoding="utf-8")
        reasoning_text = (ROOT / "src/ai/reasoning.py").read_text(encoding="utf-8")
        retired_name = "show" + "_episodes"
        for text, label in ((prompt_text, "prompt_builder"), (reasoning_text, "reasoning")):
            if "enquire_about_media" not in text:
                raise AssertionError(f"{label} must mention enquire_about_media")
            if retired_name in text:
                raise AssertionError(f"{label} must not reference retired {retired_name}")

    def run_script(self, rel_path: str) -> None:
        """Run one project script with root cwd and raise with captured output."""
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
    Round21ArchitectureAudit().run()
