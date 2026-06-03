#!/usr/bin/env python3
"""Round 211 regression checks for login auto-start and mobile layout."""

from __future__ import annotations

import os
import tempfile
import sys
import subprocess
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.autostart import AutoStartManager


def _make_project(root: Path) -> Path:
    project = root / "Long John Silver Test"
    project.mkdir(parents=True)
    run_sh = project / "run.sh"
    run_sh.write_text("#!/usr/bin/env bash\necho run\n", encoding="utf-8")
    run_sh.chmod(0o755)
    return project


def test_linux_entries_are_wrapper_based_and_space_safe() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        home = root / "home"
        home.mkdir()
        project = _make_project(root)
        with patch.dict(os.environ, {"HOME": str(home)}), patch("platform.system", return_value="Linux"), patch("src.core.autostart.shutil_which", return_value=None):
            mgr = AutoStartManager(project)
            mgr._write_launcher_script()
            mgr._write_desktop_entry()
            mgr._write_systemd_user_service()
            assert mgr.is_enabled(), "linux autostart status should detect generated entries"
            desktop = (home / ".config" / "autostart" / "long-john-silver.desktop").read_text(encoding="utf-8")
            service = (home / ".config" / "systemd" / "user" / "long-john-silver.service").read_text(encoding="utf-8")
            wrapper = (project / "data" / "autostart" / "start-ljs.sh").read_text(encoding="utf-8")
            assert "Exec=\"/bin/" in desktop and "start-ljs.sh" in desktop, desktop
            assert "ExecStart=\"/bin/" in service and "start-ljs.sh" in service, service
            assert "flock -n" in wrapper or "mkdir \"$lock_dir\"" in wrapper
            assert "run.sh" in wrapper and "LJS_AUTOSTART=1" in wrapper
            subprocess.run(["bash", "-n", str(project / "data" / "autostart" / "start-ljs.sh")], check=True)


def test_macos_launch_agent_is_wrapper_based() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        home = root / "home"
        home.mkdir()
        project = _make_project(root)
        with patch.dict(os.environ, {"HOME": str(home)}), patch("platform.system", return_value="Darwin"):
            mgr = AutoStartManager(project)
            mgr._write_launcher_script()
            mgr._write_launch_agent()
            assert mgr.is_enabled(), "macOS autostart status should detect generated LaunchAgent"
            plist = (home / "Library" / "LaunchAgents" / "com.longjohnsilver.ljs.plist").read_text(encoding="utf-8")
            assert "<key>ProgramArguments</key>" in plist
            assert "start-ljs.sh" in plist
            assert "<key>RunAtLoad</key>" in plist
            assert "<key>EnvironmentVariables</key>" in plist
            assert "/opt/homebrew/bin" in plist
            subprocess.run(["bash", "-n", str(project / "data" / "autostart" / "start-ljs.sh")], check=True)


def test_mobile_css_shell_rules_exist() -> None:
    css = Path("src/web/static/css/style.css").read_text(encoding="utf-8")
    assert "@supports (height: 100dvh)" in css
    assert "@media (max-width: 900px)" in css
    assert "@media (max-width: 720px)" in css
    assert ".nav-dock" in css and "overflow-x: auto" in css
    assert ".chat-input-area textarea" in css and "font-size: 16px" in css
    assert ".modal-content" in css and "max-height: calc(100dvh - 20px)" in css


def main() -> None:
    test_linux_entries_are_wrapper_based_and_space_safe()
    test_macos_launch_agent_is_wrapper_based()
    test_mobile_css_shell_rules_exist()
    print("round211 autostart/mobile tests: PASS")


if __name__ == "__main__":
    main()
