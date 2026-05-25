"""
Server test harness for end-to-end LJS verification.

Connects to a running LJS server via WebSocket (chat agent) and
HTTP (REST API). Provides high-level methods for exercising every
subsystem: intent detection, download management, scheduling,
taste profiling, and suggestion workflows.
"""

import json
import asyncio
from pathlib import Path
from typing import Optional

import httpx
import websockets
from loguru import logger

from src.core.models import Intent


class ChatResponse:
    """Collected result from a single chat message exchange.

    Holds the full concatenated text and the raw token stream for
    inspection of streaming behavior.
    """

    def __init__(self) -> None:
        self.text: str = ""
        self.tokens: list[str] = []
        self.done: bool = False


class ServerTestHarness:
    """Integration test harness for a running LJS server.

    Connects via WebSocket for chat/agent interactions and HTTP for
    REST API calls. Designed to verify every subsystem from startup
    through full agent workflows.

    Usage::

        harness = ServerTestHarness("http://localhost:8088")
        await harness.connect()
        response = await harness.send_chat("hello")
        items = await harness.get_library_items()
        await harness.disconnect()
    """

    _REST_TIMEOUT = 30.0
    _WS_TIMEOUT = 120.0
    _WS_RESPONSE_TIMEOUT = 60.0

    def __init__(self, base_url: str = "http://localhost:8088") -> None:
        self._base_url = base_url.rstrip("/")
        self._ws_url = base_url.replace("http://", "ws://").rstrip("/") + "/ws/chat"
        self._http: Optional[httpx.AsyncClient] = None
        self._ws: Optional[websockets.WebSocketClientProtocol] = None

    # ── Connection management ──────────────────────────────────

    async def connect(self) -> None:
        """Open the WebSocket and HTTP client connections."""
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._REST_TIMEOUT,
        )
        self._ws = await self._connect_ws()
        logger.info(f"Harness connected to {self._base_url}")

    async def disconnect(self) -> None:
        """Close both connections gracefully."""
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        logger.info("Harness disconnected")

    async def _connect_ws(self) -> websockets.WebSocketClientProtocol:
        """Establish a WebSocket with retry."""
        for attempt in range(10):
            try:
                ws = await websockets.connect(
                    self._ws_url,
                    ping_interval=20,
                    open_timeout=10,
                )
                return ws
            except (ConnectionRefusedError, OSError) as e:
                if attempt == 9:
                    raise
                logger.debug(f"WebSocket connection attempt {attempt + 1} failed: {e}")
                await asyncio.sleep(2)
        raise RuntimeError("WebSocket connection failed after retries")

    # ── Chat / Agent ────────────────────────────────────────────

    async def send_chat(self, message: str, session_id: str = "test-harness") -> str:
        """Send a chat message and return the full collected response.

        Args:
            message: The chat message text.
            session_id: Session identifier for conversation continuity.

        Returns:
            The full concatenated text response from the agent.
        """
        response = await self.send_chat_raw(message, session_id)
        return response.text

    async def send_chat_raw(
        self, message: str, session_id: str = "test-harness"
    ) -> ChatResponse:
        """Send a chat message and return the raw token stream.

        Args:
            message: The chat message text.
            session_id: Session identifier for conversation continuity.

        Returns:
            A :class:`ChatResponse` with all collected tokens and full text.
        """
        if self._ws is None:
            raise RuntimeError("Not connected. Call connect() first.")

        payload = json.dumps({"message": message, "session_id": session_id})
        await self._ws.send(payload)
        logger.info(f"Sent chat: {message[:80]}...")

        result = ChatResponse()
        try:
            while True:
                raw = await asyncio.wait_for(
                    self._ws.recv(), timeout=self._WS_RESPONSE_TIMEOUT
                )
                event = json.loads(raw)
                if event.get("type") == "token":
                    content = event.get("content", "")
                    result.tokens.append(content)
                    result.text += content
                elif event.get("type") == "done":
                    result.done = True
                    break
                else:
                    logger.debug(f"Unknown WS event: {event}")
        except asyncio.TimeoutError:
            logger.warning("Chat response timed out")

        return result

    # ── REST: Health ────────────────────────────────────────────

    async def is_server_up(self) -> bool:
        """Check if the server is responding."""
        try:
            resp = await self._get("/")
            return resp.status_code == 200
        except Exception:
            return False

    async def get_library_status(self) -> dict:
        """Fetch the current library scan status."""
        return (await self._get("/api/library/status")).json()

    # ── REST: Category items ───────────────────────────────────

    async def get_library_items(self) -> list[dict]:
        """List category items from the library status.

        Uses the library scan data, not just items with active downloads.
        """
        status = await self.get_library_status()
        return status.get("items", [])

    async def get_active_downloads(self) -> list[dict]:
        """List all currently active downloads."""
        data = (await self._get("/api/downloads")).json()
        return data.get("active", [])

    async def add_category_item(self, name: str, auto_download: bool = False) -> dict:
        """Add a category item to the tracking list.

        Args:
            name: Item name to add.
            auto_download: Whether to enable auto-download for this item.

        Returns:
            The API response dict (typically a success message).
        """
        return (await self._post("/api/categories/tv/items", {
            "name": name,
            "auto_download": auto_download,
        })).json()

    async def remove_category_item(self, name: str) -> dict:
        """Remove a category item from tracking."""
        return (await self._delete(f"/api/categories/tv/items/{name}")).json()

    async def get_category_item_detail(self, name: str) -> dict:
        """Get detailed status for a single category item."""
        return (await self._get(f"/api/categories/tv/items/{name}")).json()

    async def pause_category_item(self, name: str) -> dict:
        """Pause monitoring for a category item."""
        return (await self._post(f"/api/categories/tv/items/{name}/pause", {})).json()

    async def resume_category_item(self, name: str) -> dict:
        """Resume monitoring for a category item."""
        return (await self._post(f"/api/categories/tv/items/{name}/resume", {})).json()

    async def check_category_item(self, name: str) -> dict:
        """Force an immediate check for a category item."""
        return (await self._post(f"/api/categories/tv/items/{name}/actions/check", {})).json()

    # ── REST: Downloads ─────────────────────────────────────────

    async def get_downloads(self) -> list[dict]:
        """List all active/completed downloads."""
        return (await self._get("/api/downloads/queue")).json()

    async def pause_download(self, download_id: str) -> dict:
        """Pause a specific download."""
        return (await self._post(f"/api/downloads/{download_id}/pause", {})).json()

    async def resume_download(self, download_id: str) -> dict:
        """Resume a specific download."""
        return (await self._post(f"/api/downloads/{download_id}/resume", {})).json()

    async def cancel_download(self, download_id: str) -> dict:
        """Cancel a specific download."""
        return (await self._post(f"/api/downloads/{download_id}/cancel", {})).json()

    async def cycle_priority(self, download_id: str) -> dict:
        """Cycle the priority of a download (HIGH→NORMAL→LOW→HIGH)."""
        return (await self._post(f"/api/downloads/{download_id}/priority", {})).json()

    # ── REST: Suggestions ───────────────────────────────────────

    async def get_suggestions(self, item_name: Optional[str] = None) -> list[dict]:
        """List pending suggestions, optionally filtered by show."""
        params = {"item_name": item_name} if item_name else {}
        resp = (await self._get("/api/suggestions", params=params)).json()
        return resp.get("suggestions", resp) if isinstance(resp, dict) else resp

    async def approve_suggestion(self, action_id: int) -> dict:
        """Approve a suggestion and queue the download."""
        return (await self._post(f"/api/suggestions/{action_id}/approve", {})).json()

    async def deny_suggestion(self, action_id: int) -> dict:
        """Deny/dismiss a suggestion."""
        return (await self._post(f"/api/suggestions/{action_id}/deny", {})).json()

    async def approve_all_suggestions(self, item_name: str) -> dict:
        """Approve all pending suggestions for a category item."""
        return (await self._post(f"/api/suggestions/approve-all/{item_name}", {})).json()

    # ── REST: Upgrades ──────────────────────────────────────────

    async def get_upgrades(self) -> list[dict]:
        """List pending quality upgrades."""
        resp = (await self._get("/api/upgrades")).json()
        return resp.get("upgrades", resp) if isinstance(resp, dict) else resp

    async def approve_upgrade(self, upgrade_id: str) -> dict:
        """Approve a quality upgrade."""
        return (await self._post(f"/api/upgrades/{upgrade_id}/approve", {})).json()

    # ── REST: Settings ──────────────────────────────────────────

    async def set_auto_download(self, enabled: bool) -> dict:
        """Toggle global auto-download mode."""
        return (await self._post("/api/settings/auto_download", {
            "enabled": enabled,
        })).json()

    async def save_quality_settings(self, settings: dict) -> dict:
        """Save quality preference settings."""
        return (await self._post("/api/settings/quality", settings)).json()

    async def trigger_library_scan(self) -> dict:
        """Trigger a manual library scan."""
        return (await self._post("/api/library/scan", {})).json()

    # ── Verification helpers ────────────────────────────────────

    async def verify_server_healthy(self) -> tuple[bool, str]:
        """Check that all core subsystems are running.

        Returns:
            A (pass, detail) tuple.
        """
        try:
            status = await self.get_library_status()
            shows = status.get("shows", 0)
            movies = status.get("movies", 0)
            files = status.get("total_files", 0)
            return True, (
                f"Server healthy: {shows} shows, {movies} movies, {files} files"
            )
        except Exception as e:
            return False, f"Health check failed: {e}"

    async def verify_intent_routes_to(
        self, message: str, expected_tools_hint: str
    ) -> tuple[bool, str]:
        """Send a message and verify the response mentions the expected tool.

        Used to verify intent detection routes correctly without
        needing exact intent enum access. Checks that the LLM
        response contains keywords suggesting the right intent was
        triggered (e.g., "search" for SEARCH, "download" for DOWNLOAD).

        Args:
            message: The chat message to test.
            expected_tools_hint: A substring expected in the response
                that indicates the correct intent routing.

        Returns:
            A (pass, detail) tuple.
        """
        try:
            response = await self.send_chat(message)
            found = expected_tools_hint.lower() in response.lower()
            detail = (
                f"Intent check: '{message[:60]}...' → "
                f"{'HIT' if found else 'MISS'} for '{expected_tools_hint}'"
            )
            return found, detail
        except Exception as e:
            return False, f"Intent check failed: {e}"

    async def wait_for_condition(
        self,
        condition_fn,
        description: str,
        timeout: float = 30.0,
        interval: float = 1.0,
    ) -> bool:
        """Poll a condition until it returns True or timeout expires.

        Args:
            condition_fn: Async callable returning a bool.
            description: Human-readable description for logging.
            timeout: Maximum seconds to wait.
            interval: Seconds between polls.

        Returns:
            True if condition became true, False on timeout.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            try:
                if await condition_fn():
                    return True
            except Exception as e:
                logger.debug(f"Poll condition '{description}' error: {e}")
            await asyncio.sleep(interval)
        logger.warning(f"Condition '{description}' not met after {timeout}s")
        return False

    # ── Internal HTTP helpers ───────────────────────────────────

    _HTTP_HEADERS = {"Accept": "application/json"}

    async def _get(self, path: str, params: Optional[dict] = None) -> httpx.Response:
        if self._http is None:
            raise RuntimeError("Not connected")
        resp = await self._http.get(path, params=params, headers=self._HTTP_HEADERS)
        resp.raise_for_status()
        return resp

    async def _post(self, path: str, data: dict) -> httpx.Response:
        if self._http is None:
            raise RuntimeError("Not connected")
        resp = await self._http.post(path, json=data, headers=self._HTTP_HEADERS)
        resp.raise_for_status()
        return resp

    async def _delete(self, path: str) -> httpx.Response:
        if self._http is None:
            raise RuntimeError("Not connected")
        resp = await self._http.delete(path, headers=self._HTTP_HEADERS)
        resp.raise_for_status()
        return resp
