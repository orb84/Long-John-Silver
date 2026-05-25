"""Cross-platform operating-system auto-start integration for LJS.

The UI exposes this as a single checkbox, but each OS stores login/startup
entries differently.  This service hides those platform details behind a small
API that settings/setup handlers can call safely.  It intentionally writes only
user-level launch entries so enabling auto-start never requires administrator
privileges.
"""

from __future__ import annotations

import os
import platform
import stat
import sys
from pathlib import Path
from typing import Any

from loguru import logger


class AutoStartManager:
    """Installs or removes a user-level launch-at-login entry.

    Maintainers should keep this class free of web/UI concerns.  It answers two
    questions only: can this platform be configured, and did the user-level boot
    entry get created/removed?  Future packaged-app support can override
    ``command`` and ``working_dir`` while preserving the same public contract.
    """

    APP_ID = "com.longjohnsilver.ljs"
    APP_NAME = "Long John Silver"

    def __init__(self, working_dir: str | Path | None = None, command: list[str] | None = None) -> None:
        """Create an auto-start manager for the current application checkout.

        Args:
            working_dir: Project root containing ``main.py``. Defaults to the
                current working directory used to launch LJS.
            command: Optional explicit command. Packaged distributions should
                pass their executable command here; source checkouts use
                ``sys.executable main.py``.
        """
        self._working_dir = Path(working_dir or os.getcwd()).resolve()
        self._command = command or [sys.executable, str(self._working_dir / "main.py")]

    def status(self) -> dict[str, Any]:
        """Return the current platform and whether the launch entry exists."""
        return {
            "platform": self._platform_key(),
            "supported": self.is_supported(),
            "enabled": self.is_enabled(),
            "target": str(self._entry_path()) if self._entry_path() else "",
            "command": " ".join(self._command),
        }

    def is_supported(self) -> bool:
        """Return whether this OS has a user-level implementation."""
        return self._platform_key() in {"darwin", "windows", "linux"}

    def is_enabled(self) -> bool:
        """Return whether the expected auto-start entry is already present."""
        try:
            path = self._entry_path()
            if not path:
                return False
            if self._platform_key() == "windows":
                return self._windows_value_exists()
            return path.exists()
        except Exception:
            return False

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        """Create or remove the auto-start entry and return an operation report.

        Args:
            enabled: True to start LJS at login/boot, False to remove the entry.

        Returns:
            JSON-safe dict suitable for API responses.  ``ok`` is false when the
            OS cannot be configured or the write failed.
        """
        if not self.is_supported():
            return {"ok": False, "enabled": False, "message": "Auto-start is not supported on this platform."}
        try:
            if enabled:
                self._enable()
            else:
                self._disable()
            actual = self.is_enabled()
            return {
                "ok": actual == bool(enabled),
                "enabled": actual,
                "message": "Auto-start enabled." if actual else "Auto-start disabled.",
                **self.status(),
            }
        except Exception as exc:
            logger.warning(f"Failed to update auto-start entry: {exc}")
            return {"ok": False, "enabled": self.is_enabled(), "message": str(exc), **self.status()}

    def _enable(self) -> None:
        """Dispatch auto-start installation to the current platform."""
        key = self._platform_key()
        if key == "darwin":
            self._write_launch_agent()
        elif key == "linux":
            self._write_desktop_entry()
        elif key == "windows":
            self._write_windows_run_key()
        else:
            raise RuntimeError(f"Unsupported platform: {key}")

    def _disable(self) -> None:
        """Remove the current platform's user-level auto-start entry."""
        key = self._platform_key()
        if key == "windows":
            self._remove_windows_run_key()
            return
        path = self._entry_path()
        if path and path.exists():
            # The path is deterministic and user-level (LaunchAgents/autostart),
            # not supplied by the web UI or LLM.  Keep deletion local to this
            # service so startup cleanup never touches library/download data.
            os.remove(path)

    @staticmethod
    def _platform_key() -> str:
        """Return normalized platform key used by the service."""
        system = platform.system().lower()
        if system == "darwin":
            return "darwin"
        if system.startswith("win"):
            return "windows"
        if system == "linux":
            return "linux"
        return system

    def _entry_path(self) -> Path | None:
        """Return the platform-specific file path, if the platform uses one."""
        key = self._platform_key()
        home = Path.home()
        if key == "darwin":
            return home / "Library" / "LaunchAgents" / f"{self.APP_ID}.plist"
        if key == "linux":
            return home / ".config" / "autostart" / "long-john-silver.desktop"
        if key == "windows":
            return None
        return None

    def _quote(self, value: str | Path) -> str:
        """Shell-quote one command/path component for desktop/plist files."""
        import shlex

        return shlex.quote(str(value))

    def _command_line(self) -> str:
        """Return the command as a shell-safe line for user-level launch files."""
        return " ".join(self._quote(part) for part in self._command)

    def _write_launch_agent(self) -> None:
        """Write the macOS LaunchAgent plist for login auto-start."""
        path = self._entry_path()
        if path is None:
            raise RuntimeError("No LaunchAgent path available")
        path.parent.mkdir(parents=True, exist_ok=True)
        program_args = "\n".join(f"        <string>{self._xml_escape(str(part))}</string>" for part in self._command)
        plist = f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">
<plist version=\"1.0\">
<dict>
    <key>Label</key>
    <string>{self.APP_ID}</string>
    <key>ProgramArguments</key>
    <array>
{program_args}
    </array>
    <key>WorkingDirectory</key>
    <string>{self._xml_escape(str(self._working_dir))}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
</dict>
</plist>
"""
        path.write_text(plist, encoding="utf-8")

    def _write_desktop_entry(self) -> None:
        """Write a freedesktop autostart entry for Linux desktop sessions."""
        path = self._entry_path()
        if path is None:
            raise RuntimeError("No autostart desktop path available")
        path.parent.mkdir(parents=True, exist_ok=True)
        desktop = f"""[Desktop Entry]
Type=Application
Name={self.APP_NAME}
Comment=Start LJS media automation at login
Exec={self._command_line()}
Path={self._working_dir}
Terminal=false
X-GNOME-Autostart-enabled=true
"""
        path.write_text(desktop, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def _write_windows_run_key(self) -> None:
        """Create the HKCU Run value used by Windows login auto-start."""
        import winreg  # type: ignore

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, self.APP_NAME, 0, winreg.REG_SZ, self._windows_command())

    def _remove_windows_run_key(self) -> None:
        """Remove the HKCU Run value, ignoring already-removed entries."""
        import winreg  # type: ignore

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, self.APP_NAME)
        except FileNotFoundError:
            return

    def _windows_value_exists(self) -> bool:
        """Return whether the HKCU Run value exists on Windows."""
        if self._platform_key() != "windows":
            return False
        try:
            import winreg  # type: ignore

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ) as key:
                winreg.QueryValueEx(key, self.APP_NAME)
                return True
        except Exception:
            return False

    def _windows_command(self) -> str:
        """Return a Windows Run value command line."""
        return subprocess_quoted(self._command)

    @staticmethod
    def _xml_escape(value: str) -> str:
        """Escape text for the small plist we generate manually."""
        return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def subprocess_quoted(parts: list[str]) -> str:
    """Return a conservative Windows command line from argument parts.

    Python's ``subprocess.list2cmdline`` is exactly the quoting algorithm used
    for Windows CreateProcess; reusing it avoids hand-rolled quoting bugs.
    """
    import subprocess

    return subprocess.list2cmdline([str(part) for part in parts])
