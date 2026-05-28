#!/usr/bin/env python3
"""Round 151 Windows launcher bootstrap regression tests."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN_BAT = (ROOT / "run.bat").read_text(encoding="utf-8", errors="ignore")
README = (ROOT / "README.md").read_text(encoding="utf-8", errors="ignore")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_windows_launcher_has_python_bootstrap() -> None:
    text = RUN_BAT
    require("install-python" in text, "run.bat should expose install-python")
    require("LJS_AUTO_INSTALL_PYTHON" in text, "run.bat should allow disabling Python auto-install")
    require("Python.Python.3.11" in text, "run.bat should install a known-compatible Python through winget")
    require("py -3.12" in text and "py -3.11" in text and "py -3.10" in text, "run.bat should discover versioned Python launchers")
    require("%LOCALAPPDATA%\\Programs\\Python\\Python311\\python.exe" in text, "run.bat should discover user-scope winget/python.org installs")
    require("rmdir /s /q \"%VENV_DIR%\"" in text, "run.bat should recreate stale/old virtualenvs")


def test_windows_launcher_has_ffmpeg_bootstrap() -> None:
    text = RUN_BAT
    require("install-ffmpeg" in text, "run.bat should expose install-ffmpeg")
    require("LJS_AUTO_INSTALL_FFMPEG" in text, "run.bat should allow disabling FFmpeg auto-install")
    require("Gyan.FFmpeg" in text, "run.bat should install FFmpeg through winget")
    require("choco install ffmpeg -y" in text, "run.bat should have Chocolatey fallback")
    require("scoop install ffmpeg" in text, "run.bat should have Scoop fallback")
    require("Microsoft\\WinGet\\Packages" in text, "run.bat should detect winget-installed FFmpeg even before PATH refresh")


def test_windows_launcher_has_diagnostics_and_safe_startup() -> None:
    text = RUN_BAT
    require("run.bat doctor" in text, "run.bat should expose doctor diagnostics")
    require("%VENV_PYTHON%\" main.py" in text, "run.bat should launch with the venv interpreter, not ambient python")
    require("playwright install chromium" in text, "run.bat should ensure Playwright browser assets")
    require("LJS_ALLOW_INSECURE_DEV=1" in text, "run.bat should preserve development auth behavior")


def test_readme_documents_windows_bootstrap() -> None:
    text = README
    require("run.bat install-python" in text, "README should document Windows Python installer")
    require("run.bat install-ffmpeg" in text, "README should document Windows FFmpeg installer")
    require("Gyan.FFmpeg" in text, "README should name the Windows FFmpeg winget package")
    require("Chocolatey/Scoop fallbacks" in text, "README should document fallback package managers")


def main() -> None:
    test_windows_launcher_has_python_bootstrap()
    test_windows_launcher_has_ffmpeg_bootstrap()
    test_windows_launcher_has_diagnostics_and_safe_startup()
    test_readme_documents_windows_bootstrap()
    print("Round 151 Windows launcher bootstrap tests passed")


if __name__ == "__main__":
    main()
