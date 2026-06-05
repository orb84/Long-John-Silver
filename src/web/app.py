"""
FastAPI composition root for LJS.
"""

import json
import inspect
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from loguru import logger

from src.core.actions.audit import ActionEventStore
from src.core.actions.gateway import ActionGateway
from src.core.actions.registration import ActionRegistrationService
from src.web.dependencies import (
    DownloadStatsBroadcaster,
    WebDependencies,
    verify_auth,
    verify_ws_auth,
)
from src.web.event_bus import ShipEventBus
from src.web.routers.actions import ActionsRouter
from src.web.routers.categories import CategoriesRouter
from src.web.routers.category_items import CategoryItemsRouter
from src.web.routers.downloads import DownloadsRouter
from src.web.routers.health import HealthRouter
from src.web.routers.library import LibraryRouter
from src.web.routers.notifications import NotificationsRouter
from src.web.routers.release_watches import ReleaseWatchesRouter
from src.web.routers.pages import PagesRouter
from src.web.routers.personas import PersonasRouter
from src.web.routers.providers import ProvidersRouter
from src.web.routers.settings import SettingsRouter
from src.web.routers.sharing import SharingRouter
from src.web.routers.setup import SetupRouter
from src.web.routers.suggestions import SuggestionsRouter
from src.web.routers.system import SystemRouter
from src.web.routers.storage import StorageRouter
from src.web.routers.upgrades import UpgradesRouter
from src.web.websocket_manager import ConnectionManager
from src.ai.chat_session_runner import ChatSessionRunner, ChatTurnRequest


async def _stream_chat_with_progress(websocket: WebSocket, deps: WebDependencies, message: str, session_id: str) -> None:
    """Adapt the shared assistant chat runner to the browser websocket."""
    runner = ChatSessionRunner(deps.assistant)
    request = ChatTurnRequest(prompt=message, session_id=session_id)
    async for event in runner.run_events(request):
        await websocket.send_json({"type": event.type, "content": event.content})


