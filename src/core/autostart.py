"""Cross-platform operating-system auto-start integration for LJS.

The UI exposes this as a single checkbox, but macOS, Linux desktop sessions,
Linux systemd user sessions, and Windows all store login/startup entries
differently.  The OS-specific entries written here intentionally stay small:
they all delegate to one generated wrapper script under ``data/autostart``.

That wrapper is the single authority for profile loading, diagnostics, duplicate
process protection, and finally invoking ``run.sh``.  Keeping the runtime logic
in one script avoids the drift that previously made launchd, XDG autostart, and
systemd disagree about quoting, PATH, and working directory behavior.
"""

from __future__ import annotations

import os
import platform
import stat
from pathlib import Path
from typing import Any

from loguru import logger


class AutoStartManager:
    """Installs or removes a user-level launch-at-login entry."""

    APP_ID = "com.longjohnsilver.ljs"
    APP_NAME = "Long John Silver"

    def __init__(self, working_dir: str | Path | None = None, command: list[str] | None = None) -> None:
        self._working_dir = Path(working_dir).resolve() if working_dir else self._default_project_root()
        self._command = command or self._default_entry_command()

    def status(self) -> dict[str, Any]:
        """Return current platform auto-start state and generated target paths."""
        return {
            "platform": self._platform_key(),
            "supported": self.is_supported(),
            "enabled": self.is_enabled(),
            "target": str(self._entry_path()) if self._entry_path() else "",
            "linux_systemd_target": str(self._linux_systemd_path()) if self._platform_key() == "linux" else "",
            "working_dir": str(self._working_dir),
            "command": " ".join(str(part) for part in self._command),
            "launcher": str(self._launcher_script_path()),
            "lock_path": str(self._lock_path()),
            "log_path": str(self._log_path()),
        }

    def is_supported(self) -> bool:
        """Return whether this OS has a user-level startup implementation."""
        return self._platform_key() in {"darwin", "windows", "linux"}

    def is_enabled(self) -> bool:
        """Return whether a current launch entry points at this checkout."""
        try:
            key = self._platform_key()
            if key == "windows":
                return self._windows_value_matches_current_target()
            if key == "linux":
                desktop = self._entry_path()
                service = self._linux_systemd_path()
                desktop_ok = bool(desktop and desktop.exists() and self._file_entry_matches_current_target(desktop))
                service_ok = bool(service.exists() and self._file_entry_matches_current_target(service))
                return desktop_ok or service_ok
            path = self._entry_path()
            return bool(path and path.exists() and self._file_entry_matches_current_target(path))
        except Exception:
            return False

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        """Create or remove the user-level launch entry and return a report."""
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
        except Exception as exc:  # pragma: no cover - platform-specific
            logger.warning(f"Failed to update auto-start entry: {exc}")
            return {"ok": False, "enabled": self.is_enabled(), "message": str(exc), **self.status()}

    def _enable(self) -> None:
        self._write_launcher_script()
        key = self._platform_key()
        if key == "darwin":
            self._write_launch_agent()
            self._bootstrap_launch_agent()
        elif key == "linux":
            # Install both common user-level mechanisms.  Some Linux desktop
            # sessions start XDG autostart but not user systemd; some headless
            # user sessions only honor systemd.  The generated wrapper has a
            # lock, so installing both cannot create duplicate LJS processes.
            self._write_desktop_entry()
            self._write_systemd_user_service()
        elif key == "windows":
            self._write_windows_run_key()
        else:
            raise RuntimeError(f"Unsupported platform: {key}")

    def _disable(self) -> None:
        key = self._platform_key()
        if key == "windows":
            self._remove_windows_run_key()
            return
        if key == "linux":
            self._disable_systemd_user_service()
            service = self._linux_systemd_path()
            if service.exists():
                os.remove(service)
        if key == "darwin":
            self._bootout_launch_agent()
        path = self._entry_path()
        if path and path.exists():
            os.remove(path)

    @staticmethod
    def _platform_key() -> str:
        system = platform.system().lower()
        if system == "darwin":
            return "darwin"
        if system.startswith("win"):
            return "windows"
        if system == "linux":
            return "linux"
        return system

    @staticmethod
    def _default_project_root() -> Path:
        return Path(__file__).resolve().parents[2]

    def _entry_path(self) -> Path | None:
        key = self._platform_key()
        home = Path.home()
        if key == "darwin":
            return home / "Library" / "LaunchAgents" / f"{self.APP_ID}.plist"
        if key == "linux":
            return home / ".config" / "autostart" / "long-john-silver.desktop"
        return None

    def _autostart_dir(self) -> Path:
        return self._working_dir / "data" / "autostart"

    def _launcher_script_path(self) -> Path:
        return self._autostart_dir() / "start-ljs.sh"

    def _lock_path(self) -> Path:
        return self._autostart_dir() / "autostart.lock"

    def _log_path(self) -> Path:
        return self._autostart_dir() / "autostart.log"

    def _linux_systemd_path(self) -> Path:
        return Path.home() / ".config" / "systemd" / "user" / "long-john-silver.service"

    def _default_entry_command(self) -> list[str]:
        """Return argv used by the OS login manager to execute the wrapper."""
        if self._platform_key() == "windows":
            return [str(self._launcher_script_path())]
        shell = "/bin/bash" if Path("/bin/bash").exists() else "/bin/sh"
        return [shell, str(self._launcher_script_path())]

    def _file_entry_matches_current_target(self, path: Path) -> bool:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return False
        return str(self._working_dir) in text and str(self._launcher_script_path()) in text

    @staticmethod
    def _quote(value: str | Path) -> str:
        import shlex

        return shlex.quote(str(value))

    def _write_launcher_script(self) -> None:
        """Write the single cross-platform login wrapper."""
        script = self._launcher_script_path()
        script.parent.mkdir(parents=True, exist_ok=True)
        run_sh = self._working_dir / "run.sh"
        log_path = self._log_path()
        lock_path = self._lock_path()
        lock_dir = str(lock_path) + ".d"
        content = f'''#!/usr/bin/env bash
set -u
mkdir -p {self._quote(log_path.parent)}

log() {{
  printf '%s %s\n' "$(date -Is 2>/dev/null || date)" "$*" >> {self._quote(log_path)} 2>&1
}}

log "===== LJS autostart ====="
log "project={self._working_dir}"
log "run_sh={run_sh}"
log "user=$(id -un 2>/dev/null || true) uid=$(id -u 2>/dev/null || true)"
log "pwd=$(pwd)"
log "PATH=$PATH"

if command -v flock >/dev/null 2>&1; then
  exec 9>{self._quote(lock_path)}
  if ! flock -n 9; then
    log "another LJS autostart wrapper already holds the lock; exiting"
    exit 0
  fi
else
  lock_dir={self._quote(lock_dir)}
  if ! mkdir "$lock_dir" 2>/dev/null; then
    log "another LJS autostart wrapper appears active; exiting"
    exit 0
  fi
  trap 'rmdir "$lock_dir" 2>/dev/null || true' EXIT INT TERM
fi

for profile in /etc/profile "$HOME/.profile" "$HOME/.zprofile" "$HOME/.bash_profile"; do
  if [ -r "$profile" ]; then
    # shellcheck disable=SC1090
    . "$profile" >/dev/null 2>&1 || true
  fi
done

cd {self._quote(self._working_dir)} || {{ log "cannot cd into project"; exit 1; }}
if [ ! -x {self._quote(run_sh)} ]; then
  chmod +x {self._quote(run_sh)} >/dev/null 2>&1 || true
fi
if [ ! -x {self._quote(run_sh)} ]; then
  log "run.sh is missing or not executable: {run_sh}"
  exit 1
fi

export LJS_AUTOSTART=1
export LJS_ALLOW_INSECURE_DEV="${{LJS_ALLOW_INSECURE_DEV:-1}}"
export LJS_ACCESS_LOGS="${{LJS_ACCESS_LOGS:-quiet}}"
export LJS_PORT="${{LJS_PORT:-8088}}"
log "launching LJS via run.sh on port $LJS_PORT"
exec {self._quote(run_sh)} "$LJS_PORT" >> {self._quote(log_path)} 2>&1
'''
        script.write_text(content, encoding="utf-8")
        script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def _write_launch_agent(self) -> None:
        path = self._entry_path()
        if path is None:
            raise RuntimeError("No LaunchAgent path available")
        path.parent.mkdir(parents=True, exist_ok=True)
        program_args = "\n".join(f"        <string>{self._xml_escape(str(part))}</string>" for part in self._command)
        plist = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
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
    <key>EnvironmentVariables</key>
    <dict>
        <key>LJS_AUTOSTART</key>
        <string>1</string>
        <key>LJS_ACCESS_LOGS</key>
        <string>quiet</string>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
    <key>StandardOutPath</key>
    <string>{self._xml_escape(str(self._log_path()))}</string>
    <key>StandardErrorPath</key>
    <string>{self._xml_escape(str(self._log_path()))}</string>
