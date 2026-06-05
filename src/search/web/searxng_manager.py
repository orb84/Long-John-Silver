"""Managed SearXNG runtime for LJS web research.

LJS treats SearXNG as an optional local research sidecar. Managed mode owns an
isolated source checkout, virtual environment, generated settings.yml, logs, and
process lifecycle. It must not adopt or trust an already-running system/global
SearXNG instance, because that compromises first-install tests and can hide
configuration drift.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import secrets
import shutil
import socket
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, IO

from loguru import logger

from src.core.models import Settings, WebSearchConfig
from src.core.security.path_policy import SafePathResolver
from src.utils.archive_safety import safe_extract_tar


SEARXNG_DEFAULT_PORT = 18888
SEARXNG_FALLBACK_REF = "master"
SEARXNG_SOURCE_BASE = "https://github.com/searxng/searxng/archive"
SEARXNG_PROJECT_ROOT = Path(__file__).resolve().parents[3]
SEARXNG_DATA_DIR = SEARXNG_PROJECT_ROOT / "data" / "searxng"
SEARXNG_STATE_DIR = SEARXNG_PROJECT_ROOT / "data" / "searxng_state"


class SearXNGManager:
    """Install, configure, start, stop, upgrade, and health-check managed SearXNG.

    The manager is intentionally conservative around pre-existing processes. If
    the requested port is already occupied before this manager starts its child
    process, LJS chooses another localhost port instead of adopting the existing
    service. Manual/external SearXNG instances remain supported through
    ``settings.web_search.mode == "manual"`` and the normal provider health
    check.
    """

    def __init__(self, service_dir: Path | None = None, state_dir: Path | None = None) -> None:
        self._service_dir = service_dir or SEARXNG_DATA_DIR
        self._state_dir = state_dir or SEARXNG_STATE_DIR
        self._process: asyncio.subprocess.Process | None = None
        self._log_handle: IO[bytes] | None = None
        self._running = False
        self._installed = False
        self._last_error: str | None = None
        self._port = SEARXNG_DEFAULT_PORT
        self._trace_event("manager_initialized", service_dir=str(self._service_dir), state_dir=str(self._state_dir), platform=self.platform_label())

    @property
    def is_installed(self) -> bool:
        """Return whether the LJS-owned SearXNG runtime is complete.

        A source checkout plus a Python executable is not enough: uv can create
        a venv without pip, leaving a partial install that cannot start. LJS only
        treats the runtime as installed after dependency installation writes its
        own completion marker.
        """
        return self._installed or self._source_dir().exists() and self._venv_ready_marker().exists() and self._venv_python().exists()

    @property
    def is_running(self) -> bool:
        """Return whether this LJS process owns a live SearXNG child process."""
        return bool(self._running and self._process is not None and self._process.returncode is None)

    @property
    def url(self) -> str:
        """Return the current managed localhost URL."""
        return f"http://127.0.0.1:{self._port}"

    @property
    def last_error(self) -> str | None:
        """Return the most recent managed runtime error."""
        return self._last_error

    @staticmethod
    def platform_supported() -> bool:
        """Return whether the current OS family is supported by managed mode."""
        return platform.system() in {"Darwin", "Linux", "Windows"}

    async def ensure_installed(self, force: bool = False, *, source_ref: str | None = None) -> bool:
        """Download SearXNG source and create an isolated virtual environment."""
        self._trace_event("install.ensure_started", force=force, source_ref=self._current_source_ref(source_ref), installed=self.is_installed)
        if not self.platform_supported():
            self._last_error = f"Unsupported SearXNG platform: {self.platform_label()}"
            logger.error(self._last_error)
            self._trace_event("install.unsupported_platform", error=self._last_error)
            return False
        if not force and self.is_installed:
            self._installed = True
            self._trace_event("install.reused_ljs_owned_runtime", source_dir=str(self._source_dir()), venv_python=str(self._venv_python()))
            return True
        self._prepare_directories()
        if force:
            self._trace_event("install.force_clear_runtime_paths")
            await asyncio.to_thread(self._clear_runtime_paths)
        source_ok = await self._ensure_source(source_ref=source_ref)
        if not source_ok:
            return False
        venv_ok = await self._ensure_venv()
        if not venv_ok:
            return False
        self._write_state({
            "installed": True,
            "installed_at": self._utc_now(),
            "platform": self.platform_label(),
            "source_ref": self._current_source_ref(source_ref),
        })
        self._installed = True
        self._last_error = None
        self._trace_event("install.ensure_finished", source_dir=str(self._source_dir()), venv_python=str(self._venv_python()))
        return True

    async def configure(self, settings: Settings) -> bool:
        """Write managed SearXNG settings and persist LJS web-search config."""
        self._trace_event("configure.started")
        cfg = getattr(settings, "web_search", WebSearchConfig())
        self._port = self._select_port(int(getattr(cfg, "managed_port", 0) or SEARXNG_DEFAULT_PORT))
        cfg.enabled = True
        cfg.provider = "searxng"
        cfg.mode = "managed"
        cfg.auto_install = True
        cfg.managed_port = self._port
        cfg.api_base = self.url
        cfg.status = "configuring"
        cfg.status_message = "Writing managed SearXNG settings."
        cfg.last_health_check = self._utc_now()
        settings.web_search = cfg
        try:
            self.config_path().parent.mkdir(parents=True, exist_ok=True)
            self.config_path().write_text(self._render_settings(cfg), encoding="utf-8")
            self.logs_dir().mkdir(parents=True, exist_ok=True)
            self._last_error = None
            self._trace_event("configure.finished", port=self._port, url=self.url, config_path=str(self.config_path()))
            return True
        except Exception as exc:
            self._last_error = f"Could not write managed SearXNG settings: {exc}"
            cfg.status = "error"
            cfg.status_message = self._last_error
            logger.error(self._last_error)
            self._trace_event("configure.failed", error=self._last_error)
            return False

    async def start(self, settings: Settings, *, health_timeout_seconds: float = 35.0) -> bool:
        """Install/configure/start managed SearXNG and require JSON health."""
        self._trace_event("start.requested", health_timeout_seconds=health_timeout_seconds, running=self.is_running)
        cfg = getattr(settings, "web_search", WebSearchConfig())
        if getattr(cfg, "mode", "managed") == "manual":
            self._last_error = "SearXNG is configured for manual/external mode."
            self._trace_event("start.refused_manual_mode", error=self._last_error)
            return False
        if self.is_running:
            self._trace_event("start.reused_owned_process", pid=self._process.pid if self._process else None)
            return await self._wait_for_json_health(settings, timeout_seconds=5.0)
        source_ref = self._configured_source_ref(cfg)
        if not await self.ensure_installed(source_ref=source_ref):
            return False
        if not await self.configure(settings):
            return False
        command = self._start_command()
        self._trace_event("start.launching_process", command=self._redacted_command(command), cwd=str(self._source_dir()), url=self.url)
        try:
            self.logs_dir().mkdir(parents=True, exist_ok=True)
            self._close_log_handle()
            self._log_handle = open(self.logs_dir() / "searxng.log", "ab", buffering=0)
            self._process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(self._source_dir()),
                env=self._process_environment(),
                stdout=self._log_handle,
                stderr=asyncio.subprocess.STDOUT,
            )
            self._running = True
            self._trace_event("start.process_spawned", pid=self._process.pid, log_path=str(self.logs_dir() / "searxng.log"))
            ok = await self._wait_for_json_health(settings, timeout_seconds=health_timeout_seconds)
            if not ok:
                await self.stop()
                return False
            cfg.status = "ready"
            cfg.status_message = "Managed SearXNG is installed, running, and JSON search is available."
            cfg.api_base = self.url
            cfg.last_health_check = self._utc_now()
            self._last_error = None
            self._write_state({"running": True, "url": self.url, "started_at": self._utc_now(), "pid": self._process.pid})
            self._trace_event("start.ready", pid=self._process.pid, url=self.url)
            return True
        except Exception as exc:
            self._last_error = f"Managed SearXNG start failed: {exc}"
            cfg.status = "error"
            cfg.status_message = self._last_error
            cfg.last_health_check = self._utc_now()
            logger.error(self._last_error)
            self._trace_event("start.failed", error=self._last_error)
            await self.stop()
            return False

    async def stop(self) -> dict[str, Any]:
        """Stop only the SearXNG process owned by this LJS process."""
        self._trace_event("stop.requested", running=self.is_running, pid=self._process.pid if self._process else None)
        if not self._process or self._process.returncode is not None:
            self._running = False
            self._close_log_handle()
            self._trace_event("stop.no_owned_process")
            return {"status": "stopped", "running": False}
        self._process.terminate()
        try:
            await asyncio.wait_for(self._process.wait(), timeout=8.0)
        except asyncio.TimeoutError:
            self._process.kill()
            await self._process.wait()
        self._running = False
        self._close_log_handle()
        self._write_state({"running": False, "stopped_at": self._utc_now()})
        self._trace_event("stop.finished")
        return {"status": "stopped", "running": False}

    async def repair(self, settings: Settings) -> dict[str, Any]:
        """Repair generated settings/venv and restart managed SearXNG."""
        self._trace_event("repair.started")
        await self.stop()
        ok = await self.ensure_installed(force=False, source_ref=self._configured_source_ref(settings.web_search))
        if ok:
            ok = await self.configure(settings)
        if ok:
            ok = await self.start(settings)
        self.save_to_settings(settings)
        self._trace_event("rollback.finished", ok=ok, error=self._last_error)
        return await self.health_check(settings) if ok else {"status": "error", "ready": False, "error": self._last_error}

    async def upgrade(self, settings: Settings, *, source_ref: str | None = None) -> dict[str, Any]:
        """Reinstall managed SearXNG from a target ref with rollback on failure."""
        self._trace_event("upgrade.started", source_ref=source_ref or self._configured_source_ref(settings.web_search))
        await self.stop()
        target_ref = self._current_source_ref(source_ref or self._configured_source_ref(settings.web_search))
        backup_dir = await asyncio.to_thread(self._backup_runtime, reason="upgrade")
        ok = False
        try:
            ok = await self.ensure_installed(force=True, source_ref=target_ref)
            if ok:
                ok = await self.configure(settings)
            if ok:
                ok = await self.start(settings)
            if ok:
                self._write_state({"last_upgrade_at": self._utc_now(), "source_ref": target_ref, "rollback_available": True})
                self.save_to_settings(settings)
                self._trace_event("upgrade.finished", source_ref=target_ref, backup_dir=str(backup_dir), url=self.url)
                return {"status": "ready", "ready": True, "url": self.url, "source_ref": target_ref, "backup_dir": str(backup_dir)}
        except Exception as exc:
            self._last_error = f"Managed SearXNG upgrade failed: {exc}"
            logger.error(self._last_error)
        self._trace_event("upgrade.failed_rollback_starting", error=self._last_error or "upgrade did not become ready", backup_dir=str(backup_dir))
        await asyncio.to_thread(self._restore_runtime_backup, backup_dir)
        restored = await self.start(settings)
        self.save_to_settings(settings)
        self._trace_event("upgrade.rollback_finished", restored=restored, backup_dir=str(backup_dir), error=self._last_error)
        return {
            "status": "rolled_back" if restored else "error",
            "ready": bool(restored),
            "error": self._last_error or "Managed SearXNG upgrade failed; rollback attempted.",
            "backup_dir": str(backup_dir),
        }

    async def rollback(self, settings: Settings) -> dict[str, Any]:
        """Restore the most recent managed SearXNG runtime backup."""
        self._trace_event("rollback.started")
        await self.stop()
        backup_dir = self._latest_backup_dir()
        if not backup_dir:
            self._last_error = "No managed SearXNG backup is available for rollback."
            self._trace_event("rollback.no_backup", error=self._last_error)
            return {"status": "error", "ready": False, "error": self._last_error}
        await asyncio.to_thread(self._restore_runtime_backup, backup_dir)
        ok = await self.configure(settings)
        if ok:
            ok = await self.start(settings)
        self.save_to_settings(settings)
        self._trace_event("repair.finished", ok=ok, error=self._last_error)
        return await self.health_check(settings) if ok else {"status": "error", "ready": False, "error": self._last_error}

    async def uninstall(self, settings: Settings) -> dict[str, Any]:
        """Stop and remove the LJS-owned managed SearXNG runtime."""
        self._trace_event("uninstall.started")
        await self.stop()
        await asyncio.to_thread(self._clear_runtime_paths)
        self._installed = False
        cfg = getattr(settings, "web_search", WebSearchConfig())
        cfg.status = "not_installed"
        cfg.status_message = "Managed SearXNG runtime removed. Manual SearXNG configuration remains available."
        cfg.last_health_check = self._utc_now()
        settings.web_search = cfg
        self._write_state({"installed": False, "running": False, "uninstalled_at": self._utc_now()})
        self._trace_event("uninstall.finished", service_dir=str(self._service_dir))
        return {"status": "not_installed", "ready": False, "installed": False}

    async def health_check(self, settings: Settings | None = None) -> dict[str, Any]:
        """Return managed runtime health without adopting external instances."""
        self._trace_event("health.started", running=self.is_running, installed=self.is_installed)
        cfg = getattr(settings, "web_search", None) if settings is not None else None
        if cfg is not None:
            self._port = int(getattr(cfg, "managed_port", self._port) or self._port)
        installed = self.is_installed
        process_owned = self.is_running
        configured = self.config_path().exists()
        json_ok = False
        status = "stopped"
        error = self._last_error
        if process_owned:
            json_ok = await self._probe_json(self.url)
            status = "ready" if json_ok else "error"
            error = None if json_ok else (self._last_error or "Managed SearXNG process is running but JSON health failed.")
        elif installed:
            status = "installed"
        if cfg is not None:
            cfg.status = status
            cfg.status_message = error or ("Managed SearXNG ready." if json_ok else "Managed SearXNG is not running.")
            cfg.last_health_check = self._utc_now()
        state = self._read_state()
        result = {
            "status": status,
            "ready": bool(installed and process_owned and json_ok),
            "installed": installed,
            "running": process_owned,
            "configured": configured,
            "json_api": json_ok,
            "url": self.url,
            "mode": getattr(cfg, "mode", "managed") if cfg is not None else "managed",
            "service_dir": str(self._service_dir),
            "config_path": str(self.config_path()),
            "logs_dir": str(self.logs_dir()),
            "source_ref": state.get("source_ref", self._current_source_ref(None)),
            "rollback_available": bool(self._latest_backup_dir()),
            "platform": self.platform_label(),
            "error": error,
        }
        self._trace_event("health.finished", status=status, ready=result["ready"], json_api=json_ok, error=error)
        return result

    def save_to_settings(self, settings: Settings) -> None:
        """Persist current managed endpoint/status into ``Settings.web_search``."""
        cfg = getattr(settings, "web_search", WebSearchConfig())
        cfg.provider = "searxng"
        cfg.mode = "managed"
        cfg.enabled = True
        cfg.auto_install = True
        cfg.managed_port = self._port
        cfg.api_base = self.url
        cfg.status_message = self._last_error or cfg.status_message
        cfg.last_health_check = self._utc_now()
        settings.web_search = cfg

    def config_path(self) -> Path:
        """Return generated managed SearXNG settings path."""
        return self._service_dir / "config" / "settings.yml"

    def logs_dir(self) -> Path:
        """Return managed SearXNG log directory."""
        return self._service_dir / "logs"

    @staticmethod
    def platform_label() -> str:
        """Return a compact platform label for diagnostics."""
        return f"{platform.system()}-{platform.machine()}"

    def _prepare_directories(self) -> None:
        for path in (self._service_dir, self._state_dir, self._service_dir / "src", self.logs_dir(), self.backups_dir()):
            path.mkdir(parents=True, exist_ok=True)

    def _clear_runtime_paths(self) -> None:
        for path in (self._source_dir(), self._venv_dir()):
            if path.exists():
                shutil.rmtree(path)

    async def _ensure_source(self, *, source_ref: str | None = None) -> bool:
        if self._source_dir().exists():
            return True
        ref = self._current_source_ref(source_ref)
        url = f"{SEARXNG_SOURCE_BASE}/{ref}.tar.gz"
        self._trace_event("source.download_started", ref=ref, url=url)
        tmp = self._temporary_archive_path()
        try:
            logger.info(f"SearXNG: downloading source archive {url}")
            await asyncio.to_thread(self._download_file, url, tmp)
            extract_root = self._service_dir / "src"
            await asyncio.to_thread(self._extract_source_archive, tmp, extract_root)
            unpacked = self._find_unpacked_source(extract_root)
            if not unpacked:
                raise RuntimeError("SearXNG source archive extracted but no searx package was found")
            if self._source_dir().exists():
                shutil.rmtree(self._source_dir())
            unpacked.rename(self._source_dir())
            self._write_state({"source_ref": ref, "source_downloaded_at": self._utc_now()})
            self._trace_event("source.download_finished", ref=ref, source_dir=str(self._source_dir()))
            return True
        except Exception as exc:
            self._last_error = f"SearXNG source download/extract failed: {exc}"
            logger.error(self._last_error)
            self._trace_event("source.download_failed", ref=ref, error=self._last_error)
            return False
        finally:
            try:
                SafePathResolver.for_application(extra_roots=[tmp.parent]).safe_unlink(tmp, purpose="searxng.cleanup_tmp", move_to_trash=False)
            except Exception:
                pass

    async def _ensure_venv(self) -> bool:
        python = self._venv_python()
        if self._venv_is_marked_ready():
            self._trace_event("venv.reused", python=str(python), marker=str(self._venv_ready_marker()))
            return True
        if self._venv_dir().exists():
            self._trace_event("venv.partial_runtime_removed", python=str(python), marker=str(self._venv_ready_marker()))
            await asyncio.to_thread(shutil.rmtree, self._venv_dir())
        self._trace_event("venv.create_started", python=str(python))
        try:
            await self._create_virtualenv()
            await self._ensure_packaging_tools()
            await self._install_searxng_package()
            self._mark_venv_ready()
            self._trace_event("venv.create_finished", python=str(python), marker=str(self._venv_ready_marker()))
            return True
        except Exception as exc:
            self._last_error = f"SearXNG virtualenv/dependency install failed: {exc}"
            logger.error(self._last_error)
            self._trace_event("venv.create_failed", error=self._last_error)
            return False

    async def _create_virtualenv(self) -> None:
        """Create the managed venv, preferring uv when it is already available."""
        uv = shutil.which("uv")
        if uv:
            self._trace_event("venv.uv_available", uv=uv)
            if await self._try_uv_venv(uv, seed=True):
                return
            if await self._try_uv_venv(uv, seed=False):
                return
        base_python = self._select_base_python()
        if not base_python:
            raise RuntimeError("No compatible Python runtime was found for managed SearXNG.")
        self._trace_event("venv.base_python_selected", python=base_python)
        await self._run_checked([base_python, "-m", "venv", "--upgrade-deps", str(self._venv_dir())], timeout=300)

    async def _try_uv_venv(self, uv: str, *, seed: bool) -> bool:
        command = [uv, "venv", "--python", "3.12"]
        if seed:
            command.append("--seed")
        command.append(str(self._venv_dir()))
        try:
            await self._run_checked(command, timeout=300)
            self._trace_event("venv.uv_finished", uv=uv, seed=seed)
            return True
        except Exception as exc:
            self._trace_event("venv.uv_failed", uv=uv, seed=seed, error=str(exc))
            if self._venv_dir().exists():
                await asyncio.to_thread(shutil.rmtree, self._venv_dir())
            return False

    async def _ensure_packaging_tools(self) -> None:
        python = self._venv_python()
        if not python.exists():
            raise RuntimeError(f"virtualenv Python was not created: {python}")
        if not await self._python_module_available("pip"):
            await self._bootstrap_pip()
        await self._run_checked([str(python), "-m", "pip", "install", "--upgrade", "pip", "wheel", "setuptools"], timeout=300)

    async def _bootstrap_pip(self) -> None:
        python = self._venv_python()
        uv = shutil.which("uv")
        if uv:
            try:
                self._trace_event("venv.pip_bootstrap_uv_started", uv=uv)
                await self._run_checked([uv, "pip", "install", "--python", str(python), "pip", "wheel", "setuptools"], timeout=300)
            except Exception as exc:
                self._trace_event("venv.pip_bootstrap_uv_failed", uv=uv, error=str(exc))
        if not await self._python_module_available("pip"):
            self._trace_event("venv.pip_bootstrap_ensurepip_started", python=str(python))
            await self._run_checked([str(python), "-m", "ensurepip", "--upgrade"], timeout=180)
        if not await self._python_module_available("pip"):
            raise RuntimeError("virtualenv was created without pip and pip bootstrap failed")

    async def _install_searxng_package(self) -> None:
        """Install SearXNG into the managed venv.

        Current SearXNG source imports ``searx`` from ``setup.py`` to read
        version/branding metadata. That import path requires runtime packages
        such as ``msgspec`` before editable build metadata can be generated. A
        plain ``pip install -e`` therefore fails on clean Linux installs with
        ``ModuleNotFoundError: msgspec``. LJS installs SearXNG's pinned
        runtime requirements first, then performs the editable install without
        build isolation so the already-installed requirements are visible.
        """
        await self._install_searxng_runtime_requirements()
        await self._install_searxng_editable_no_isolation()
        await self._verify_searxng_imports()

    async def _install_searxng_runtime_requirements(self) -> None:
        """Install SearXNG runtime requirements before editable package build."""
        requirements = self._source_dir() / "requirements.txt"
        if not requirements.exists():
            self._trace_event("venv.requirements_missing", path=str(requirements))
            raise RuntimeError(f"SearXNG requirements.txt not found: {requirements}")
        python = self._venv_python()
        try:
            self._trace_event("venv.requirements_install_started", requirements=str(requirements), installer="pip")
            await self._run_checked([str(python), "-m", "pip", "install", "-r", str(requirements)], timeout=900)
            self._trace_event("venv.requirements_install_finished", installer="pip")
            return
        except Exception as exc:
            uv = shutil.which("uv")
            if not uv:
                raise
            self._trace_event("venv.requirements_install_with_pip_failed_trying_uv", error=str(exc), uv=uv)
            await self._run_checked([uv, "pip", "install", "--python", str(python), "-r", str(requirements)], timeout=900)
            self._trace_event("venv.requirements_install_finished", installer="uv")

    async def _install_searxng_editable_no_isolation(self) -> None:
        """Install the SearXNG package after requirements are present."""
        python = self._venv_python()
        editable_command = [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-build-isolation",
            "-e",
            str(self._source_dir()),
        ]
        try:
            self._trace_event("venv.editable_install_started", installer="pip", no_build_isolation=True)
            await self._run_checked(editable_command, timeout=900)
            self._trace_event("venv.editable_install_finished", installer="pip")
            return
        except Exception as exc:
            uv = shutil.which("uv")
            if not uv:
                raise
            self._trace_event("venv.editable_install_with_pip_failed_trying_uv", error=str(exc), uv=uv)
            await self._run_checked([
                uv,
                "pip",
                "install",
                "--python",
                str(python),
                "--no-build-isolation",
                "-e",
                str(self._source_dir()),
            ], timeout=900)
            self._trace_event("venv.editable_install_finished", installer="uv")

    async def _verify_searxng_imports(self) -> None:
        """Verify the managed venv can import the modules needed to start.

        Importing ``searx.webapp`` is not a pure import in current SearXNG: it
        loads SearXNG settings and aborts when the default upstream
        ``ultrasecretkey`` is still active. During first install LJS has not yet
        called ``configure()``, so the final managed settings file may not exist
        yet. Use a temporary LJS-owned verification settings file with a random
        secret and pass it through ``SEARXNG_SETTINGS_PATH``. The real managed
        settings are still written by ``configure()`` immediately before start.
        """
        python = self._venv_python()
        settings_path = self._write_import_verification_settings()
        env = self._process_environment()
        env["SEARXNG_SETTINGS_PATH"] = str(settings_path)
        self._trace_event("venv.import_verification_started", python=str(python), settings_path=str(settings_path))
        await self._run_checked([
            str(python),
            "-c",
            "import msgspec; import searx; import searx.webapp; print('searxng-import-ok')",
        ], timeout=60, env=env)
        self._trace_event("venv.import_verification_finished")

    def _write_import_verification_settings(self) -> Path:
        """Write a temporary safe settings file for import/startup verification."""
        cfg = WebSearchConfig()
        cfg.safe_search = 1
        cfg.request_timeout_seconds = 8.0
        cfg.default_language = "auto"
        path = self._import_verification_settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self._render_settings(cfg), encoding="utf-8")
        self._trace_event("venv.import_verification_settings_written", settings_path=str(path))
        return path

    def _import_verification_settings_path(self) -> Path:
        return self._service_dir / "config" / "import-verification-settings.yml"

    async def _python_module_available(self, module_name: str) -> bool:
        python = self._venv_python()
        if not python.exists():
            return False
        proc = await asyncio.create_subprocess_exec(
            str(python),
            "-c",
            f"import {module_name}",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(proc.wait(), timeout=20)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return False
        return proc.returncode == 0

    def _venv_is_marked_ready(self) -> bool:
        return self._venv_python().exists() and self._venv_ready_marker().exists()

    def _mark_venv_ready(self) -> None:
        self._venv_ready_marker().write_text(json.dumps({"ready_at": self._utc_now()}, indent=2), encoding="utf-8")

    async def _run_checked(self, command: list[str], *, timeout: int, env: dict[str, str] | None = None) -> None:
        log_path = self.logs_dir() / "install.log"
        self._trace_event("command.started", command=self._redacted_command(command), timeout=timeout, env_overrides=[key for key in ("SEARXNG_SETTINGS_PATH", "PYTHONUNBUFFERED", "LANG", "LC_ALL") if env and key in env])
        log_path.parent.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise RuntimeError(f"command timed out: {command[0]}") from exc
        output = stdout.decode("utf-8", errors="replace") if stdout else ""
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n$ {' '.join(command)}\n{output}\n")
        if proc.returncode != 0:
            self._trace_event("command.failed", command=self._redacted_command(command), returncode=proc.returncode, log_path=str(log_path))
            raise RuntimeError(output[-1200:] or f"command exited with {proc.returncode}")
        self._trace_event("command.finished", command=self._redacted_command(command), returncode=proc.returncode, log_path=str(log_path))

    def _source_dir(self) -> Path:
        return self._service_dir / "src" / "searxng"

    def _venv_dir(self) -> Path:
        return self._service_dir / "venv"

    def _venv_ready_marker(self) -> Path:
        return self._venv_dir() / ".ljs-searxng-venv-ready.json"

    def backups_dir(self) -> Path:
        """Return the managed SearXNG backup directory."""
        return self._service_dir / "backups"

    def _venv_python(self) -> Path:
        if platform.system() == "Windows":
            return self._venv_dir() / "Scripts" / "python.exe"
        return self._venv_dir() / "bin" / "python"

    def _select_base_python(self) -> str | None:
        candidates = [sys.executable, shutil.which("python3.12"), shutil.which("python3.11"), shutil.which("python3"), shutil.which("python")]
        for candidate in candidates:
            if candidate:
                return str(candidate)
        return None

    def _process_environment(self) -> dict[str, str]:
        env = os.environ.copy()
        env["SEARXNG_SETTINGS_PATH"] = str(self.config_path())
        env.setdefault("PYTHONUNBUFFERED", "1")
        env.setdefault("LANG", "C.UTF-8")
        env.setdefault("LC_ALL", "C.UTF-8")
        return env

    def _start_command(self) -> list[str]:
        return [str(self._venv_python()), "-m", "searx.webapp"]

    def _render_settings(self, cfg: WebSearchConfig) -> str:
        secret = secrets.token_hex(32)
        safe_search = int(getattr(cfg, "safe_search", 1) or 1)
        timeout = float(getattr(cfg, "request_timeout_seconds", 8.0) or 8.0)
        language = str(getattr(cfg, "default_language", "auto") or "auto")
        return f"""use_default_settings: true

