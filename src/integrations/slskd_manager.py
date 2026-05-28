"""Managed slskd runtime for LJS.

LJS treats Soulseek support like Jackett: if the user enables the companion
source, the application downloads a native slskd binary, writes the slskd.yml
configuration from LJS settings, starts it with the app, and stops it on app
shutdown.  Docker is deliberately not required.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import secrets
import shutil
import subprocess
import tempfile
import uuid
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from src.core.models import Settings
from src.core.security.command_policy import CommandPolicy, CommandPolicyError
from src.core.security.path_policy import SafePathResolver
from src.integrations.slskd_client import SlskdClient
from src.integrations.slskd_config import build_slskd_share_plan, render_slskd_yaml
from src.utils.archive_safety import safe_extract_zip

SLSKD_PORT = 5030
SLSKD_RELEASE_API = "https://api.github.com/repos/slskd/slskd/releases/latest"
SLSKD_FALLBACK_VERSION = "0.25.1"
SLSKD_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "slskd"
SLSKD_BIN_DIR = SLSKD_DIR / "bin"


class SlskdManager:
    """Install, configure, start, stop, and health-check the bundled slskd runtime."""

    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None
        self._installed = False
        self._running = False
        self._last_error: str | None = None
        self._version: str | None = None
        self._last_connection_status: dict[str, Any] = {}
        self._running_credentials_key: tuple[str, str] | None = None
        self._start_lock = asyncio.Lock()
        self._adopted_external = False

    @property
    def is_installed(self) -> bool:
        """Return whether a managed slskd executable is present on disk.

        Keep this property cheap because setup/status endpoints may call it from
        the event loop.  The expensive executable smoke test is performed inside
        ensure_installed(), where it can run off-thread.
        """
        return self._installed or self._detect_existing()

    @property
    def is_running(self) -> bool:
        """Return whether this LJS process currently owns a live slskd subprocess."""
        return bool(self._adopted_external or (self._running and self._process is not None and self._process.returncode is None))

    @property
    def url(self) -> str:
        """Return the local managed slskd HTTP/API endpoint."""
        return f"http://127.0.0.1:{SLSKD_PORT}"

    @property
    def last_error(self) -> str | None:
        """Return the most recent install/start/configuration error, if any."""
        return self._last_error

    async def ensure_installed(self, force: bool = False) -> bool:
        """Download and extract a platform-specific slskd binary if needed."""
        if not force and self._detect_existing():
            exe = self._executable_path()
            if exe and await self._binary_is_runnable_async(exe):
                self._installed = True
                return True
            logger.warning("slskd: existing binary is not runnable on this platform; reinstalling")
            self._clear_bin_dir()
        if not self.platform_supported():
            self._last_error = f"Unsupported slskd platform: {platform.system()}-{platform.machine()}"
            logger.error(self._last_error)
            return False

        SLSKD_BIN_DIR.mkdir(parents=True, exist_ok=True)
        asset_urls = await self._candidate_asset_urls()
        if not asset_urls:
            self._last_error = "Could not resolve a compatible slskd release asset."
            logger.error(self._last_error)
            return False

        last_error: Exception | None = None
        for url in asset_urls:
            tmp = Path(tempfile.mktemp(suffix="_slskd.zip"))
            try:
                logger.info(f"slskd: downloading {url}")
                await asyncio.to_thread(self._download_file, url, str(tmp))
                self._clear_bin_dir()
                await asyncio.to_thread(self._extract_zip, tmp, SLSKD_BIN_DIR)
                exe = self._executable_path()
                if exe:
                    if platform.system() != "Windows":
                        exe.chmod(exe.stat().st_mode | 0o111)
                    if not await self._binary_is_runnable_async(exe):
                        raise RuntimeError(f"slskd binary from {url} is not runnable on this system")
                    self._installed = True
                    self._last_error = None
                    logger.info(f"slskd: installed at {exe}")
                    return True
                self._last_error = "slskd archive extracted but executable was not found."
                logger.error(self._last_error)
            except Exception as exc:
                last_error = exc
                logger.warning(f"slskd: failed to install from {url}: {exc}")
            finally:
                try:
                    SafePathResolver.for_application(extra_roots=[tmp.parent]).safe_unlink(
                        tmp, purpose="slskd.cleanup_tmp", move_to_trash=False,
                    )
                except Exception:
                    pass
        self._last_error = str(last_error or "slskd install failed")[:500]
        return False

    async def configure(self, settings: Settings) -> bool:
        """Write slskd.yml from LJS settings, generating local secrets as needed."""
        cfg = settings.soulseek
        if not cfg.enabled:
            self._last_error = "Soulseek/slskd is disabled in settings."
            return False
        if not cfg.soulseek_username or not cfg.soulseek_password:
            cfg.account_status = "needs_credentials"
            cfg.account_status_message = "Soulseek username and password are required before LJS can start slskd."
            cfg.account_checked_at = self._utc_now()
            self._last_error = cfg.account_status_message
            return False

        cfg.host = self.url
        cfg.url_base = "/"
        cfg.managed = True
        if not cfg.api_key:
            cfg.api_key = secrets.token_hex(32)
        if not cfg.web_username:
            cfg.web_username = "ljs"
        if not cfg.web_password:
            cfg.web_password = secrets.token_urlsafe(24)
        if not cfg.jwt_key:
            cfg.jwt_key = secrets.token_hex(32)

        app_dir = self._resolve_path(cfg.app_dir)
        config_path = self.config_path(settings)
        raw_downloads_dir = str(getattr(cfg, "downloads_dir", "") or "")
        raw_incomplete_dir = str(getattr(cfg, "incomplete_dir", "") or "")

        app_error = self._preflight_single_directory("application", app_dir)
        if app_error:
            cfg.account_status = "error"
            cfg.account_status_message = app_error
            cfg.account_checked_at = self._utc_now()
            self._last_error = app_error
            logger.error(app_error)
            return False

        downloads_dir, incomplete_dir, selection_reason, directory_mode, runtime_app_dir = self._select_managed_download_directories(settings)
        cfg.managed_directory_mode = "explicit"
        cfg.managed_runtime_app_dir = ""
        cfg.downloads_dir = str(downloads_dir)
        cfg.incomplete_dir = str(incomplete_dir)
        plan = build_slskd_share_plan(settings)
        logger.info(
            "slskd managed path plan selected: "
            f"settings.download_dir={getattr(settings, 'download_dir', '')!r} "
            f"settings.library_root={getattr(settings, 'library_root', '')!r} "
            f"app_dir={app_dir} config_path={config_path} "
            f"raw_soulseek.downloads_dir={raw_downloads_dir!r} "
            f"raw_soulseek.incomplete_dir={raw_incomplete_dir!r} "
            f"effective_downloads_dir={plan.downloads_dir} effective_incomplete_dir={plan.incomplete_dir} "
            f"selection_reason={selection_reason!r} directory_mode={directory_mode!r} "
            f"runtime_app_dir={runtime_app_dir}"
        )

        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(render_slskd_yaml(settings, redact_secrets=False), encoding="utf-8")
            logger.info(f"slskd managed config written: {config_path}")
        except Exception as exc:
            message = f"Soulseek/slskd cannot write managed config: {config_path} ({exc})"
            cfg.account_status = "error"
            cfg.account_status_message = message
            cfg.account_checked_at = self._utc_now()
            self._last_error = message
            logger.error(message)
            return False

        cfg.account_status = "checking"
        cfg.account_status_message = "Waiting for slskd to authenticate with the Soulseek network."
        cfg.account_checked_at = self._utc_now()
        self._last_error = None
        return True

    async def _start_impl(self, settings: Settings, *, login_timeout_seconds: float = 90.0) -> bool:
        """Start the managed slskd process, installing/configuring first if needed."""
        if self.is_running:
            if self._running_credentials_key != self._credentials_key(settings):
                logger.info("slskd credentials changed; restarting managed runtime so the Soulseek login is applied.")
                await self.stop()
            else:
                account = await self.validate_account(settings, timeout_seconds=5)
                return account.get("status") == "ready"
        if not getattr(settings.soulseek, "enabled", False):
            self._last_error = "Soulseek/slskd is disabled."
            return False
        if not await self.ensure_installed():
            return False
        if not await self.configure(settings):
            await self._quarantine_reachable_stale_instance(settings, reason="managed configuration/preflight failed")
            return False

        # Do not blindly adopt a process already bound to the managed slskd port.
        # The latest real failures came from a stale slskd instance that was still
        # using an older project-local downloads directory even after LJS wrote a
        # corrected config.  Managed mode means this LJS process owns the runtime;
        # a pre-existing reachable runtime must be stopped and restarted with the
        # just-written config, or refused loudly.
        if await self._api_reachable(settings):
            stopped = await self._stop_reachable_stale_instance(settings, reason="managed slskd start found an already-running API")
            if not stopped:
                message = (
                    "Soulseek/slskd refused to start because another slskd process is already reachable at "
                    f"{self.url}. LJS will not adopt it because it may still be using stale download paths. "
                    "Stop the existing slskd process and start LJS again."
                )
                settings.soulseek.account_status = "error"
                settings.soulseek.account_status_message = message
                settings.soulseek.account_checked_at = self._utc_now()
                self._last_error = message
                logger.error(message)
                return False

        exe = self._executable_path()
        if not exe:
            self._last_error = "slskd executable not found."
            return False
        app_dir = self._runtime_app_dir(settings)
        config_path = self.config_path(settings)
        env = self._start_environment(settings, app_dir, config_path)
        args = self._start_args(exe, app_dir, config_path, settings)
        plan = build_slskd_share_plan(settings)
        logger.info(
            "slskd launching managed process: "
            f"exe={exe} app_dir={app_dir} config_path={config_path} "
            f"downloads_dir={plan.downloads_dir} incomplete_dir={plan.incomplete_dir} "
            f"argv={args}"
        )
        log_path = self.log_path(settings)
        self._prepare_fresh_start_log(settings, app_dir=app_dir, config_path=config_path, args=args, env=env)
        log_file = log_path.open("ab", buffering=0)
        try:
            self._process = await CommandPolicy().create_subprocess_exec(
                args,
                purpose="slskd.start",
                approved=True,
                # Keep the subprocess working directory on stable local LJS
                # storage.  ``--app-dir``/``APP_DIR`` controls slskd's own
                # application directory; using the download mount as cwd made
                # startup sensitive to autofs/NFS quirks before slskd could even
                # parse its explicit config.
                cwd=str(self._resolve_path(settings.soulseek.app_dir)),
                env=env,
                stdout=log_file,
                stderr=log_file,
            )
        except CommandPolicyError as exc:
            log_file.close()
            self._last_error = f"slskd start blocked by security policy: {exc}"
            settings.soulseek.account_status = "error"
            settings.soulseek.account_status_message = self._last_error
            settings.soulseek.account_checked_at = self._utc_now()
            logger.error(self._last_error)
            return False
        except Exception as exc:
            log_file.close()
            self._last_error = f"slskd failed to start: {exc}"
            settings.soulseek.account_status = "error"
            settings.soulseek.account_status_message = self._last_error
            settings.soulseek.account_checked_at = self._utc_now()
            logger.error(self._last_error)
            return False
        else:
            log_file.close()

        try:
            log_path.touch(exist_ok=True)
        except Exception:
            pass

        cfg = settings.soulseek
        startup_checks = max(1, int(float(login_timeout_seconds or 90.0)))
        for _ in range(startup_checks):
            await asyncio.sleep(1)
            if self._process.returncode is not None:
                self._running = False
                cfg.account_status = "error"
                cfg.account_status_message = self._format_startup_exit_message(settings, self._process.returncode)
                cfg.account_checked_at = self._utc_now()
                self._last_error = cfg.account_status_message
                logger.error(self._last_error)
                self._process = None
                return False
            account = await self.validate_account(settings, timeout_seconds=0)
            if account.get("status") == "ready":
                self._running = True
                self._adopted_external = False
                self._running_credentials_key = self._credentials_key(settings)
                self._last_error = None
                logger.info(f"slskd: running and authenticated at {self.url}")
                return True
            if account.get("status") == "auth_failed":
                self._running = False
                self._last_error = account.get("error") or cfg.account_status_message
                await self.stop()
                return False
            if account.get("api_reachable") is False:
                continue
        self._running = self._process is not None and self._process.returncode is None
        if self._running:
            self._running_credentials_key = self._credentials_key(settings)
        cfg.account_status = "checking" if self._running else "error"
        cfg.account_status_message = "slskd is running, but LJS has not confirmed Soulseek account login yet. Check again shortly; if this persists, verify the credentials."
        cfg.account_checked_at = self._utc_now()
        self._last_error = cfg.account_status_message
        logger.warning(self._last_error)
        return False

    async def start(self, settings: Settings, *, login_timeout_seconds: float = 90.0) -> bool:
        """Safely start managed slskd without allowing runtime errors to crash LJS."""
        async with self._start_lock:
            try:
                return await self._start_impl(settings, login_timeout_seconds=login_timeout_seconds)
            except Exception as exc:
                self._running = False
                self._last_error = f"slskd startup failed: {exc}"
                try:
                    settings.soulseek.account_status = "error"
                    settings.soulseek.account_status_message = self._last_error
                    settings.soulseek.account_checked_at = self._utc_now()
                except Exception:
                    pass
                logger.exception(self._last_error)
                return False

    async def _validate_account_impl(self, settings: Settings, *, timeout_seconds: float = 30.0) -> dict[str, Any]:
        """Validate that slskd is logged in to the Soulseek network.

        A running slskd HTTP API is not enough.  LJS only reports Soulseek as
        ready when the network account is confirmed, or reports a precise
        recoverable status when credentials are missing/rejected.
        """
        cfg = settings.soulseek
        if not cfg.soulseek_username or not cfg.soulseek_password:
            cfg.account_status = "needs_credentials"
            cfg.account_status_message = "Soulseek username and password are required. Use an existing account, or try a new unique username/password."
            cfg.account_checked_at = self._utc_now()
            self._last_connection_status = {"status": cfg.account_status, "error": cfg.account_status_message}
            return {"status": cfg.account_status, "ready": False, "running": self.is_running, "error": cfg.account_status_message}

        deadline = asyncio.get_event_loop().time() + max(0.0, float(timeout_seconds or 0.0))
        first = True
        last_status: dict[str, Any] = {}
        while first or asyncio.get_event_loop().time() <= deadline:
            first = False
            log_text = self._recent_log_text(settings)
            status = await SlskdClient(cfg).connection_status(log_text=log_text)
            last_status = dict(status)
            if status.get("credentials_rejected"):
                cfg.account_status = "auth_failed"
                cfg.account_status_message = status.get("error") or "Soulseek rejected these credentials."
                cfg.account_checked_at = self._utc_now()
                self._last_connection_status = {**status, "status": cfg.account_status, "ready": False}
                self._last_error = cfg.account_status_message
                return {**self._last_connection_status, "error": cfg.account_status_message}
            if status.get("authenticated_to_soulseek"):
                cfg.account_status = "ready"
                cfg.account_status_message = "Soulseek account authenticated."
                cfg.account_checked_at = self._utc_now()
                self._last_connection_status = {**status, "status": cfg.account_status, "ready": True, "error": ""}
                self._last_error = None
                return self._last_connection_status
            if timeout_seconds <= 0:
                break
            await asyncio.sleep(1)

        cfg.account_status = "checking" if last_status.get("api_reachable") else "error"
        cfg.account_status_message = last_status.get("error") or "Waiting for slskd to confirm Soulseek network login."
        cfg.account_checked_at = self._utc_now()
        self._last_connection_status = {**last_status, "status": cfg.account_status, "ready": False, "error": cfg.account_status_message}
        self._last_error = cfg.account_status_message if cfg.account_status == "error" else None
        return self._last_connection_status

    async def validate_account(self, settings: Settings, *, timeout_seconds: float = 30.0) -> dict[str, Any]:
        """Safely validate Soulseek login without raising through API/startup paths."""
        try:
            return await self._validate_account_impl(settings, timeout_seconds=timeout_seconds)
        except Exception as exc:
            message = f"Soulseek login validation failed: {exc}"
            self._last_error = message
            try:
                settings.soulseek.account_status = "error"
                settings.soulseek.account_status_message = message
                settings.soulseek.account_checked_at = self._utc_now()
            except Exception:
                pass
            logger.exception(message)
            self._last_connection_status = {
                "status": "error",
                "ready": False,
                "api_reachable": False,
                "authenticated_to_soulseek": False,
                "credentials_rejected": False,
                "error": message,
            }
            return self._last_connection_status

    async def stop(self) -> None:
        """Stop the slskd subprocess owned by this LJS process."""
        if self._process and self._process.returncode is None:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=10)
            except asyncio.TimeoutError:
                self._process.kill()
            except Exception:
                pass
        self._running = False
        self._adopted_external = False
        self._process = None
        self._running_credentials_key = None

    async def _api_reachable(self, settings: Settings) -> bool:
        """Return whether a slskd API is currently reachable at the managed endpoint."""
        try:
            state = await SlskdClient(settings.soulseek).state()
            if not isinstance(state, dict):
                return True
            return state.get("error_code") not in {"SLSKD_UNREACHABLE", "SLSKD_NOT_CONFIGURED"}
        except Exception as exc:
            logger.debug(f"slskd API reachability probe failed: {exc}")
            return False

    async def _quarantine_reachable_stale_instance(self, settings: Settings, *, reason: str) -> None:
        """Best-effort stop for a reachable stale slskd when managed startup cannot continue."""
        if await self._api_reachable(settings):
            await self._stop_reachable_stale_instance(settings, reason=reason)

    async def _stop_reachable_stale_instance(self, settings: Settings, *, reason: str) -> bool:
        """Best-effort stop of a pre-existing slskd process before managed launch."""
        client = SlskdClient(settings.soulseek)
        logger.warning(
            "slskd reachable before managed launch; refusing blind adoption and requesting stop: "
            f"reason={reason} host={settings.soulseek.host} downloads_dir={settings.soulseek.downloads_dir} "
            f"incomplete_dir={settings.soulseek.incomplete_dir}"
        )
        result = await client.stop_application()
        if not (isinstance(result, dict) and result.get("ok")):
            logger.error(f"slskd stale-instance stop failed: {result}")
            return False
        for _ in range(10):
            await asyncio.sleep(1)
            if not await self._api_reachable(settings):
                logger.info("slskd stale instance stopped before managed restart")
                return True
        logger.error("slskd stop request succeeded but API was still reachable after 10 seconds")
        return False


    def _prepare_fresh_start_log(self, settings: Settings, *, app_dir: Path, config_path: Path, args: list[str], env: dict[str, str]) -> None:
        """Rotate stale slskd output and write a redacted launch marker.

        Appending every managed start to the same slskd.log made failures look
        like they still referenced old paths: the bounded tail could contain
        previous downloads and previous invalid-configuration errors.  Keep one
        previous log for context, but make the active log belong to the current
        launch so startup errors cannot be diagnosed from stale evidence.
        """
        log_path = self.log_path(settings)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        previous_path = log_path.with_name(log_path.stem + ".previous" + log_path.suffix)
        try:
            if log_path.exists() and log_path.stat().st_size > 0:
                try:
                    previous_path.unlink(missing_ok=True)
                except Exception:
                    pass
                log_path.replace(previous_path)
        except Exception as exc:
            logger.warning(f"Could not rotate stale slskd log before managed start: {log_path} ({exc})")
        safe_env = {
            key: env[key]
            for key in ("APP_DIR", "SLSKD_CONFIG", "SLSKD_DOWNLOADS_DIR", "SLSKD_INCOMPLETE_DIR", "SLSKD_APP_DIR")
            if key in env
        }
        marker = (
            "===== LJS managed slskd launch =====\n"
            f"time={self._utc_now()}\n"
            f"app_dir={app_dir}\n"
            f"config_path={config_path}\n"
            f"settings.download_dir={getattr(settings, 'download_dir', '')}\n"
            f"settings.library_root={getattr(settings, 'library_root', '')}\n"
            f"soulseek.downloads_dir={getattr(settings.soulseek, 'downloads_dir', '')}\n"
            f"soulseek.incomplete_dir={getattr(settings.soulseek, 'incomplete_dir', '')}\n"
            f"soulseek.managed_directory_mode={getattr(settings.soulseek, 'managed_directory_mode', '')}\n"
            f"argv={args}\n"
            f"dir_env={safe_env}\n"
            "====================================\n"
        )
        try:
            log_path.write_text(marker, encoding="utf-8")
        except Exception as exc:
            logger.warning(f"Could not write managed slskd launch marker: {log_path} ({exc})")

    async def health_check(self, settings: Settings | None = None) -> dict[str, Any]:
        """Return setup/runtime status for the UI."""
        settings = settings or Settings()
        cfg = settings.soulseek
        result: dict[str, Any] = {
            "installed": self.is_installed,
            "running": self.is_running,
            "enabled": bool(cfg.enabled),
            "managed": bool(getattr(cfg, "managed", True)),
            "supported_platform": self.platform_supported(),
            "url": self.url if (self.is_running or cfg.enabled) else None,
            "api_key_available": bool(cfg.api_key),
            "credentials_configured": bool(cfg.soulseek_username and cfg.soulseek_password),
            "last_error": self._last_error,
            "error": self._last_error,
        }
        if cfg.enabled and cfg.managed and self._last_error and not self.is_running:
            cfg.account_status = "error"
            cfg.account_status_message = self._last_error
            cfg.account_checked_at = self._utc_now()
        result["account_status"] = cfg.account_status
        result["account_status_message"] = cfg.account_status_message
        result["account_ready"] = cfg.account_ready
        result["account_checked_at"] = cfg.account_checked_at
        if result["running"] and cfg.api_configured:
            account = await self.validate_account(settings, timeout_seconds=0)
            result["api_reachable"] = account.get("api_reachable", False)
            result["account_status"] = cfg.account_status
            result["account_status_message"] = cfg.account_status_message
            result["account_ready"] = cfg.account_ready
            result["account_checked_at"] = cfg.account_checked_at
            if account.get("error"):
                result["error"] = account.get("error") or result.get("error")
        return result

    def save_to_settings(self, settings: Settings) -> None:
        """Persist local managed slskd connection values into LJS settings."""
        settings.soulseek.host = self.url
        settings.soulseek.url_base = "/"
        settings.soulseek.managed = True

    def _select_managed_download_directories(self, settings: Settings) -> tuple[Path, Path, str, str, Path]:
        """Return the managed slskd payload folders.

        This deliberately restores the working storage topology from the early
        successful downloads: APP_DIR stays local LJS state, completed Soulseek
        files are written directly into ``settings.download_dir``, and partials
        live in ``settings.download_dir/.slskd-incomplete``.

        Do not invent ``Soulseek/`` or ``downloads/`` children here.  The user
        chose the download root because it may be the only disk with enough
        space.  Directory/write probes are forensic only; slskd remains the
        runtime authority for whether it can use its configured directories.
        """
        download_root = self._resolve_path(getattr(settings, "download_dir", "./downloads"))
        incomplete = (download_root / ".slskd-incomplete").resolve(strict=False)
        cfg = getattr(settings, "soulseek", None)
        raw_downloads = str(getattr(cfg, "downloads_dir", "") or "").strip() if cfg is not None else ""
        raw_incomplete = str(getattr(cfg, "incomplete_dir", "") or "").strip() if cfg is not None else ""
        raw_mode = str(getattr(cfg, "managed_directory_mode", "explicit") or "explicit").strip().lower() if cfg is not None else "explicit"

        logger.info(
            "slskd managed storage plan: using configured LJS download root directly; "
            f"download_root=({self._path_diagnostics(download_root)}) "
            f"incomplete={incomplete} raw_mode={raw_mode!r} "
            f"raw_downloads={raw_downloads!r} raw_incomplete={raw_incomplete!r} "
            "invariant='APP_DIR is local state; payload bytes stay under settings.download_dir'"
        )

        # Create only what is required and do not write-probe the user's
        # download mount.  The early Soulseek runs proved slskd could write real
        # media there; repeated synthetic temp-file writes/fsyncs added noise and
        # can aggravate fragile USB/autofs mounts.  slskd itself remains the
        # runtime authority for validating its configured directories.
        for label, folder in (("downloads", download_root), ("incomplete", incomplete)):
            try:
                folder.mkdir(parents=True, exist_ok=True)
                logger.info(
                    "slskd managed storage directory present/prepared without write probe: "
                    f"label={label} diagnostics=({self._path_diagnostics(folder)})"
                )
            except Exception as exc:
                logger.warning(
                    "slskd managed storage mkdir failed; continuing so slskd can validate the configured path itself: "
                    f"label={label} path={folder} parent=({self._path_diagnostics(folder.parent)}) error={exc!r}"
                )

        return download_root, incomplete, "managed-direct-download-root", "explicit", Path("")

    def _candidate_directory_is_writable(self, label: str, folder: Path, *, reason: str) -> bool:
        """Create and write-probe a candidate directory for actual slskd use."""
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            logger.error(
                "slskd storage candidate mkdir failed: "
                f"reason={reason} label={label} path={folder} parent=({self._path_diagnostics(folder.parent)}) error={exc!r}"
            )
            return False
        logger.info(
            "slskd storage candidate directory prepared: "
            f"reason={reason} label={label} diagnostics=({self._path_diagnostics(folder)})"
        )
        probe = self._write_probe_directory(folder)
        if probe.get("ok"):
            logger.info(
                "slskd storage candidate write probe ok: "
                f"reason={reason} label={label} path={folder} filename={probe.get('filename')!r} bytes={probe.get('bytes')}"
            )
            return True
        logger.warning(
            "slskd storage candidate write probe failed: "
            f"reason={reason} label={label} path={folder} result={probe} diagnostics=({self._path_diagnostics(folder)})"
        )
        return False

    @staticmethod
    def _path_within_or_equal(path: Path, root: Path) -> bool:
        try:
            path.resolve(strict=False).relative_to(root.resolve(strict=False))
            return True
        except Exception:
            try:
                path_s = str(path.resolve(strict=False)).replace("\\", "/").rstrip("/")
                root_s = str(root.resolve(strict=False)).replace("\\", "/").rstrip("/")
                return path_s == root_s or path_s.startswith(root_s + "/")
            except Exception:
                return False

    def _prepare_managed_directories(self, app_dir: Path, downloads_dir: Path, incomplete_dir: Path) -> str | None:
        """Create required slskd directories without stress-testing payload storage.

        ``app_dir`` is local LJS state and can be write-probed.  ``downloads`` and
        ``incomplete`` can be external USB/NAS/autofs paths selected specifically
        because they have capacity.  Do not perform synthetic write/fsync probes
        there; repeated probes are not useful once slskd is the runtime authority
        and they can worsen a fragile mount under concurrent transfers/imports.
        """
        if downloads_dir == incomplete_dir:
            return "Soulseek/slskd cannot start because downloads_dir and incomplete_dir resolve to the same folder."
        error = self._ensure_directory_for_slskd("application", app_dir)
        if error:
            return error
        for label, folder in (("downloads", downloads_dir), ("incomplete", incomplete_dir)):
            try:
                folder.mkdir(parents=True, exist_ok=True)
                logger.info(
                    "slskd payload directory prepared without write probe: "
                    f"label={label} diagnostics=({self._path_diagnostics(folder)})"
                )
            except Exception as exc:
                logger.warning(
                    "slskd payload directory mkdir failed; continuing so slskd can validate it: "
                    f"label={label} path={folder} parent=({self._path_diagnostics(folder.parent)}) error={exc!r}"
                )
        return None

    def _ensure_directory_for_slskd(self, label: str, folder: Path) -> str | None:
        """Ensure one slskd directory exists and log path/mount/write diagnostics."""
        logger.info(f"slskd directory prepare start: label={label} path={folder}")
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            logger.error(
                "slskd directory creation failed: "
                f"label={label} path={folder} parent_probe=({self._path_diagnostics(folder.parent)}) error={exc!r}"
            )
            return f"Soulseek/slskd cannot start because the {label} directory could not be created: {folder} ({exc})"

        logger.info(f"slskd directory prepared: label={label} probe=({self._path_diagnostics(folder)})")
        probe = self._write_probe_directory(folder)
        if not isinstance(probe, dict):
            probe = {"ok": probe is None, "legacy_result": probe}
        if probe.get("ok"):
            logger.info(
                "slskd directory write probe ok: "
                f"label={label} path={folder} filename={probe.get('filename')!r} bytes={probe.get('bytes')}"
            )
        else:
            logger.warning(
                "slskd directory write probe failed; continuing to let slskd validate the configured directory: "
                f"label={label} path={folder} result={probe} diagnostics=({self._path_diagnostics(folder)})"
            )
        return None

    @classmethod
    def _write_probe_directory(cls, folder: Path) -> dict[str, Any]:
        """Return diagnostic write-test details without deciding startup fate."""
        attempts = []
        for prefix in (".ljs-slskd-write-test", "ljs-slskd-write-test"):
            probe = folder / f"{prefix}-{os.getpid()}-{uuid.uuid4().hex}.tmp"
            try:
                with probe.open("wb") as handle:
                    handle.write(b"ljs slskd write test\n")
                    handle.flush()
                    try:
                        os.fsync(handle.fileno())
                    except OSError as fsync_exc:
                        attempts.append({
                            "filename": probe.name,
                            "stage": "fsync",
                            "ok": True,
                            "fsync_warning": repr(fsync_exc),
                        })
                size = 0
                try:
                    size = probe.stat().st_size
                except Exception:
                    pass
                try:
                    probe.unlink(missing_ok=True)
                except Exception as unlink_exc:
                    attempts.append({"filename": probe.name, "stage": "unlink", "ok": False, "error": repr(unlink_exc)})
                return {"ok": True, "filename": probe.name, "bytes": size, "attempts": attempts}
            except Exception as exc:
                attempts.append({"filename": probe.name, "stage": "open_write", "ok": False, "error": repr(exc)})
                try:
                    probe.unlink(missing_ok=True)
                except Exception as unlink_exc:
                    attempts.append({"filename": probe.name, "stage": "cleanup_after_error", "ok": False, "error": repr(unlink_exc)})
        return {"ok": False, "attempts": attempts}

    @classmethod
    def _path_diagnostics(cls, path: Path) -> str:
        """Return compact forensic details for path/ownership/mount debugging."""
        parts: list[str] = [f"path={path}"]
        try:
            resolved = path.expanduser().resolve(strict=False)
            parts.append(f"resolved={resolved}")
        except Exception as exc:
            resolved = path
            parts.append(f"resolve_error={exc!r}")
        try:
            parts.append(f"exists={path.exists()}")
        except Exception as exc:
            parts.append(f"exists_error={exc!r}")
        try:
            parts.append(f"is_dir={path.is_dir()}")
        except Exception as exc:
            parts.append(f"is_dir_error={exc!r}")
        try:
            st = path.stat()
            parts.extend([
                f"mode={oct(st.st_mode & 0o7777)}",
                f"uid={getattr(st, 'st_uid', 'n/a')}",
                f"gid={getattr(st, 'st_gid', 'n/a')}",
                f"dev={getattr(st, 'st_dev', 'n/a')}",
            ])
        except Exception as exc:
            parts.append(f"stat_error={exc!r}")
        try:
            vfs = os.statvfs(path)
            parts.extend([
                f"free_bytes={int(vfs.f_bavail) * int(vfs.f_frsize)}",
                f"total_bytes={int(vfs.f_blocks) * int(vfs.f_frsize)}",
            ])
        except Exception as exc:
            parts.append(f"statvfs_error={exc!r}")
        mount = cls._mount_info_for_path(resolved)
        if mount:
            parts.append(f"mount={mount}")
        return " ".join(parts)

    @staticmethod
    def _mount_info_for_path(path: Path) -> str:
        """Best-effort Linux mount description for diagnostics."""
        proc_mounts = Path("/proc/mounts")
        if not proc_mounts.exists():
            return ""
        try:
            target = str(path.resolve(strict=False))
        except Exception:
            target = str(path)
        best: tuple[int, str] | None = None
        try:
            for line in proc_mounts.read_text(encoding="utf-8", errors="replace").splitlines():
                fields = line.split()
                if len(fields) < 4:
                    continue
                device, mountpoint, fstype, options = fields[:4]
                mountpoint = mountpoint.replace("\\040", " ")
                if target == mountpoint or target.startswith(mountpoint.rstrip("/") + "/"):
                    score = len(mountpoint)
                    text = f"device={device} mountpoint={mountpoint} fstype={fstype} options={options}"
                    if best is None or score > best[0]:
                        best = (score, text)
        except Exception:
            return ""
        return best[1] if best else ""

    # Backward-compatible names retained for older tests/scripts that inspect
    # these helpers directly.
    def _preflight_managed_directories(self, app_dir: Path, downloads_dir: Path, incomplete_dir: Path) -> str | None:
        return self._prepare_managed_directories(app_dir, downloads_dir, incomplete_dir)

    def _preflight_single_directory(self, label: str, folder: Path) -> str | None:
        return self._ensure_directory_for_slskd(label, folder)

    def log_path(self, settings: Settings) -> Path:
        """Return the managed slskd startup/runtime log path."""
        return self._resolve_path(settings.soulseek.app_dir) / "slskd.log"

    def _recent_log_text(self, settings: Settings, *, max_bytes: int = 120_000) -> str:
        """Return a bounded tail of the managed slskd log for login diagnostics."""
        try:
            path = self.log_path(settings)
            if not path.exists():
                return ""
            data = path.read_bytes()[-max_bytes:]
            return data.decode("utf-8", errors="replace")
        except Exception:
            return ""

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _credentials_key(settings: Settings) -> tuple[str, str]:
        """Return the credential identity currently applied to managed slskd.

        The actual password is never logged or exposed.  A short keyed marker is
        enough to detect that the configured Soulseek login changed and the
        daemon must be restarted so slskd rereads the generated config/env.
        """
        cfg = settings.soulseek
        username = str(getattr(cfg, "soulseek_username", "") or "").strip()
        password = str(getattr(cfg, "soulseek_password", "") or "")
        return (username, hashlib.sha256(password.encode("utf-8", errors="ignore")).hexdigest())

    def config_path(self, settings: Settings) -> Path:
        """Return the managed slskd YAML configuration path for current settings."""
        return self._resolve_path(settings.soulseek.app_dir) / "slskd.yml"

    def _runtime_app_dir(self, settings: Settings) -> Path:
        """Return the APP_DIR to pass to slskd for this managed launch.

        APP_DIR is slskd application state, not payload storage.  Keep it on
        stable local LJS data.  Completed/incomplete Soulseek files are routed to
        the user-selected download root through explicit --downloads and
        --incomplete paths.
        """
        return self._resolve_path(settings.soulseek.app_dir)


    def _start_environment(self, settings: Settings, app_dir: Path, config_path: Path) -> dict[str, str]:
        """Return a controlled environment for the managed slskd process.

        slskd's configuration hierarchy is:

            defaults < environment < YAML < command line < run-time overlay

        The earlier managed runtime copied ``os.environ`` wholesale.  That was
        unsafe: stale user/session values such as ``SLSKD_DOWNLOADS_DIR`` and
        ``SLSKD_INCOMPLETE_DIR`` could override the current plan.  In managed
        mode LJS owns every slskd option, so inherited ``SLSKD_*`` and ``APP_DIR``
        values are scrubbed first and then only the values computed from current
        LJS settings are added back.
        """
        cfg = settings.soulseek
        plan = build_slskd_share_plan(settings)
        inherited_slskd_keys = sorted(key for key in os.environ if key.upper().startswith("SLSKD_") or key.upper() == "APP_DIR")
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.upper().startswith("SLSKD_") and key.upper() != "APP_DIR"
        }
        env.update({
            "APP_DIR": str(app_dir),
            "SLSKD_CONFIG": str(config_path),
            "SLSKD_HTTP_PORT": str(SLSKD_PORT),
            "SLSKD_HTTP_IP_ADDRESS": "127.0.0.1",
            "SLSKD_API_KEY": cfg.api_key or "",
            "SLSKD_USERNAME": cfg.web_username or "ljs",
            "SLSKD_PASSWORD": cfg.web_password or "",
            "SLSKD_JWT_KEY": cfg.jwt_key or "",
            "SLSKD_SLSK_USERNAME": cfg.soulseek_username or "",
            "SLSKD_SLSK_PASSWORD": cfg.soulseek_password or "",
            "SLSKD_DOWNLOADS_DIR": str(plan.downloads_dir),
            "SLSKD_INCOMPLETE_DIR": str(plan.incomplete_dir),
        })
        # Kept for backward compatibility with older experiments and harmless if
        # ignored by slskd.  The documented variable is APP_DIR.
        env["SLSKD_APP_DIR"] = str(app_dir)
        logger.info(
            "slskd managed environment prepared: "
            f"directory_mode='explicit' stripped_parent_keys={inherited_slskd_keys} "
            f"app_dir={app_dir} config_path={config_path} "
            f"downloads_env={env['SLSKD_DOWNLOADS_DIR']} "
            f"incomplete_env={env['SLSKD_INCOMPLETE_DIR']}"
        )
        return env

    def _start_args(self, exe: Path, app_dir: Path, config_path: Path, settings: Settings) -> list[str]:
        """Return the managed slskd argv.

        Use documented command-line arguments for all payload locations.  slskd
        documents ``--downloads`` and ``--incomplete`` for alternative download
        roots; passing them here keeps APP_DIR local while every Soulseek payload
        byte goes under the user-selected LJS download root.
        """
        plan = build_slskd_share_plan(settings)
        args = [
            str(exe.resolve()),
            "--app-dir",
            str(app_dir),
            "--config",
            str(config_path),
        ]
        args.extend([
            "--downloads",
            str(plan.downloads_dir),
            "--incomplete",
            str(plan.incomplete_dir),
        ])
        return args

    def _format_startup_exit_message(self, settings: Settings, returncode: int | None) -> str:
        """Return a useful error when slskd exits before serving its API."""
        tail = self._recent_log_text(settings, max_bytes=8000).strip()
        if tail:
            tail = tail[-3000:]
            return f"slskd exited during startup (rc={returncode}). Recent slskd log tail:\n{tail}"
        return f"slskd exited during startup (rc={returncode}) before its API became reachable. No slskd log output was captured."

    @staticmethod
    def platform_supported() -> bool:
        """Return whether official slskd release assets are expected for this OS/architecture."""
        return SlskdManager._platform_asset_terms() is not None

    @staticmethod
    def _platform_asset_terms() -> tuple[list[str], list[str]] | None:
        system = platform.system()
        machine = platform.machine().lower()
        if machine in {"x86_64", "amd64"}:
            arch_terms = ["x64", "amd64"]
        elif machine in {"arm64", "aarch64"}:
            arch_terms = ["arm64", "aarch64"]
        else:
            return None
        if system == "Linux":
            return ["linux"], arch_terms
        if system == "Darwin":
            return ["osx", "macos", "darwin"], arch_terms
        if system == "Windows":
            return ["win", "windows"], arch_terms
        return None

    @classmethod
    async def _candidate_asset_urls(cls) -> list[str]:
        """Resolve compatible release asset URLs, preferring GitHub's live API."""
        live = await asyncio.to_thread(cls._release_api_asset_urls)
        if live:
            return live
        return cls._fallback_asset_urls(SLSKD_FALLBACK_VERSION)

    @classmethod
    def _release_api_asset_urls(cls) -> list[str]:
        try:
            req = urllib.request.Request(
                SLSKD_RELEASE_API,
                headers={"Accept": "application/vnd.github+json", "User-Agent": "Long-John-Silver"},
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            logger.debug(f"slskd: release API lookup failed: {exc}")
            return []
        version = str(data.get("tag_name") or "").lstrip("v") or None
        urls = cls._select_asset_urls(data.get("assets") or [])
        if version:
            # Keep for diagnostics; classmethod cannot mutate instance.
            pass
        return urls

    @classmethod
    def _select_asset_urls(cls, assets: list[dict[str, Any]]) -> list[str]:
        terms = cls._platform_asset_terms()
        if not terms:
            return []
        os_terms, arch_terms = terms
        matches: list[tuple[int, str]] = []
        for asset in assets:
            name = str(asset.get("name") or "").lower()
            url = str(asset.get("browser_download_url") or "")
            if not name.endswith(".zip") or not url:
                continue
            if "source" in name or "checksum" in name or "sha" in name:
                continue
            if not any(term in name for term in os_terms):
                continue
            if not any(term in name for term in arch_terms):
                continue
            # Prefer explicit OS/arch native packages over generic bundles.  On
            # Linux prefer glibc over musl first: user logs showed a linux-musl
            # asset could extract but fail at exec time with ENOENT on systems
            # without a musl loader.  The smoke test still verifies the binary.
            score = 0
            if "slskd" in name:
                score -= 5
            if platform.system() == "Linux" and "musl" in name:
                score += 4
            if platform.system() == "Linux" and "linux-x64" in name and "musl" not in name:
                score -= 3
            if "web" in name or "wwwroot" in name:
                score += 10
            matches.append((score, url))
        return [url for _, url in sorted(matches, key=lambda item: item[0])]

    @classmethod
    def _fallback_asset_urls(cls, version: str) -> list[str]:
        terms = cls._platform_asset_terms()
        if not terms:
            return []
        system = platform.system()
        machine = platform.machine().lower()
        arch = "arm64" if machine in {"arm64", "aarch64"} else "x64"
        prefixes: list[str]
        if system == "Linux":
            prefixes = [
                f"slskd-{version}-linux-{arch}.zip",
                f"slskd-linux-{arch}.zip",
                f"slskd-{version}-linux-musl-{arch}.zip",
                f"slskd-linux-musl-{arch}.zip",
            ]
        elif system == "Darwin":
            prefixes = [f"slskd-{version}-osx-{arch}.zip", f"slskd-{version}-macos-{arch}.zip", f"slskd-osx-{arch}.zip"]
        elif system == "Windows":
            prefixes = [f"slskd-{version}-win-{arch}.zip", f"slskd-{version}-windows-{arch}.zip", f"slskd-win-{arch}.zip"]
        else:
            return []
        return [f"https://github.com/slskd/slskd/releases/download/{version}/{name}" for name in prefixes]

    def _detect_existing(self) -> bool:
        exe = self._executable_path()
        return exe is not None and exe.exists()

    async def _binary_is_runnable_async(self, exe: Path) -> bool:
        """Run the executable smoke test without blocking the asyncio loop."""
        return await asyncio.to_thread(self._binary_is_runnable, exe)

    def _executable_path(self) -> Path | None:
        if not SLSKD_BIN_DIR.exists():
            return None
        target = "slskd.exe" if platform.system() == "Windows" else "slskd"
        for found in SLSKD_BIN_DIR.rglob(target):
            if found.is_file():
                if platform.system() != "Windows":
                    try:
                        found.chmod(found.stat().st_mode | 0o111)
                    except Exception:
                        pass
                return found
        return None

    @staticmethod
    def _binary_is_runnable(exe: Path) -> bool:
        """Return whether the extracted binary can be executed on this host."""
        try:
            completed = subprocess.run(
                [str(exe), "--version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=8,
                check=False,
            )
            return completed.returncode in {0, 1}
        except FileNotFoundError:
            return False
        except OSError as exc:
            logger.debug(f"slskd: binary smoke test failed for {exe}: {exc}")
            return False
        except subprocess.TimeoutExpired:
            return True

    @staticmethod
    def _clear_bin_dir() -> None:
        """Remove stale extracted binaries before trying another release asset."""
        if SLSKD_BIN_DIR.exists():
            for child in SLSKD_BIN_DIR.iterdir():
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    try:
                        child.unlink()
                    except Exception:
                        pass

    @staticmethod
    def _download_file(url: str, dest: str) -> None:
        try:
            urllib.request.urlretrieve(url, dest)
        except urllib.error.HTTPError:
            raise

    @staticmethod
    def _extract_zip(archive: Path, dest: Path) -> None:
        safe_extract_zip(archive, dest)

    @staticmethod
    def _resolve_path(value: str | None) -> Path:
        path = Path(str(value or "")).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        return path.resolve(strict=False)
