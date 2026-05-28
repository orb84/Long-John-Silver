#!/usr/bin/env python3
"""Round 148 launcher/bootstrap regression tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_run_sh_syntax() -> None:
    subprocess.run(["bash", "-n", str(ROOT / "run.sh")], check=True)


def test_run_sh_bootstrap_features() -> None:
    launcher = read("run.sh")
    for needle in [
        "LJS_PYTHON",
        "LJS_AUTO_INSTALL_PYTHON",
        "install_python_macos",
        "install_python_linux",
        "install_homebrew_if_allowed",
        "python3.11",
        "python3.12",
        "pyenv",
        "ensurepip",
        "reset-venv",
        "doctor",
        "validate_port",
        "exec \"$VENV_PYTHON\" main.py",
    ]:
        require(needle in launcher, f"run.sh missing expected launcher feature: {needle}")


def test_torrent_selection_fstring_fixed() -> None:
    src = read("src/ai/torrent_selection.py")
    require('f"No candidates after normalization for {item_display_name} {unit_key or ""}' not in src, "broken nested double-quote f-string returned")
    require('f"No queueable candidates after hard filtering for {item_display_name} {unit_key or ""}' not in src, "broken nested double-quote f-string returned")
    require("No candidates after normalization" in src, "expected log line missing")


def test_readme_documents_launcher() -> None:
    readme = read("README.md")
    for needle in [
        "./run.sh install",
        "./run.sh update",
        "./run.sh doctor",
        "./run.sh reset-venv",
        "LJS_AUTO_INSTALL_PYTHON",
        "LJS_AUTO_INSTALL_HOMEBREW",
    ]:
        require(needle in readme, f"README missing launcher documentation: {needle}")


def main() -> None:
    test_run_sh_syntax()
    test_run_sh_bootstrap_features()
    test_torrent_selection_fstring_fixed()
    test_readme_documents_launcher()
    print("Round 148 launcher/bootstrap tests passed")


if __name__ == "__main__":
    main()