general:
  debug: false
  instance_name: "Long John Silver Search"

search:
  safe_search: {safe_search}
  autocomplete: ""
  default_lang: "{language}"
  formats:
    - html
    - json
  max_page: 2
  ban_time_on_fail: 5
  max_ban_time_on_fail: 120

server:
  port: {self._port}
  bind_address: "127.0.0.1"
  base_url: false
  secret_key: "{secret}"
  limiter: false
  public_instance: false
  image_proxy: false
  method: "GET"

valkey:
  url: false

ui:
  query_in_title: false

outgoing:
  request_timeout: {min(max(timeout / 2.0, 3.0), 8.0):.1f}
  max_request_timeout: {min(max(timeout, 5.0), 15.0):.1f}
  enable_http2: true
"""

    def _select_port(self, preferred: int) -> int:
        for port in [preferred] + list(range(SEARXNG_DEFAULT_PORT, SEARXNG_DEFAULT_PORT + 50)):
            if self._port_available(port):
                if port != preferred:
                    self._trace_event("configure.port_collision_avoided", preferred=preferred, selected=port)
                return port
        self._trace_event("configure.no_free_probe_port", preferred=preferred)
        return preferred

    @staticmethod
    def _port_available(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", int(port)))
                return True
            except OSError:
                return False

    async def _wait_for_json_health(self, settings: Settings, *, timeout_seconds: float) -> bool:
        self._trace_event("health.wait_started", timeout_seconds=timeout_seconds, url=self.url)
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            if self._process is not None and self._process.returncode is not None:
                self._last_error = f"Managed SearXNG exited early with code {self._process.returncode}. Check {self.logs_dir() / 'searxng.log'}."
                settings.web_search.status = "error"
                settings.web_search.status_message = self._last_error
                settings.web_search.last_health_check = self._utc_now()
                self._trace_event("health.wait_failed_process_exit", error=self._last_error)
                return False
            if await self._probe_json(self.url):
                self._trace_event("health.wait_finished")
                return True
            await asyncio.sleep(0.8)
        self._last_error = "Managed SearXNG did not expose JSON search before the health timeout."
        settings.web_search.status = "error"
        settings.web_search.status_message = self._last_error
        settings.web_search.last_health_check = self._utc_now()
        self._trace_event("health.wait_timeout", error=self._last_error)
        return False

    async def _probe_json(self, base_url: str) -> bool:
        self._trace_event("health.probe_started", url=base_url)
        try:
            import httpx

            async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
                response = await client.get(f"{base_url.rstrip('/')}/search", params={"q": "ljs-health-check", "format": "json"})
            if response.status_code == 403:
                self._last_error = "SearXNG is reachable, but JSON output is disabled. Enable search.formats: [html, json]."
                self._trace_event("health.probe_json_disabled", status=response.status_code, error=self._last_error)
                return False
            if response.status_code >= 400:
                self._last_error = f"SearXNG health returned HTTP {response.status_code}."
                self._trace_event("health.probe_http_failed", status=response.status_code, error=self._last_error)
                return False
            response.json()
            self._trace_event("health.probe_succeeded", status=response.status_code)
            return True
        except Exception as exc:
            self._last_error = str(exc)[:500]
            self._trace_event("health.probe_exception", error=self._last_error)
            return False

    @staticmethod
    def _download_file(url: str, target: Path) -> None:
        request = urllib.request.Request(url, headers={"User-Agent": "LongJohnSilver-SearXNG-Installer"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response, open(target, "wb") as handle:
                shutil.copyfileobj(response, handle)
        except urllib.error.URLError as exc:
            raise RuntimeError(exc) from exc

    def _extract_source_archive(self, archive: Path, target: Path) -> None:
        target.mkdir(parents=True, exist_ok=True)
        safe_extract_tar(archive, target)

    def _find_unpacked_source(self, root: Path) -> Path | None:
        for candidate in sorted(root.iterdir()):
            if candidate.is_dir() and candidate != self._source_dir() and (candidate / "searx").exists():
                return candidate
        return None

    def _temporary_archive_path(self) -> Path:
        fd, raw_path = tempfile.mkstemp(suffix="_searxng.tar.gz")
        os.close(fd)
        return Path(raw_path)

    def _backup_runtime(self, *, reason: str) -> Path:
        self._trace_event("backup.started", reason=reason)
        self._prepare_directories()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_dir = self.backups_dir() / f"{stamp}_{reason}"
        backup_dir.mkdir(parents=True, exist_ok=True)
        for name, path in {"src": self._source_dir(), "venv": self._venv_dir(), "settings.yml": self.config_path()}.items():
            if not path.exists():
                continue
            target = backup_dir / name
            if path.is_dir():
                shutil.copytree(path, target)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
        state = self._state_dir / "installed-version.json"
        if state.exists():
            shutil.copy2(state, backup_dir / "installed-version.json")
        self._write_state({"rollback_available": True, "last_backup_dir": str(backup_dir), "last_backup_reason": reason})
        self._trace_event("backup.finished", reason=reason, backup_dir=str(backup_dir))
        return backup_dir

    def _restore_runtime_backup(self, backup_dir: Path) -> None:
        self._trace_event("rollback.restore_started", backup_dir=str(backup_dir))
        if not backup_dir.exists():
            raise RuntimeError(f"Managed SearXNG backup does not exist: {backup_dir}")
        self._clear_runtime_paths()
        src_backup = backup_dir / "src"
        venv_backup = backup_dir / "venv"
        settings_backup = backup_dir / "settings.yml"
        state_backup = backup_dir / "installed-version.json"
        if src_backup.exists():
            self._source_dir().parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src_backup, self._source_dir())
        if venv_backup.exists():
            shutil.copytree(venv_backup, self._venv_dir())
        if settings_backup.exists():
            self.config_path().parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(settings_backup, self.config_path())
        if state_backup.exists():
            self._state_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(state_backup, self._state_dir / "installed-version.json")
        self._installed = self.is_installed
        self._write_state({"last_rollback_at": self._utc_now(), "restored_backup_dir": str(backup_dir)})
        self._trace_event("rollback.restore_finished", backup_dir=str(backup_dir), installed=self._installed)

    def _latest_backup_dir(self) -> Path | None:
        state_path = self._state_dir / "installed-version.json"
        try:
            state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
            explicit = Path(str(state.get("last_backup_dir") or ""))
            if explicit.exists():
                return explicit
        except Exception:
            pass
        if not self.backups_dir().exists():
            return None
        candidates = [path for path in self.backups_dir().iterdir() if path.is_dir()]
        return sorted(candidates)[-1] if candidates else None

    def _read_state(self) -> dict[str, Any]:
        path = self._state_dir / "installed-version.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write_state(self, patch: dict[str, Any]) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        path = self._state_dir / "installed-version.json"
        current = self._read_state()
        current.update(patch)
        path.write_text(json.dumps(current, indent=2, sort_keys=True), encoding="utf-8")

    def _configured_source_ref(self, cfg: WebSearchConfig | Any) -> str:
        return self._current_source_ref(getattr(cfg, "source_ref", "") or getattr(cfg, "managed_source_ref", ""))

    @staticmethod
    def _current_source_ref(source_ref: str | None) -> str:
        env_ref = os.environ.get("LJS_SEARXNG_REF", "").strip()
        return str(source_ref or env_ref or SEARXNG_FALLBACK_REF).strip() or SEARXNG_FALLBACK_REF


    def _trace_event(self, event: str, **fields: Any) -> None:
        """Write a safe SearXNG manager event to app logs and a JSONL trace file."""
        payload = {"ts": self._utc_now(), "event": event, **self._safe_trace_fields(fields)}
        try:
            logger.info("SearXNGManager event={} fields={}", event, {key: value for key, value in payload.items() if key not in {"ts", "event"}})
        except Exception:
            pass
        try:
            self.logs_dir().mkdir(parents=True, exist_ok=True)
            with (self.logs_dir() / "manager-events.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")
        except Exception:
            pass

    @staticmethod
    def _safe_trace_fields(fields: dict[str, Any]) -> dict[str, Any]:
        """Drop secret-looking trace fields and bound noisy values."""
        safe: dict[str, Any] = {}
        for key, value in fields.items():
            lower = str(key).lower()
            if any(token in lower for token in ("secret", "token", "password", "api_key", "authorization")):
                safe[key] = "<redacted>"
                continue
            if isinstance(value, str) and len(value) > 600:
                safe[key] = value[:599] + "…"
            else:
                safe[key] = value
        return safe

    @staticmethod
    def _redacted_command(command: list[str]) -> list[str]:
        """Return command arguments safe for diagnostics."""
        redacted: list[str] = []
        skip_next = False
        for part in command:
            if skip_next:
                redacted.append("<redacted>")
                skip_next = False
                continue
            lowered = str(part).lower()
            redacted.append(str(part))
            if lowered in {"--password", "--token", "--api-key", "--secret"}:
                skip_next = True
        return redacted

    def _close_log_handle(self) -> None:
        if self._log_handle:
            try:
                self._log_handle.close()
            except Exception:
                pass
            self._log_handle = None

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()