def create_app(**kwargs: Any) -> FastAPI:
    """Create and configure the FastAPI application."""
    deps = WebDependencies(**kwargs)
    app = FastAPI(title="LJS Quartermaster's Deck")
    deps.templates = Jinja2Templates(directory="src/web/templates")
    app.state.deps = deps

    chat_ws_manager = ConnectionManager()
    dl_ws_manager = ConnectionManager()
    event_bus = ShipEventBus(deps.supervisor)
    deps.chat_ws_manager = chat_ws_manager
    deps.dl_ws_manager = dl_ws_manager
    deps.event_bus = event_bus
    if deps.notifications and hasattr(deps.notifications, "set_event_bus"):
        deps.notifications.set_event_bus(event_bus)

    audit_store = deps.action_event_store

    # Wire ActionGateway through the shared ToolRegistry so UI actions
    # and LLM tool calls use the exact same handler registration and
    # execution pipeline.
    from src.ai.assistant import AIAssistant
    tool_registry = deps.assistant.tool_registry if isinstance(deps.assistant, AIAssistant) else None
    action_gateway = ActionGateway(
        audit_store=audit_store, event_bus=event_bus,
        tool_registry=tool_registry,
        behavior_recorder=deps.behavior_recorder,
    )
    deps.action_gateway = action_gateway
    deps.tool_registry = tool_registry

    ActionRegistrationService(action_gateway, deps).register_all()

    downloader = deps.downloader
    stats_callback_result = downloader.set_stats_callback(
        DownloadStatsBroadcaster(dl_ws_manager, deps.supervisor, event_bus),
    )
    if inspect.iscoroutine(stats_callback_result):
        # Test doubles may mock this synchronous setter with AsyncMock.
        stats_callback_result.close()

    p = Path("src/web/static")
    p.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(p)), name="static")
    category_data = Path("data/categories")
    category_data.mkdir(parents=True, exist_ok=True)
    app.mount("/category-data", StaticFiles(directory=str(category_data)), name="category_data")

    @app.get("/api/live")
    async def live_probe():
        """Extremely lightweight liveness endpoint used by startup probes.

        Unlike /api/health, this must never touch browser, storage, provider,
        database, or library services. It proves that the FastAPI app itself is
        answering requests and avoids false readiness from a bare TCP accept.
        """
        return {"status": "ok", "service": "ljs-live"}

    @app.middleware("http")
    async def setup_redirect(request: Request, call_next):
        """Redirect unconfigured interactive pages to the setup wizard."""
        if deps.settings_manager.settings.setup_complete:
            return await call_next(request)
        allowed = ("/setup", "/api/setup", "/static", "/ws", "/api/providers",
                   "/api/comms", "/api/health", "/api/live", "/api/browser", "/api/jackett", "/api/soulseek", "/api/searxng", "/api/storage",
                   "/api/settings", "/api/web-search", "/api/web-research", "/api/personas", "/api/setup/language", "/api/trakt", "/category-data")
        if any(request.url.path == p or request.url.path.startswith(p + "/") for p in allowed):
            return await call_next(request)
        return RedirectResponse(url="/setup", status_code=302)

    @app.websocket("/ws/chat")
    async def chat_websocket(websocket: WebSocket):
        """Stream chat turns over the primary web WebSocket."""
        if not await verify_ws_auth(websocket, deps):
            return
        await chat_ws_manager.connect(websocket)
        session_id = None
        try:
            while True:
                data = await websocket.receive_text()
                try:
                    msg = json.loads(data)
                    message = msg.get("message", data)
                    session_id = msg.get("session_id", session_id) or f"web_{websocket.client.host}"
                except (json.JSONDecodeError, TypeError):
                    message = data
                    if not session_id:
                        session_id = f"web_{websocket.client.host}"
                try:
                    await _stream_chat_with_progress(websocket, deps, message, session_id)
                except WebSocketDisconnect:
                    raise
                except Exception as e:
                    logger.exception("WebSocket assistant error")
                    try:
                        formatter = getattr(deps.assistant, "format_chat_error", None)
                        content = (
                            formatter("websocket chat", e)
                            if callable(formatter)
                            else f"⚠️ **Error — websocket chat**\n**Details:** `{str(e)}`"
                        )
                        await websocket.send_json({"type": "error", "content": content})
                    except Exception:
                        pass
        except WebSocketDisconnect:
            chat_ws_manager.disconnect(websocket)

    @app.websocket("/ws/downloads")
    async def dl_stats_websocket(websocket: WebSocket):
        """Keep the downloads socket open while the manager pushes stats."""
        if not await verify_ws_auth(websocket, deps):
            return
        await dl_ws_manager.connect(websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            dl_ws_manager.disconnect(websocket)

    @app.websocket("/ws/events")
    async def ship_events_websocket(websocket: WebSocket):
        """Keep the ship-events socket open while the event bus pushes updates."""
        if not await verify_ws_auth(websocket, deps):
            return
        await event_bus.connect(websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            event_bus.disconnect(websocket)

    @app.post("/api/chat")
    async def chat(request: Request, _auth: bool = Depends(verify_auth)):
        """Run one non-streaming REST chat turn through the shared chat runner."""
        body = await request.json()
        message = body.get("message", "")
        session_id = body.get("session_id", "web_rest")
        if not message:
            return {"response": "No message provided"}
        runner = ChatSessionRunner(deps.assistant)
        response = await runner.collect_response(
            ChatTurnRequest(prompt=message, session_id=session_id),
        )
        return {"response": response}

    for router_cls in (
        DownloadsRouter, ActionsRouter, HealthRouter,
        PagesRouter, PersonasRouter, ProvidersRouter, SetupRouter, CategoriesRouter,
        SettingsRouter, CategoryItemsRouter, LibraryRouter, NotificationsRouter,
        UpgradesRouter, SuggestionsRouter, SystemRouter, StorageRouter, SharingRouter, ReleaseWatchesRouter,
    ):
        app.include_router(router_cls(deps).get_router())

    return app
