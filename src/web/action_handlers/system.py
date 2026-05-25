"""
System action handlers for LJS.

Provides SystemActionHandler: the single place for system management
mutation logic invoked via ActionGateway from UI endpoints.
"""

import uuid

from src.core.config import SettingsManager
from src.core.database import Database
from src.core.security.command_policy import CommandPolicy, CommandPolicyError
from src.search.jackett_manager import JackettManager
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

    def __init__(self, settings_manager: SettingsManager, browser_runtime: BrowserRuntime, jackett_manager: JackettManager, comms_registry: CommsRegistry, db: Database, auth_service: AuthService) -> None:
        self._sm = settings_manager
        self._browser = browser_runtime
        self._jackett = jackett_manager
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
