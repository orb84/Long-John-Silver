"""
System action handlers for LJS.

Provides SystemActionHandler: the single place for system management
mutation logic invoked via ActionGateway from UI endpoints.
"""

import uuid
from typing import Any

from src.core.config import SettingsManager
from src.core.database import Database
from src.core.security.command_policy import CommandPolicy, CommandPolicyError
from src.search.jackett_manager import JackettManager
from src.integrations.slskd_manager import SlskdManager
from src.core.models import SoulseekSettings
from src.integrations.slskd_client import SlskdClient
from src.utils.auth import AuthService
from src.utils.browser.runtime import BrowserRuntime
from src.web.comms import CommsRegistry


class SystemActionHandler:
    """Handlers for system management actions routed through ActionGateway.

    Each method receives keyword arguments from ActionCommand.arguments
    and returns a dict wrapped into ActionResult.data.

    Dependencies (injected at composition root):
        settings_manager — SettingsManager (save settings after jackett install)
        browser_runtime — BrowserRuntime (playwright management)
        jackett_manager — JackettManager (jackett installation)
        comms_registry — CommsRegistry (bridge installation)
        db — Database (user CRUD)
        auth_service — AuthService (password hashing)
    """

    def __init__(self, settings_manager: SettingsManager, browser_runtime: BrowserRuntime, jackett_manager: JackettManager, slskd_manager: SlskdManager | None, comms_registry: CommsRegistry, db: Database, auth_service: AuthService) -> None:
        self._sm = settings_manager
        self._browser = browser_runtime
        self._jackett = jackett_manager
        self._slskd = slskd_manager
        self._comms = comms_registry
        self._db = db
        self._auth = auth_service

    async def install_playwright(self) -> dict:
        """Install Playwright and Chromium browser via pip and playwright CLI."""
        import sys
        try:
            policy = CommandPolicy()
            result = policy.run_sync(
                [sys.executable, "-m", "pip", "install", "playwright"],
                purpose="system.install_playwright.pip", approved=True,
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                return {"status": "error", "detail": result.stderr[:500]}
            result = policy.run_sync(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                purpose="system.install_playwright.chromium", approved=True,
                capture_output=True, text=True, timeout=300,
            )
            if result.returncode != 0:
                return {"status": "error", "detail": result.stderr[:500]}
            return {"status": "installed"}
        except TimeoutError:
            return {"status": "error", "detail": "Installation timed out"}
        except CommandPolicyError as e:
            return {"status": "error", "detail": f"Command blocked by security policy: {e}"}
        except Exception as e:
            return {"status": "error", "detail": str(e)[:500]}

    async def install_jackett(self) -> dict:
        """Download, start, persist, and configure Jackett for first launch."""
        if not self._jackett:
            return {"status": "error", "error": "Jackett manager not configured"}
        ok = await self._jackett.ensure_installed()
        if not ok:
            return {"status": "error", "error": "Jackett download or extraction failed"}
        running = await self._jackett.start()
        if not running:
            return {"status": "error", "error": "Jackett started but failed health check"}
        self._jackett.save_to_settings(self._sm.settings)
        self._sm.save(self._sm.settings)
        indexers = await self._jackett.configure_default_indexers()
        return {
            "status": "installed",
            "url": self._jackett.url,
            "api_key": self._jackett.api_key or "",
            "indexers": indexers,
        }


    async def start_jackett(self) -> dict:
        """Start Jackett and persist URL/API key if available."""
        if not self._jackett:
            return {"status": "error", "error": "Jackett manager not configured"}
        running = await self._jackett.start()
        if not running:
            return {"status": "error", "error": "Jackett failed to start"}
        self._jackett.save_to_settings(self._sm.settings)
        self._sm.save(self._sm.settings)
        return {"status": "running", "url": self._jackett.url, "api_key": self._jackett.api_key or ""}

    async def install_soulseek(self) -> dict:
        """Install, configure, start, and persist the managed slskd runtime."""
        if not self._slskd:
            return {"status": "error", "error": "slskd manager not configured"}
        settings = self._sm.settings
        settings.soulseek.enabled = True
        if not settings.soulseek.soulseek_username or not settings.soulseek.soulseek_password:
            self._sm.save(settings)
            settings.soulseek.account_status = "needs_credentials"
            settings.soulseek.account_status_message = "Soulseek username and password are required. Use an existing account, or enter a new unique username/password and LJS will validate it."
            self._sm.save(settings)
            return {"status": "needs_credentials", "ready": False, "error": settings.soulseek.account_status_message}
        try:
            ok = await self._slskd.start(settings, login_timeout_seconds=10.0)
        except Exception as exc:
            ok = False
            settings.soulseek.account_status = "error"
            settings.soulseek.account_status_message = f"Soulseek start failed: {exc}"
        self._slskd.save_to_settings(settings)
        self._sm.save(settings)
        ready = bool(ok and settings.soulseek.account_ready)
        if not ready:
            return {"status": settings.soulseek.account_status or "error", "ready": False, "running": self._slskd.is_running, "error": None if settings.soulseek.account_status == "checking" else (settings.soulseek.account_status_message or self._slskd.last_error or "slskd failed to start"), "account_status_message": settings.soulseek.account_status_message}
        return {"status": "ready", "running": True, "ready": True, "url": self._slskd.url, "api_key_available": bool(settings.soulseek.api_key), "account_status": settings.soulseek.account_status, "account_status_message": settings.soulseek.account_status_message}

    async def start_soulseek(self) -> dict:
        """Start the managed slskd runtime from saved settings."""
        if not self._slskd:
            return {"status": "error", "error": "slskd manager not configured"}
        settings = self._sm.settings
        settings.soulseek.enabled = True
        try:
            ok = await self._slskd.start(settings, login_timeout_seconds=10.0)
        except Exception as exc:
            ok = False
            settings.soulseek.account_status = "error"
            settings.soulseek.account_status_message = f"Soulseek start failed: {exc}"
        self._slskd.save_to_settings(settings)
        self._sm.save(settings)
        ready = bool(ok and settings.soulseek.account_ready)
        if not ready:
            return {"status": settings.soulseek.account_status or "error", "ready": False, "running": self._slskd.is_running, "error": None if settings.soulseek.account_status == "checking" else (settings.soulseek.account_status_message or self._slskd.last_error or "slskd failed to start"), "account_status_message": settings.soulseek.account_status_message}
        return {"status": "ready", "running": True, "ready": True, "url": self._slskd.url, "account_status": settings.soulseek.account_status, "account_status_message": settings.soulseek.account_status_message}

    async def check_soulseek_login(self, soulseek: dict[str, Any] | None = None, timeout_seconds: float = 45.0) -> dict:
        """Install/start slskd if needed and immediately validate Soulseek credentials.

        Used by setup and Compass explicit "Check login" buttons.  The button
        should prove the whole managed path: saved credentials, native slskd
        runtime, API reachability, Soulseek network login, and the search API.
        """
        if not self._slskd:
            return {"status": "error", "ready": False, "error": "slskd manager not configured"}
        settings = self._sm.settings
        if isinstance(soulseek, dict):
            merged = {**settings.soulseek.model_dump(mode="json"), **soulseek}
            settings.soulseek = SoulseekSettings(**merged)
        settings.soulseek.enabled = True
        settings.soulseek.managed = True
        settings.soulseek.auto_install = True

        cfg = settings.soulseek
        if not cfg.soulseek_username or not cfg.soulseek_password:
            cfg.account_status = "needs_credentials"
            cfg.account_status_message = "Enter a Soulseek username and password first. Existing accounts work; a new unique username/password may create an account if the Soulseek network accepts it."
            self._sm.save(settings)
            return self._soulseek_login_response(settings, status="needs_credentials", ready=False, error=cfg.account_status_message)

        try:
            ok = await self._slskd.start(settings, login_timeout_seconds=max(5.0, min(float(timeout_seconds or 45.0), 90.0)))
        except Exception as exc:
            ok = False
            settings.soulseek.account_status = "error"
            settings.soulseek.account_status_message = f"Soulseek start failed during login check: {exc}"
        self._slskd.save_to_settings(settings)
        account = await self._slskd.validate_account(settings, timeout_seconds=0 if ok else 2)
        search_probe = await self._probe_soulseek_search(settings) if settings.soulseek.account_ready else {"ok": False, "skipped": True}
        self._sm.save(settings)

        if settings.soulseek.account_ready and search_probe.get("ok") is True:
            return self._soulseek_login_response(
                settings,
                status="ready",
                ready=True,
                error="",
                extra={
                    "running": True,
                    "url": self._slskd.url,
                    "api_reachable": account.get("api_reachable", True),
                    "authenticated_to_soulseek": True,
                    "search_probe_ok": True,
                    "search_probe_message": "Soulseek login and search API are working.",
                    "next_actions": ["You can now save settings and use Soulseek searches."],
                },
            )

        if settings.soulseek.account_status == "auth_failed":
            return self._soulseek_login_response(
                settings,
                status="auth_failed",
                ready=False,
                error=settings.soulseek.account_status_message or "Soulseek rejected these credentials.",
                extra={"next_actions": ["Try an existing Soulseek account.", "Or choose a different new username/password and press Check login again."]},
            )

        if settings.soulseek.account_ready and search_probe.get("ok") is False:
            error = search_probe.get("error") or "Soulseek login succeeded, but the slskd search API did not accept a probe search."
            settings.soulseek.account_status = "error"
            settings.soulseek.account_status_message = error
            self._sm.save(settings)
            return self._soulseek_login_response(settings, status="error", ready=False, error=error, extra={"search_probe_ok": False, "search_probe": search_probe})

        status = settings.soulseek.account_status or ("checking" if ok else "error")
        error = None if status == "checking" else (settings.soulseek.account_status_message or self._slskd.last_error or "Soulseek login could not be confirmed.")
        return self._soulseek_login_response(
            settings,
            status=status,
            ready=False,
            error=error,
            extra={
                "api_reachable": account.get("api_reachable", False),
                "authenticated_to_soulseek": account.get("authenticated_to_soulseek", False),
                "next_actions": ["Wait a few seconds and press Check login again.", "If it still fails, change the username/password."],
            },
        )

    async def _probe_soulseek_search(self, settings) -> dict:
        """Run a tiny search probe after login so the button proves real usability."""
        try:
            result = await SlskdClient(settings.soulseek).search("ljs login test", timeout_seconds=5, max_results=1)
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:500]}
        if isinstance(result, dict) and result.get("ok") is False:
            return {"ok": False, "error": result.get("error") or result.get("error_code") or "slskd search probe failed", "raw": result}
        return {"ok": True, "candidate_count": len((result or {}).get("candidates") or []) if isinstance(result, dict) else 0}

    def _soulseek_login_response(self, settings, *, status: str, ready: bool, error: str | None = None, extra: dict | None = None) -> dict:
        cfg = settings.soulseek
        payload = {
            "status": status,
            "ready": bool(ready),
            "running": bool(self._slskd and self._slskd.is_running),
            "installed": bool(self._slskd and self._slskd.is_installed),
            "account_status": cfg.account_status,
            "account_status_message": cfg.account_status_message,
            "account_checked_at": cfg.account_checked_at,
            "credentials_configured": bool(cfg.soulseek_username and cfg.soulseek_password),
            "url": self._slskd.url if self._slskd else cfg.host,
            "error": error or "",
        }
        if extra:
            payload.update(extra)
        return payload

    async def stop_soulseek(self, disable: bool = True) -> dict:
        """Stop the managed slskd runtime and optionally disable Soulseek in settings."""
        if not self._slskd:
            return {"status": "error", "error": "slskd manager not configured"}
        await self._slskd.stop()
        if disable:
            self._sm.settings.soulseek.enabled = False
        self._sm.settings.soulseek.account_status = "not_checked" if disable else self._sm.settings.soulseek.account_status
        if disable:
            self._sm.settings.soulseek.account_status_message = "Soulseek/slskd is disabled."
        self._sm.save(self._sm.settings)
        return {"status": "stopped", "running": False, "enabled": bool(self._sm.settings.soulseek.enabled)}

    async def configure_default_indexers(self) -> dict:
        """Configure Jackett's first-run open/public indexer set."""
        if not self._jackett:
            return {"status": "error", "error": "Jackett manager not configured"}
        return await self._jackett.configure_default_indexers()

    async def configure_jackett_indexers(self, profile: str = "balanced_public") -> dict:
        """Configure a named Jackett indexer profile."""
        if not self._jackett:
            return {"status": "error", "error": "Jackett manager not configured"}
        return await self._jackett.configure_indexer_profile(profile)

    async def jackett_indexer_diagnostics(self) -> dict:
        """Return Jackett indexer coverage diagnostics."""
        if not self._jackett:
            return {"status": "error", "error": "Jackett manager not configured"}
        return await self._jackett.indexer_diagnostics()

    async def jackett_indexer_config_schema(self, indexer_id: str) -> dict:
        """Return the generic Jackett config schema for one indexer."""
        if not self._jackett:
            return {"status": "error", "error": "Jackett manager not configured"}
        return await self._jackett.get_indexer_config_schema(indexer_id)

    async def configure_jackett_custom_indexer(self, indexer_id: str, values: dict | None = None) -> dict:
        """Configure a private/closed Jackett indexer with user-supplied values."""
        if not self._jackett:
            return {"status": "error", "error": "Jackett manager not configured"}
        return await self._jackett.configure_custom_indexer(indexer_id, values or {})

    async def auth_register(self, username: str, password: str) -> dict:
        """Register a new user account.

        Returns found=False if the username already exists.
        """
        existing = await self._db.users.get_user_by_username(username)
        if existing:
            return {"found": False, "error": "Username already exists"}
        password_hash = self._auth.hash_password(password)
        user_id = str(uuid.uuid4())
        await self._db.users.create_user(user_id=user_id, username=username, password_hash=password_hash)
        return {"id": user_id, "username": username}

    async def install_comms_bridge(self, bridge_id: str) -> dict:
        """Install a communication bridge by ID."""
        if not self._comms:
            return {"status": "error", "error": "Comms registry not available"}
        installed = await self._comms.install_bridge(bridge_id)
        if installed:
            return {"status": "installed", "bridge_id": bridge_id}
        return {"status": "error", "error": f"Failed to install bridge '{bridge_id}'"}
