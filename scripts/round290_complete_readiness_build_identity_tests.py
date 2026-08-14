"""Executable release checks for Round 290 startup/readiness integrity."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.web.readiness import HTTPProbeResponse, LJSWebReadinessGate
from src.web.runtime_identity import RuntimeBuildIdentityResolver


class Round290Checks:
    """Reproduce the split-response incident and stale-build rejection."""

    @classmethod
    async def run(cls) -> None:
        await cls._check_fragmented_http_body()
        cls._check_stale_build_rejected()
        cls._check_build_identity_tracks_backend()
        cls._check_composition_contract()

    @classmethod
    async def _check_fragmented_http_body(cls) -> None:
        build_id = "round290-build"
        asset_version = "round290-assets"

        async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await reader.readuntil(b"\r\n\r\n")
            body = json.dumps({
                "status": "ok",
                "service": "ljs-live",
                "build_id": build_id,
                "asset_version": asset_version,
            }, separators=(",", ":")).encode("utf-8")
            writer.write((
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii"))
            await writer.drain()
            await asyncio.sleep(0.02)
            writer.write(body[:4])
            await writer.drain()
            await asyncio.sleep(0.02)
            writer.write(body[4:])
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        server_task = asyncio.create_task(asyncio.sleep(2))
        try:
            await LJSWebReadinessGate().wait(
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

    @staticmethod
    def _check_stale_build_rejected() -> None:
        body = json.dumps({
            "status": "ok",
            "service": "ljs-live",
            "build_id": "old-build",
            "asset_version": "old-assets",
        }).encode("utf-8")
        response = HTTPProbeResponse(200, {"content-length": str(len(body))}, body)
        try:
            LJSWebReadinessGate().validate(
                response,
                expected_build_id="new-build",
                expected_asset_version="new-assets",
            )
        except ValueError as exc:
            assert "stale/different LJS backend" in str(exc)
        else:
            raise AssertionError("stale backend was incorrectly accepted")

    @staticmethod
    def _check_build_identity_tracks_backend() -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "src").mkdir()
            (root / "config" / "category-definitions").mkdir(parents=True)
            (root / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
            source = root / "src" / "worker.py"
            source.write_text("VALUE = 1\n", encoding="utf-8")
            first = RuntimeBuildIdentityResolver(root).version
            source.write_text("VALUE = 2\n", encoding="utf-8")
            second = RuntimeBuildIdentityResolver(root).version
            assert first != second

    @staticmethod
    def _check_composition_contract() -> None:
        app_source = Path("src/web/app.py").read_text(encoding="utf-8")
        main_source = Path("main.py").read_text(encoding="utf-8")
        base_template = Path("src/web/templates/base.html").read_text(encoding="utf-8")
        assert '"build_id": app.state.runtime_build_id' in app_source
        assert '"asset_version": app.state.static_asset_version' in app_source
        assert "expected_build_id=app.state.runtime_build_id" in main_source
        assert "expected_asset_version=app.state.static_asset_version" in main_source
        assert 'meta name="ljs-build-id"' in base_template


if __name__ == "__main__":
    asyncio.run(Round290Checks.run())
    print("ROUND290_COMPLETE_READINESS_BUILD_IDENTITY_PASS")
