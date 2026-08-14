"""Round 290 regressions for complete HTTP readiness and deployed-build truth."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.core.models import LLMConfig, Settings
from src.utils.auth import AuthService
from src.web.app import create_app
from src.web.readiness import HTTPProbeResponse, LJSWebReadinessGate
from src.web.runtime_identity import RuntimeBuildIdentityResolver


async def _fragmented_live_server(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    build_id: str,
    asset_version: str,
) -> None:
    await reader.readuntil(b"\r\n\r\n")
    body = json.dumps({
        "status": "ok",
        "service": "ljs-live",
        "build_id": build_id,
        "asset_version": asset_version,
    }, separators=(",", ":")).encode("utf-8")
    headers = (
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode("ascii")
    writer.write(headers)
    await writer.drain()
    await asyncio.sleep(0.03)
    writer.write(body[:5])
    await writer.drain()
    await asyncio.sleep(0.03)
    writer.write(body[5:])
    await writer.drain()
    writer.close()
    await writer.wait_closed()


@pytest.mark.asyncio
async def test_probe_reads_body_when_headers_and_json_arrive_separately() -> None:
    gate = LJSWebReadinessGate()
    build_id = "build-fragmented"
    asset_version = "asset-fragmented"
    server = await asyncio.start_server(
        lambda reader, writer: _fragmented_live_server(
            reader,
            writer,
            build_id=build_id,
            asset_version=asset_version,
        ),
        "127.0.0.1",
        0,
    )
    port = server.sockets[0].getsockname()[1]
    try:
        response = await gate.probe("127.0.0.1", port, timeout=1.0)
        assert response.status_code == 200
        assert response.json_object()["build_id"] == build_id
        gate.validate(
            response,
            expected_build_id=build_id,
            expected_asset_version=asset_version,
        )
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_wait_accepts_fragmented_response_for_exact_running_build() -> None:
    gate = LJSWebReadinessGate()
    build_id = "build-current"
    asset_version = "asset-current"
    server = await asyncio.start_server(
        lambda reader, writer: _fragmented_live_server(
            reader,
            writer,
            build_id=build_id,
            asset_version=asset_version,
        ),
        "127.0.0.1",
        0,
    )
    port = server.sockets[0].getsockname()[1]
    server_task = asyncio.create_task(asyncio.sleep(2))
    try:
        await gate.wait(
            "127.0.0.1",
            port,
            server_task,
            expected_build_id=build_id,
            expected_asset_version=asset_version,
            timeout_seconds=1.0,
        )
    finally:
        server_task.cancel()
        server.close()
        await server.wait_closed()


def test_readiness_rejects_stale_backend_even_with_valid_ljs_marker() -> None:
    gate = LJSWebReadinessGate()
    body = json.dumps({
        "status": "ok",
        "service": "ljs-live",
        "build_id": "old-build",
        "asset_version": "old-assets",
    }).encode("utf-8")
    response = HTTPProbeResponse(200, {"content-length": str(len(body))}, body)
    with pytest.raises(ValueError, match="stale/different LJS backend"):
        gate.validate(
            response,
            expected_build_id="new-build",
            expected_asset_version="new-assets",
        )


def test_runtime_build_identity_changes_when_backend_source_changes(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "config" / "category-definitions").mkdir(parents=True)
    (tmp_path / "main.py").write_text("print('one')\n", encoding="utf-8")
    source = tmp_path / "src" / "worker.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    first = RuntimeBuildIdentityResolver(tmp_path).version
    source.write_text("VALUE = 2\n", encoding="utf-8")
    second = RuntimeBuildIdentityResolver(tmp_path).version
    assert first != second


def _minimal_app(*, runtime_build_id: str):
    settings = Settings(
        llm=LLMConfig(model="test", api_key="test"),
        tracked_items=[],
        download_dir="/tmp/test",
        web_password_hash=None,
        setup_complete=True,
        trakt_client_id="",
    )
    manager = MagicMock()
    manager.settings = settings
    downloader = MagicMock()
    downloader.set_stats_callback.return_value = None
    downloader.get_active_downloads = AsyncMock(return_value=[])
    downloader.get_recent_downloads = AsyncMock(return_value=[])
    database = MagicMock()
    database.media.get_all_item_progress = AsyncMock(return_value={})
    database.media.get_category_item_paused = AsyncMock(return_value=False)
    return create_app(
        runtime_build_id=runtime_build_id,
        settings_manager=manager,
        db=database,
        assistant=MagicMock(),
        downloader=downloader,
        notifications=MagicMock(),
        auth_service=AuthService(secret_key="test-secret"),
        llm_manager=MagicMock(),
        scanner=MagicMock(),
        conversation_manager=MagicMock(),
        behavior_tracker=MagicMock(),
        suggestion_compiler=MagicMock(),
        recommender=MagicMock(),
        release_group_tracker=MagicMock(),
        comms_registry=MagicMock(),
        torrent_racer=MagicMock(),
        browser_runtime=MagicMock(),
        jackett_manager=MagicMock(),
        scheduler=MagicMock(),
        supervisor=MagicMock(),
    )


def test_live_endpoint_reports_exact_backend_and_browser_bundle() -> None:
    app = _minimal_app(runtime_build_id="round290-test-build")
    with TestClient(app) as client:
        response = client.get("/api/live")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "ljs-live",
        "build_id": "round290-test-build",
        "asset_version": app.state.static_asset_version,
    }


def test_rendered_html_exposes_backend_build_for_user_diagnostics() -> None:
    app = _minimal_app(runtime_build_id="round290-visible-build")
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert '<meta name="ljs-build-id" content="round290-visible-build">' in response.text
    assert f'<meta name="ljs-asset-version" content="{app.state.static_asset_version}">' in response.text

@pytest.mark.asyncio
async def test_real_uvicorn_server_passes_exact_build_readiness() -> None:
    """Exercise the shipped FastAPI endpoint through a real TCP/uvicorn server."""
    import socket
    import uvicorn

    app = _minimal_app(runtime_build_id="round290-uvicorn-build")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]

    server = uvicorn.Server(uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="error",
        access_log=False,
    ))
    server_task = asyncio.create_task(server.serve())
    try:
        await LJSWebReadinessGate().wait(
            "127.0.0.1",
            port,
            server_task,
            expected_build_id=app.state.runtime_build_id,
            expected_asset_version=app.state.static_asset_version,
            timeout_seconds=3.0,
        )
        assert server.started is True
    finally:
        server.should_exit = True
        await asyncio.wait_for(server_task, timeout=3.0)