</dict>
</plist>
'''
        path.write_text(plist, encoding="utf-8")

    def _bootstrap_launch_agent(self) -> None:
        if self._platform_key() != "darwin":
            return
        path = self._entry_path()
        if not path:
            return
        import subprocess

        uid = os.getuid()
        subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        completed = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if completed.returncode == 0:
            # RunAtLoad fires at bootstrap.  The wrapper lock prevents duplicate
            # launches if LJS is already running because the user toggled this
            # from the UI.
            subprocess.run(["launchctl", "kickstart", "-k", f"gui/{uid}/{self.APP_ID}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        else:
            logger.info("LaunchAgent written but not bootstrapped in current session; it will run at next GUI login.")

    def _bootout_launch_agent(self) -> None:
        if self._platform_key() != "darwin":
            return
        path = self._entry_path()
        if not path:
            return
        import subprocess

        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

    def _desktop_exec_value(self) -> str:
        return " ".join(self._desktop_quote(part) for part in self._command)

    @staticmethod
    def _desktop_quote(value: str | Path) -> str:
        text = str(value)
        escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
        return f'"{escaped}"'

    def _systemd_exec_value(self) -> str:
        return " ".join(self._systemd_quote(part) for part in self._command)

    @staticmethod
    def _systemd_quote(value: str | Path) -> str:
        text = str(value).replace("\\", "\\\\").replace('"', '\\"')
        return f'"{text}"'

    def _write_desktop_entry(self) -> None:
        path = self._entry_path()
        if path is None:
            raise RuntimeError("No autostart desktop path available")
        path.parent.mkdir(parents=True, exist_ok=True)
        desktop = f'''[Desktop Entry]
