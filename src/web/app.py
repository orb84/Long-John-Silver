"""
FastAPI composition root for LJS.
"""

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any
import uuid

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
from src.web.llm_diagnostics import LLMActivityBroadcaster
from src.web.static_assets import BrowserBundleCoherenceMiddleware, StaticAssetVersionResolver
from src.web.runtime_identity import RuntimeBuildIdentityResolver
from src.web.chat_state import ChatTurnStateBroadcaster
from src.ai.chat_turn_registry import ActiveChatTurn, ChatTurnRegistry
from src.ai.chat_session_runner import ChatSessionRunner, ChatTurnRequest
from src.ai.agent_delegation import AgentDelegationService
from src.core.conversation_handle import ConversationHandleService
from src.core.public_control_plane import (
    PublicDiagnosticsService,
    PublicDownloadService,
    PublicLibraryService,
    PublicLLMConfigurationService,
    PublicStatusService,
)
from src.core.public_control_plane_facade import PublicControlPlane
from src.integrations.mcp_auth import MCPPrincipalResolver
from src.integrations.mcp_configuration import MCPIntegrationSettings
from src.integrations.mcp_runtime import MCPHostRuntime
from src.integrations.mcp_server import MCPServerAdapter
from src.utils.async_boundary import AsyncBoundary


async def _stream_chat_with_progress(
    websocket: WebSocket,
    deps: WebDependencies,
    message: str,
    session_id: str,
) -> None:
    """Adapt the shared assistant chat runner to the browser websocket."""
    turn_id = str(getattr(websocket.state, "chat_turn_id", "") or "")
    send_lock = getattr(websocket.state, "chat_send_lock", None)
    runner = ChatSessionRunner(deps.assistant)
    request = ChatTurnRequest(prompt=message, session_id=session_id, turn_id=turn_id)
    async for event in runner.run_events(request):
        if send_lock is None:
            # Preserve the direct adapter contract for callers that do not need
            # concurrent receiver/sender coordination.
            await websocket.send_json({"type": event.type, "content": event.content})
        else:
            payload = {"type": event.type, "content": event.content, "turn_id": turn_id}
            async with send_lock:
                await websocket.send_json(payload)


