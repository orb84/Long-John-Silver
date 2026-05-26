#!/usr/bin/env python3
"""Round 98 regression checks for TV ready-import paths and UI diagnostics."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    tv = read("src/core/categories/tv.py")
    system = read("src/web/routers/system.py")
    helm = read("src/web/static/js/components/helmPanel.js")
    app = read("src/web/static/js/app.js")
    css = read("src/web/static/css/style.css")
    agents = read("AGENTS.md")
    architecture = read("architecture.md")

    target_block = tv.split("def download_target_for_item", 1)[1].split("def fallback_library_path", 1)[0]
    require("fallback_library_path" in target_block, "TV ready imports must use the conservative category path directly")
    require("compute_target_path_from_fields" not in target_block, "TV ready imports must not try the configurable naming template first")
    require("TV root / show folder / season folder / source file" in target_block, "TV ready-import contract must be documented in code")

    require("level: str = Query(\"all\")" in system, "logs endpoint must accept a level filter")
    require("_is_warning_or_error_log_line" in system, "logs endpoint must expose warning/error filtering")
    require("wanted == \"warnings\"" in system and "wanted == \"errors\"" in system, "logs endpoint must filter warning and error tabs")

    require("data-log-level" in helm and "Warnings & Errors" in helm, "Helm UI must expose a warnings/errors log tab")
    require("this._voyageLogLevel" in app and "level=${encodeURIComponent(level)}" in app, "Voyage log polling must request the selected filter")
    require("voyage-log-tab" in css, "Voyage log tabs must be styled")

    require("Ready-Time Import Path Rule" in agents, "AGENTS must document ready-time import path rule")
    require("Round 98 — Ready Import Paths and Diagnostics" in architecture, "architecture must document Round 98")

    print("Round 98 ready-import diagnostics checks passed.")


if __name__ == "__main__":
    main()