Type=Application
Version=1.0
Name={self.APP_NAME}
Comment=Start LJS media automation at login
Exec={self._desktop_exec_value()}
Path={self._working_dir}
Terminal=false
StartupNotify=false
DBusActivatable=false
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=8
'''
        path.write_text(desktop, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IRUSR | stat.S_IWUSR)

    def _write_systemd_user_service(self) -> None:
        if self._platform_key() != "linux":
            return
        path = self._linux_systemd_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        service = f'''[Unit]
Description=Long John Silver media automation
After=network-online.target graphical-session.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={self._systemd_quote(self._working_dir)}
ExecStart={self._systemd_exec_value()}
Restart=on-failure
RestartSec=10
Environment=LJS_AUTOSTART=1
Environment=LJS_ALLOW_INSECURE_DEV=1
Environment=LJS_ACCESS_LOGS=quiet

[Install]
WantedBy=default.target
'''
        path.write_text(service, encoding="utf-8")
        import subprocess

        if shutil_which("systemctl"):
            subprocess.run(["systemctl", "--user", "daemon-reload"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            completed = subprocess.run(["systemctl", "--user", "enable", "long-john-silver.service"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            if completed.returncode != 0:
                logger.info("systemd user service written but not enabled; XDG autostart entry remains installed.")

    def _disable_systemd_user_service(self) -> None:
        if self._platform_key() != "linux":
            return
        import subprocess

        if shutil_which("systemctl"):
            subprocess.run(["systemctl", "--user", "disable", "long-john-silver.service"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            subprocess.run(["systemctl", "--user", "daemon-reload"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

    def _write_windows_run_key(self) -> None:
        import winreg  # type: ignore

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, self.APP_NAME, 0, winreg.REG_SZ, self._windows_command())

    def _remove_windows_run_key(self) -> None:
        import winreg  # type: ignore

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, self.APP_NAME)
        except FileNotFoundError:
            return

    def _windows_value_matches_current_target(self) -> bool:
        if self._platform_key() != "windows":
            return False
        try:
            import winreg  # type: ignore

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ) as key:
                value, _ = winreg.QueryValueEx(key, self.APP_NAME)
                return str(value) == self._windows_command()
        except Exception:
            return False

    def _windows_command(self) -> str:
        return subprocess_quoted(self._command)

    @staticmethod
    def _xml_escape(value: str) -> str:
        return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def shutil_which(command: str) -> str | None:
    """Return the resolved executable path for a command, if available."""
    import shutil

    return shutil.which(command)


def subprocess_quoted(parts: list[str]) -> str:
    """Return a Windows CreateProcess-compatible command line."""
    import subprocess

    return subprocess.list2cmdline([str(part) for part in parts])
