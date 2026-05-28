#!/usr/bin/env python3
"""Round 150 launcher test: FFmpeg auto-install is enabled by default."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_ffmpeg_auto_install_default_is_on() -> None:
    run_sh = (ROOT / "run.sh").read_text(encoding="utf-8")
    assert "${LJS_AUTO_INSTALL_FFMPEG:-1}" in run_sh
    assert "FFmpeg is missing; installing it automatically" in run_sh
    assert "LJS_AUTO_INSTALL_FFMPEG=0" in run_sh
    assert "Set LJS_AUTO_INSTALL_FFMPEG=0 to skip" in run_sh


def test_readme_documents_default_ffmpeg_install() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "tries to install FFmpeg automatically by default" in readme
    assert "LJS_AUTO_INSTALL_FFMPEG=0 ./run.sh" in readme


def main() -> None:
    test_ffmpeg_auto_install_default_is_on()
    test_readme_documents_default_ffmpeg_install()
    print("Round 150 FFmpeg auto-install default tests passed")


if __name__ == "__main__":
    main()
