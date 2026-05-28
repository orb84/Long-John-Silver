#!/usr/bin/env python3
"""Round 149 FFmpeg setup/bootstrap regression tests."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.config import SettingsManager
from src.core.categories.registry import CategoryRegistry


def test_ffmpeg_is_not_first_run_blocker() -> None:
    sm = SettingsManager()
    settings = sm.settings
    registry = CategoryRegistry.with_defaults()
    ffmpeg_requirements = []
    for category in registry.list_all():
        for req in category.setup_requirements(settings):
            if req.id == "runtime_ffmpeg":
                ffmpeg_requirements.append((category.category_id, req))
    assert ffmpeg_requirements, "Expected audio-derived categories to declare FFmpeg"
    assert all(req.required is False for _, req in ffmpeg_requirements), ffmpeg_requirements
    assert all(req.severity in {"recommended", "warning", "info"} for _, req in ffmpeg_requirements), ffmpeg_requirements


def test_setup_router_dedupes_runtime_requirements() -> None:
    src = (ROOT / "src/web/routers/setup.py").read_text(encoding="utf-8")
    assert 'if ".runtime_" in raw_id' in src
    assert "add_missing" in src and "add_warning" in src


def test_setup_js_dedupes_toast_items() -> None:
    src = (ROOT / "src/web/static/js/pages/setup.js").read_text(encoding="utf-8")
    assert "function uniqueSetupItems" in src
    assert "runtime_" in src
    assert "var missing = uniqueSetupItems" in src


def test_run_sh_ffmpeg_action() -> None:
    src = (ROOT / "run.sh").read_text(encoding="utf-8")
    assert "install-ffmpeg" in src
    assert "LJS_AUTO_INSTALL_FFMPEG" in src
    assert "warn_or_install_ffmpeg" in src
    assert "${LJS_AUTO_INSTALL_FFMPEG:-1}" in src
    assert "LJS_AUTO_INSTALL_FFMPEG=0" in src


def main() -> None:
    test_ffmpeg_is_not_first_run_blocker()
    test_setup_router_dedupes_runtime_requirements()
    test_setup_js_dedupes_toast_items()
    test_run_sh_ffmpeg_action()
    print("Round 149 FFmpeg setup/bootstrap tests passed")


if __name__ == "__main__":
    main()
