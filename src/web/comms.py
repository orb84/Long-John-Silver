"""
Modular communications bridge registry for LJS.

Provides a plugin system for chat bridges. Each bridge (Discord, Telegram,
future WhatsApp, etc.) registers itself as a CommsBridge subclass. The
registry discovers available bridges, installs missing packages on demand,
and manages bridge lifecycle.

New platforms are added by:
1. Creating a new file: src/web/whatsapp_bridge.py
2. Subclassing CommsBridge
3. Registering with: registry.register("whatsapp", WhatsAppBridge)
"""

import asyncio
import importlib
import inspect
import sys
from pathlib import Path
from loguru import logger
from typing import Optional

from src.core.models import Settings, NotificationMessage
from src.core.notifications import NotificationService
from src.core.security.command_policy import CommandPolicy, CommandPolicyError
from src.core.task_supervisor import TaskSupervisor
from src.ai.assistant import AIAssistant
from src.ai.chat_session_runner import ChatSessionRunner, ChatTurnRequest


class CommsBridge:
    """Base class for all communication bridges."""

    name: str = ""
    display_name: str = ""
    description: str = ""
    icon: str = ""
    package_name: str = ""
    settings_token_field: str = ""

    @classmethod
    def get_formatting_instructions(cls) -> str:
        """Return platform-specific formatting instructions and best practices for the LLM."""
        return ""

    def __init__(self, settings: Settings, assistant: AIAssistant,
                 notifications: NotificationService,
                 supervisor: TaskSupervisor | None = None):
        self._settings = settings
        self._assistant = assistant
        self._notifications = notifications
        self._supervisor = supervisor
        self._chat_runner = ChatSessionRunner(assistant)

    @property
    def assistant(self) -> AIAssistant:
        """Expose the AI Assistant instance for this bridge."""
        return self._assistant

    @property
    def chat_runner(self) -> ChatSessionRunner:
        """Expose the shared assistant-turn runner for transport adapters."""
        return self._chat_runner

    def make_chat_request(self, prompt: str, session_id: str, user_id: str | None = None) -> ChatTurnRequest:
        """Create the canonical chat request used by bridge transports."""
        return ChatTurnRequest(prompt=prompt, session_id=session_id, user_id=user_id)

    def chat_error_text(self, operation: str, exc: BaseException | str) -> str:
        """Format bridge errors through the same assistant/persona presenter."""
        return self._chat_runner.format_error(operation, exc)

    def is_configured(self) -> bool:
        """Return True if the user has configured this bridge (e.g. set a token)."""
        return False

    def is_installed(self) -> bool:
        """Return True if the required Python package is importable."""
        return True

    async def start(self) -> None:
        """Start the bridge (connect to platform, register handlers)."""
        pass

    async def stop(self) -> None:
        """Stop the bridge gracefully."""
        pass

    async def send_notification(self, message: NotificationMessage) -> None:
        """Send a notification through this bridge. Optional override."""
        pass