def create_app(*, runtime_build_id: str | None = None, **kwargs: Any) -> FastAPI:
    """Create and configure the FastAPI application."""
    deps = WebDependencies(**kwargs)
    mcp_runtime = MCPHostRuntime()
    app = FastAPI(title="LJS Quartermaster's Deck", lifespan=mcp_runtime.lifespan)
    deps.templates = Jinja2Templates(directory="src/web/templates")
    asset_versions = StaticAssetVersionResolver(Path("src/web/static"))
    deps.templates.env.globals["static_asset"] = asset_versions.url
    deps.templates.env.globals["static_asset_version"] = asset_versions.version
    app.state.static_asset_version = asset_versions.version
    project_root = Path(__file__).resolve().parents[2]
    app.state.runtime_build_id = runtime_build_id or RuntimeBuildIdentityResolver(project_root).version
    deps.templates.env.globals["runtime_build_id"] = app.state.runtime_build_id
    app.add_middleware(
        BrowserBundleCoherenceMiddleware,
        asset_version=asset_versions.version,
    )
    app.state.deps = deps

    chat_ws_manager = ConnectionManager()
    dl_ws_manager = ConnectionManager()
    event_bus = ShipEventBus(deps.supervisor)
    chat_state = ChatTurnStateBroadcaster(event_bus)
    chat_turns = ChatTurnRegistry()
    app.state.chat_turn_registry = chat_turns
    deps.chat_ws_manager = chat_ws_manager
    deps.dl_ws_manager = dl_ws_manager
    deps.event_bus = event_bus

    async def _audit_turn(
        event: str, *, session_id: str, turn_id: str, transport: str,
        detail: str | None = None, message: str | None = None, state: str | None = None,
    ) -> None:
        """Record chat lifecycle evidence without making logging a failure dependency."""
        turn_logger = getattr(deps, "turn_logger", None)
        if turn_logger is None:
            return
        try:
            await turn_logger.log_event(
                event, session_id=session_id, turn_id=turn_id, transport=transport,
                detail=detail, message=message, state=state,
            )
        except Exception as exc:
            logger.warning("Failed to write chat turn audit event {} for {}: {}", event, turn_id, exc)
    if deps.llm_activity_monitor and hasattr(deps.llm_activity_monitor, "set_event_sink"):
        deps.llm_activity_monitor.set_event_sink(LLMActivityBroadcaster(event_bus))
    if deps.notifications and hasattr(deps.notifications, "set_event_bus"):
        notification_callback_result = deps.notifications.set_event_bus(event_bus)
        # Test doubles may expose this synchronous setter as AsyncMock.
        AsyncBoundary.close_if_awaitable(notification_callback_result)

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

    mcp_settings = MCPIntegrationSettings.from_environment()
    app.state.mcp_adapter = None
    if mcp_settings.enabled:
        control_plane = PublicControlPlane(
            agent=AgentDelegationService(
                ChatSessionRunner(deps.assistant),
                chat_turns,
                ConversationHandleService(deps.db),
            ),
            status=PublicStatusService(deps.storage_monitor),
            library=PublicLibraryService(
                settings_manager=deps.settings_manager,
                database=deps.db,
                downloader=deps.downloader,
                category_registry=deps.category_registry,
            ),
            downloads=PublicDownloadService(deps.downloader),
            llm=PublicLLMConfigurationService(
                settings_manager=deps.settings_manager,
                assistant=deps.assistant,
                llm_manager=deps.llm_manager,
                action_gateway=action_gateway,
            ),
            diagnostics=PublicDiagnosticsService(deps.llm_activity_monitor),
        )
        mcp_adapter = MCPServerAdapter(
            control_plane=control_plane,
            principal_resolver=MCPPrincipalResolver(
                settings=mcp_settings,
                auth_service=deps.auth_service,
                database=deps.db,
            ),
        )
        mcp_runtime.configure(mcp_adapter)
        app.mount(mcp_settings.mount_path, mcp_adapter.asgi_app, name="mcp")
        app.state.mcp_adapter = mcp_adapter

    downloader = deps.downloader
    stats_callback_result = downloader.set_stats_callback(
        DownloadStatsBroadcaster(dl_ws_manager, deps.supervisor, event_bus),
    )
    # Test doubles may mock this synchronous setter with AsyncMock.
    AsyncBoundary.close_if_awaitable(stats_callback_result)

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
        return {
            "status": "ok",
            "service": "ljs-live",
            "build_id": app.state.runtime_build_id,
            "asset_version": app.state.static_asset_version,
        }

    @app.middleware("http")
    async def setup_redirect(request: Request, call_next):
        """Redirect unconfigured interactive pages to the setup wizard."""
        if deps.settings_manager.settings.setup_complete:
            return await call_next(request)
        allowed = ("/setup", "/api/setup", "/static", "/ws", "/api/providers",
                   "/api/comms", "/api/health", "/api/live", "/api/browser", "/api/jackett", "/api/soulseek", "/api/searxng", "/api/storage",
                   "/api/settings", "/api/web-search", "/api/web-research", "/api/personas", "/api/setup/language", "/api/trakt", "/category-data", "/mcp")
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
        owned_turn: ActiveChatTurn | None = None
        send_lock = asyncio.Lock()

        async def _send(payload: dict[str, Any]) -> None:
            async with send_lock:
                await websocket.send_json(payload)

        async def _run_turn(message: str, session_id: str, turn_id: str) -> None:
            try:
                await _audit_turn(
                    "turn_started", session_id=session_id, turn_id=turn_id, transport="websocket",
                    message=message, state="working",
                )
                await _send({"type": "started", "content": "", "turn_id": turn_id})
                chat_state.publish(state="working", session_id=session_id, turn_id=turn_id)
                websocket.state.chat_turn_id = turn_id
                websocket.state.chat_send_lock = send_lock
                await _stream_chat_with_progress(websocket, deps, message, session_id)
            except asyncio.CancelledError:
                await _audit_turn(
                    "turn_cancelled", session_id=session_id, turn_id=turn_id, transport="websocket",
                    detail="Assistant task received cancellation and exited.", state="cancelled",
                )
                chat_state.publish(
                    state="cancelled", session_id=session_id, turn_id=turn_id,
                    message="Request stopped.",
                )
                with contextlib.suppress(Exception):
                    await _send({
                        "type": "cancelled",
                        "content": "Request stopped.",
                        "turn_id": turn_id,
                    })
                raise
            except WebSocketDisconnect:
                await _audit_turn(
                    "transport_disconnected", session_id=session_id, turn_id=turn_id, transport="websocket",
                    detail="WebSocket disconnected while turn was active.", state="disconnected",
                )
                raise
            except Exception as e:
                await _audit_turn(
                    "turn_failed", session_id=session_id, turn_id=turn_id, transport="websocket",
                    detail=str(e), state="failed",
                )
                logger.exception("WebSocket assistant error")
                formatter = getattr(deps.assistant, "format_chat_error", None)
                content = (
                    formatter("websocket chat", e)
                    if callable(formatter)
                    else f"⚠️ **Error — websocket chat**\n**Details:** `{str(e)}`"
                )
                chat_state.publish(
                    state="failed", session_id=session_id, turn_id=turn_id,
                    message=str(e),
                )
                with contextlib.suppress(Exception):
                    await _send({"type": "error", "content": content, "turn_id": turn_id})
            else:
                await _audit_turn(
                    "turn_completed", session_id=session_id, turn_id=turn_id, transport="websocket",
                    detail="Assistant turn completed normally.", state="idle",
                )
                chat_state.publish(state="idle", session_id=session_id, turn_id=turn_id)
            finally:
                await chat_turns.release(session_id, turn_id)
        try:
            while True:
                data = await websocket.receive_text()
                try:
                    msg = json.loads(data)
                    message_type = str(msg.get("type") or "message").strip().lower()
                    message = msg.get("message", data)
                    session_id = msg.get("session_id", session_id) or f"web_{websocket.client.host}"
                except (json.JSONDecodeError, TypeError):
                    msg = {}
                    message_type = "message"
                    message = data
                    if not session_id:
                        session_id = f"web_{websocket.client.host}"
                if (
                    websocket.headers.get("origin")
                    and not asset_versions.matches(msg.get("client_asset_version"))
                ):
                    stale_turn_id = str(msg.get("turn_id") or "")
                    await _send({
                        "type": "error",
                        "turn_id": stale_turn_id,
                        "content": (
                            "The browser interface is older than the running LJS backend. "
                            "Reload this page before sending another request."
                        ),
                    })
                    chat_state.publish(
                        state="failed",
                        session_id=session_id,
                        turn_id=stale_turn_id,
                        message="Browser bundle is out of date; reload required.",
                    )
                    continue
                if message_type == "cancel":
                    requested_turn_id = str(msg.get("turn_id") or "")
                    await _audit_turn(
                        "cancel_requested", session_id=str(session_id), turn_id=requested_turn_id,
                        transport="websocket", detail="User requested Stop/Cancel.", state="cancelling",
                    )
                    chat_state.publish(
                        state="stopping", session_id=session_id, turn_id=requested_turn_id,
                        message="Stopping request…",
                    )
                    cancelled, settled = await chat_turns.cancel_and_wait(
                        str(session_id), requested_turn_id or None, timeout_seconds=5.0,
                    )
                    if cancelled is None:
                        await _audit_turn(
                            "cancel_not_matched", session_id=str(session_id), turn_id=requested_turn_id,
                            transport="websocket", detail="No matching active turn existed.", state="idle",
                        )
                        current = await chat_turns.active(str(session_id))
                        current_turn_id = current.turn_id if current else ""
                        chat_state.publish(
                            state="idle", session_id=session_id,
                            turn_id=requested_turn_id or current_turn_id,
                            message="No matching active request to stop.",
                        )
                        await _send({
                            "type": "cancelled",
                            "content": "No matching active request to stop.",
                            "turn_id": requested_turn_id or current_turn_id,
                        })
                    else:
                        await _audit_turn(
                            "cancel_settled" if settled else "cancel_still_unwinding",
                            session_id=cancelled.session_id, turn_id=cancelled.turn_id,
                            transport="websocket",
                            detail=(
                                "Assistant task exited after cancellation."
                                if settled else
                                "Cancellation was signalled but the assistant/provider task did not unwind within 5 seconds."
                            ),
                            state="cancelled" if settled else "cancelling",
                        )
                        if not settled:
                            chat_state.publish(
                                state="stopping", session_id=cancelled.session_id, turn_id=cancelled.turn_id,
                                message="Stop requested; provider cleanup is still finishing…",
                            )
                    continue

                turn_id = str(msg.get("turn_id") or uuid.uuid4().hex)
                await _audit_turn(
                    "turn_received", session_id=str(session_id), turn_id=turn_id, transport="websocket",
                    message=str(message or ""), state="received",
                )
                started, active = await chat_turns.start(
                    str(session_id),
                    turn_id,
                    lambda: _run_turn(str(message or ""), str(session_id), turn_id),
                    task_name=f"web-chat-turn-{turn_id}",
                )
                if not started:
                    await _audit_turn(
                        "turn_rejected_busy", session_id=str(session_id), turn_id=turn_id, transport="websocket",
                        detail=f"Active turn {active.turn_id} still owns the session.", state="working",
                    )
                    chat_state.publish(
                        state="working", session_id=session_id, turn_id=active.turn_id,
                        message="The previous request is still running.",
                    )
                    await _send({
                        "type": "busy",
                        "content": "The previous request is still running. Stop it before sending another.",
                        "turn_id": active.turn_id,
                    })
                    continue
                owned_turn = active
        except WebSocketDisconnect:
            if owned_turn is not None and not owned_turn.task.done():
                await _audit_turn(
                    "cancel_requested", session_id=owned_turn.session_id, turn_id=owned_turn.turn_id,
                    transport="websocket_disconnect", detail="Transport disconnected; cancelling owned turn.", state="cancelling",
                )
                cancelled = await chat_turns.cancel(owned_turn.session_id, owned_turn.turn_id)
                if cancelled is not None:
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await cancelled.task
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
        turn_id = str(body.get("turn_id") or uuid.uuid4().hex)
        if (
            request.headers.get("origin")
            and not asset_versions.matches(body.get("client_asset_version"))
        ):
            return {
                "response": (
                    "The browser interface is older than the running LJS backend. "
                    "Reload this page before sending another request."
                ),
                "reload_required": True,
            }
        if not message:
            return {"response": "No message provided"}
        runner = ChatSessionRunner(deps.assistant)

        async def _collect_rest_turn() -> str:
            try:
                await _audit_turn(
                    "turn_started", session_id=str(session_id), turn_id=turn_id, transport="rest",
                    message=str(message or ""), state="working",
                )
                chat_state.publish(state="working", session_id=session_id, turn_id=turn_id)
                return await runner.collect_response(
                    ChatTurnRequest(prompt=message, session_id=session_id, turn_id=turn_id),
                )
            finally:
                await chat_turns.release(str(session_id), turn_id)

        await _audit_turn(
            "turn_received", session_id=str(session_id), turn_id=turn_id, transport="rest",
            message=str(message or ""), state="received",
        )
        started, active = await chat_turns.start(
            str(session_id), turn_id, _collect_rest_turn, task_name=f"rest-chat-turn-{turn_id}"
        )
        if not started:
            await _audit_turn(
                "turn_rejected_busy", session_id=str(session_id), turn_id=turn_id, transport="rest",
                detail=f"Active turn {active.turn_id} still owns the session.", state="working",
            )
            return {
                "response": "The previous request is still running. Stop it before sending another.",
                "busy": True,
                "turn_id": active.turn_id,
            }
        try:
            response = await active.task
        except asyncio.CancelledError:
            await _audit_turn(
                "turn_cancelled", session_id=str(session_id), turn_id=turn_id, transport="rest",
                detail="Assistant task received cancellation and exited.", state="cancelled",
            )
            chat_state.publish(state="cancelled", session_id=session_id, turn_id=turn_id, message="Request stopped.")
            return {"response": "Request stopped.", "cancelled": True, "turn_id": turn_id}
        except Exception as exc:
            await _audit_turn(
                "turn_failed", session_id=str(session_id), turn_id=turn_id, transport="rest",
                detail=str(exc), state="failed",
            )
            raise
        else:
            await _audit_turn(
                "turn_completed", session_id=str(session_id), turn_id=turn_id, transport="rest",
                detail="Assistant turn completed normally.", state="idle",
            )
            chat_state.publish(state="idle", session_id=session_id, turn_id=turn_id)
            return {"response": response, "turn_id": turn_id}

    @app.post("/api/chat/cancel")
    async def cancel_chat(request: Request, _auth: bool = Depends(verify_auth)):
        """Cancel the active chat turn even when the browser is using REST fallback."""
        body = await request.json()
        session_id = str(body.get("session_id") or "web_rest")
        turn_id = str(body.get("turn_id") or "")
        if (
            request.headers.get("origin")
            and not asset_versions.matches(body.get("client_asset_version"))
        ):
            return {
                "ok": False,
                "cancelled": False,
                "reload_required": True,
                "error": "Browser bundle is out of date.",
            }
        await _audit_turn(
            "cancel_requested", session_id=session_id, turn_id=turn_id, transport="rest_cancel",
            detail="User requested Stop/Cancel through REST fallback.", state="cancelling",
        )
        chat_state.publish(
            state="stopping", session_id=session_id, turn_id=turn_id, message="Stopping request…",
        )
        cancelled, settled = await chat_turns.cancel_and_wait(
            session_id, turn_id or None, timeout_seconds=5.0,
        )
        if cancelled is None:
            await _audit_turn(
                "cancel_not_matched", session_id=session_id, turn_id=turn_id, transport="rest_cancel",
                detail="No matching active turn existed.", state="idle",
            )
            chat_state.publish(
                state="idle", session_id=session_id, turn_id=turn_id,
                message="No matching active request to stop.",
            )
            return {"ok": False, "cancelled": False, "turn_id": turn_id}
        await _audit_turn(
            "cancel_settled" if settled else "cancel_still_unwinding",
            session_id=cancelled.session_id, turn_id=cancelled.turn_id, transport="rest_cancel",
            detail=(
                "Assistant task exited after cancellation."
                if settled else
                "Cancellation was signalled but the assistant/provider task did not unwind within 5 seconds."
            ),
            state="cancelled" if settled else "cancelling",
        )
        if not settled:
            chat_state.publish(
                state="stopping", session_id=cancelled.session_id, turn_id=cancelled.turn_id,
                message="Stop requested; provider cleanup is still finishing…",
            )
        logger.info(
            "Chat cancellation requested through REST: session={} turn={} settled={}",
            session_id, cancelled.turn_id, settled,
        )
        return {
            "ok": True,
            "cancelled": bool(settled),
            "cancellation_requested": True,
            "settled": bool(settled),
            "turn_id": cancelled.turn_id,
        }

    for router_cls in (
        DownloadsRouter, ActionsRouter, HealthRouter,
        PagesRouter, PersonasRouter, ProvidersRouter, SetupRouter, CategoriesRouter,
        SettingsRouter, CategoryItemsRouter, LibraryRouter, NotificationsRouter,
        UpgradesRouter, SuggestionsRouter, SystemRouter, StorageRouter, SharingRouter, ReleaseWatchesRouter,
    ):
        app.include_router(router_cls(deps).get_router())

    return app
