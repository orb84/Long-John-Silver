"""
Web dependencies for LJS.

Defines WebDependencies dataclass — a single container for all services
injected into FastAPI routers — shared auth dependencies, WebSocket
auth verification, and the download stats broadcasting decoupling.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any

from fastapi import Depends, HTTPException, Request, WebSocket

from loguru import logger
from src.web.websocket_manager import ConnectionManager


@dataclass
class WebDependencies:
    """Container for all shared services injected into web routers.

    This is the single source of truth for what the web layer needs.
    Every router receives a WebDependencies instance instead of 15
    separate parameters. New services are added here, not to the
    ``create_app()`` signature.
    """
    settings_manager: Any = None
    db: Any = None
    assistant: Any = None
    downloader: Any = None
    notifications: Any = None
    auth_service: Any = None
    llm_manager: Any = None
    scanner: Any = None
    conversation_manager: Any = None
    vector_store: Any = None
    behavior_tracker: Any = None
    suggestion_compiler: Any = None
    recommender: Any = None
    release_group_tracker: Any = None
    scheduler: Any = None
    supervisor: Any = None
    comms_registry: Any = None
    torrent_racer: Any = None
    search_aggregator: Any = None
    browser_runtime: Any = None
    jackett_manager: Any = None
    slskd_manager: Any = None
    storage_monitor: Any = None
    artwork_manager: Any = None
    metadata_enricher: Any = None
    tvmaze_client: Any = None
    event_bus: Any = None
    action_gateway: Any = None
    action_event_store: Any = None
    category_registry: Any = None
    tool_registry: Any = None
    behavior_recorder: Any = None
    templates: Any = None
    librarian: Any = None
    chat_ws_manager: Any = None
    dl_ws_manager: Any = None
    trakt_pkce_store: dict[str, dict[str, str]] = field(default_factory=dict)
    suggestion_compile_task: Any = None


async def verify_auth(request: Request) -> bool:
    """Check if the request is authenticated.

    When no password is configured (web_password_hash is None),
    all access is allowed — first install requires no password.
    When a password is set, checks the session token or
    falls back to HTTP Basic auth. Returns 401 JSON (not browser
    popup) on failure so the JS frontend can handle it.
    """
    deps: WebDependencies = request.app.state.deps  # type: ignore[union-attr]
    
    # Allow all requests if first-time setup is not yet complete
    if not deps.settings_manager.settings.setup_complete:
        return True

    stored_hash = deps.settings_manager.settings.web_password_hash
    if not stored_hash or not stored_hash.strip():
        return True

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Basic "):
        import base64
        try:
            decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
            _, password = decoded.split(":", 1)
            if deps.auth_service.verify_password(password, stored_hash):
                return True
        except Exception:
            pass

    token = request.headers.get("X-Auth-Token") or request.cookies.get("ljs_token")
    if token and deps.auth_service.verify_token(token):
        return True

    raise HTTPException(status_code=401, detail="Authentication required")


async def verify_ws_auth(websocket: WebSocket, deps: WebDependencies) -> bool:
    """Verify WebSocket authentication before accepting the connection.

    Checks for a token in the ``ljs_token`` cookie first, then
    the ``token`` query parameter, then an ``Authorization: Bearer``
    header. When no password is configured, all connections are allowed.
    """
    stored_hash = deps.settings_manager.settings.web_password_hash
    if not stored_hash or not stored_hash.strip():
        return True

    token = websocket.cookies.get("ljs_token")
    if not token:
        token = websocket.query_params.get("token")
    if not token:
        auth_header = websocket.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if token and deps.auth_service.verify_token(token):
        return True

    await websocket.close(code=4001, reason="Authentication required")
    return False


class DownloadStatsBroadcaster:
    """Decoupled download stats broadcaster.

    Wires download stats updates to WebSocket broadcast and the event bus.
    Replaces the inline _on_dl_stats closure from create_app().
    """

    def __init__(self, ws_manager: ConnectionManager, supervisor: Any, event_bus: Any) -> None:
        self._ws_manager = ws_manager
        self._supervisor = supervisor
        self._event_bus = event_bus

    def __call__(self, download_id: str, stats: dict) -> None:
        """Broadcast download stats to connected WebSocket clients and event bus.

        Called synchronously by the download manager as a callback.
        """
        msg = {"type": "stats", "id": download_id, "stats": stats}
        if self._supervisor:
            self._supervisor.spawn_one_shot(
                f"dl_stats_{download_id}",
                self._ws_manager.broadcast(msg),
            )
        else:
            asyncio.create_task(self._ws_manager.broadcast(msg))
        self._event_bus.emit_dl_stats(download_id, stats)