class CommsRegistry:
    """Discovers, installs, and manages communication bridges.

    Each bridge type registers with an ID, a factory, and metadata.
    The registry checks which bridges are installed and configured,
    installs missing packages on demand, and exposes availability
    info to the UI.
    """

    def __init__(self):
        self._registered: dict[str, dict] = {}
        self._instances: dict[str, CommsBridge] = {}
        self._running: dict[str, CommsBridge] = {}

    def register(self, bridge_id: str, factory: type,
                 display_name: str = "", description: str = "",
                 icon: str = "", package_name: str = "",
                 settings_token_field: str = "") -> None:
        """Register a bridge type so the registry knows about it.

        Args:
            bridge_id: Unique ID (e.g. "discord", "telegram", "whatsapp").
            factory: CommsBridge subclass (not an instance).
            display_name: Human-readable name for UI.
            description: Short description for setup wizard.
            icon: Emoji or icon name for UI card.
            package_name: PyPI package name to install (e.g. "discord.py").
            settings_token_field: Settings field that holds the auth token.
        """
        self._registered[bridge_id] = {
            "factory": factory,
            "display_name": display_name or getattr(factory, "display_name", "") or bridge_id.title(),
            "description": description or getattr(factory, "description", ""),
            "icon": icon or getattr(factory, "icon", ""),
            "package_name": package_name or getattr(factory, "package_name", ""),
            "settings_token_field": settings_token_field or getattr(factory, "settings_token_field", ""),
        }

    def discover_bridges(self) -> None:
        """Scan the web directory for any *_bridge.py files, import them, and register any CommsBridge subclasses."""
        comms_dir = Path(__file__).parent.resolve()
        
        # Walk directory to find files ending with _bridge.py
        for filepath in comms_dir.glob("*_bridge.py"):
            module_name = filepath.stem
            try:
                full_module_name = f"src.web.{module_name}"
                
                # If module already imported, reload it to allow hot updates
                if full_module_name in sys.modules:
                    module = importlib.reload(sys.modules[full_module_name])
                else:
                    module = importlib.import_module(full_module_name)
                    
                # Find subclasses of CommsBridge defined in the module
                for _, obj in inspect.getmembers(module, inspect.isclass):
                    if issubclass(obj, CommsBridge) and obj is not CommsBridge:
                        bridge_id = getattr(obj, "name", "")
                        if not bridge_id:
                            continue
                        self.register(
                            bridge_id=bridge_id,
                            factory=obj,
                            display_name=getattr(obj, "display_name", ""),
                            description=getattr(obj, "description", ""),
                            icon=getattr(obj, "icon", ""),
                            package_name=getattr(obj, "package_name", ""),
                            settings_token_field=getattr(obj, "settings_token_field", ""),
                        )
                        logger.info(f"Dynamically registered comms bridge: '{bridge_id}' from {filepath.name}")
            except Exception as e:
                logger.error(f"Failed to dynamically load comms bridge from {filepath.name}: {e}")

    def get_registered_info(self, bridge_id: str) -> Optional[dict]:
        """Return registered bridge metadata and factory by ID."""
        return self._registered.get(bridge_id)

    def list_bridges(self) -> list[dict]:
        """Return metadata for all registered bridges (for UI rendering)."""
        result = []
        for bridge_id, info in self._registered.items():
            factory = info["factory"]
            # Check if the bridge class reports it's importable
            temp = factory.__new__(factory)
            result.append({
                "id": bridge_id,
                "display_name": info["display_name"],
                "description": info["description"],
                "icon": info["icon"],
                "package_name": info["package_name"],
                "installed": temp.is_installed(),
                "settings_token_field": info["settings_token_field"],
            })
        return result

    def is_bridge_installed(self, bridge_id: str) -> bool:
        """Check if a bridge's required package is importable."""
        info = self._registered.get(bridge_id)
        if not info:
            return False
        factory = info["factory"]
        temp = factory.__new__(factory)
        return temp.is_installed()

    async def install_bridge(self, bridge_id: str) -> bool:
        """Install a bridge's required package via pip.

        Returns True if installation succeeded or was already installed.
        """
        info = self._registered.get(bridge_id)
        if not info:
            logger.error(f"Unknown bridge: {bridge_id}")
            return False
        pkg = info["package_name"]
        if not pkg:
            return True

        logger.info(f"Installing package '{pkg}' for bridge '{bridge_id}'...")
        try:
            result = CommandPolicy().run_sync(
                [sys.executable, "-m", "pip", "install", pkg],
                purpose=f"comms.install.{bridge_id}", approved=True,
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode == 0:
                logger.info(f"Package '{pkg}' installed successfully")
                return True
            else:
                logger.error(f"pip install failed: {result.stderr}")
                return False
        except TimeoutError:
            logger.error(f"pip install timed out for '{pkg}'")
            return False
        except CommandPolicyError as e:
            logger.error(f"Bridge install blocked by command policy: {e}")
            return False
        except Exception as e:
            logger.error(f"pip install error for '{pkg}': {e}")
            return False

    async def start_bridge(self, bridge_id: str,
                           settings: Settings, assistant: AIAssistant,
                           notifications: NotificationService,
                           supervisor: TaskSupervisor | None = None) -> Optional[CommsBridge]:
        """Create and start a bridge instance if configured and installed."""
        info = self._registered.get(bridge_id)
        if not info:
            logger.error(f"Unknown bridge: {bridge_id}")
            return None

        factory = info["factory"]
        bridge = factory(settings, assistant, notifications, supervisor=supervisor)
        self._instances[bridge_id] = bridge

        if not bridge.is_installed():
            logger.info(f"Bridge '{bridge_id}' package not found — auto-installing")
            installed = await self.install_bridge(bridge_id)
            if not installed:
                logger.error(f"Cannot start bridge '{bridge_id}': package install failed")
                return None
            # Re-discover bridge modules after install.  Some bridge files used
            # optional imports at module load time, so simply re-instantiating the
            # old class can preserve a stale "package unavailable" state.
            self.discover_bridges()
            info = self._registered.get(bridge_id, info)
            factory = info["factory"]
            bridge = factory(settings, assistant, notifications, supervisor=supervisor)
            self._instances[bridge_id] = bridge
            if not bridge.is_installed():
                logger.error(f"Bridge '{bridge_id}' still not importable after install")
                return None

        if not bridge.is_configured():
            logger.info(f"Bridge '{bridge_id}' not configured — skipping")
            return None

        try:
            await bridge.start()
            self._running[bridge_id] = bridge
            logger.info(f"Bridge '{bridge_id}' started")
            return bridge
        except Exception as e:
            logger.error(f"Failed to start bridge '{bridge_id}': {e}")
            return None

    async def stop_all(self) -> None:
        """Stop all running bridges gracefully."""
        for bridge_id, bridge in list(self._running.items()):
            try:
                await bridge.stop()
            except Exception as e:
                logger.error(f"Error stopping bridge '{bridge_id}': {e}")
        self._running.clear()
        logger.info("All comms bridges stopped")

    async def restart_configured(self, settings: Settings, assistant: AIAssistant,
                                 notifications: NotificationService,
                                 supervisor: TaskSupervisor | None = None) -> list[str]:
        """Restart configured communication bridges with the latest settings.

        Setup and Settings can change bridge tokens while the server is already
        running.  Without a restart hook, Discord/Telegram/WhatsApp would remain
        disconnected until the whole LJS process was restarted.  This method is
        intentionally idempotent: it stops anything currently running, rebuilds
        bridge instances with the updated settings object, and starts only
        bridges whose tokens/configuration are now present.
        """
        await self.stop_all()
        started: list[str] = []
        for bridge_info in self.list_bridges():
            bridge = await self.start_bridge(
                bridge_info["id"], settings, assistant, notifications, supervisor=supervisor,
            )
            if bridge:
                started.append(bridge_info["id"])
        logger.info(f"Communication bridges restarted; running={started}")
        return started

    def get_running(self, bridge_id: str) -> Optional[CommsBridge]:
        """Return a running bridge instance by ID."""
        return self._running.get(bridge_id)

    def get_instance(self, bridge_id: str) -> Optional[CommsBridge]:
        """Return a bridge instance by ID, running or not."""
        return self._instances.get(bridge_id)


def create_registry() -> CommsRegistry:
    """Create and populate the comms registry with all known bridges.

    Bridges are auto-discovered dynamically from any `*_bridge.py` files in the directory.
    """
    registry = CommsRegistry()
    registry.discover_bridges()
    return registry