"""Bounded HTTP readiness probing for the LJS web process.

A TCP read is not message-framed: headers and body may arrive in separate
packets even for a 36-byte JSON response.  The readiness gate therefore parses
the complete HTTP response and validates the exact runtime/build identity,
rather than searching an arbitrary first read for a marker string.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class HTTPProbeResponse:
    """Complete, bounded HTTP response returned by the startup probe."""

    status_code: int
    headers: dict[str, str]
    body: bytes

    def json_object(self) -> dict[str, Any]:
        """Decode the bounded body and require a JSON object payload."""
        parsed = json.loads(self.body.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("readiness response JSON must be an object")
        return parsed

    def preview(self, limit: int = 180) -> str:
        """Return a bounded UTF-8-safe body preview for startup errors."""
        text = self.body.decode("utf-8", errors="replace")
        return text[:limit]


class LJSWebReadinessGate:
    """Wait for the exact LJS build launched by the current process."""

    _MAX_HEADER_BYTES = 32 * 1024
    _MAX_BODY_BYTES = 64 * 1024

    async def wait(
        self,
        host: str,
        port: int,
        server_task: asyncio.Task[Any],
        *,
        expected_build_id: str,
        expected_asset_version: str,
        timeout_seconds: float = 15.0,
    ) -> None:
        """Wait until the launched server answers with the expected identities."""
        connect_host = "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        last_error: Exception | str | None = None

        while asyncio.get_running_loop().time() < deadline:
            if server_task.done():
                self._raise_early_server_exit(server_task, port)
            try:
                remaining = max(0.05, min(0.75, deadline - asyncio.get_running_loop().time()))
                response = await self.probe(connect_host, port, timeout=remaining)
                self.validate(
                    response,
                    expected_build_id=expected_build_id,
                    expected_asset_version=expected_asset_version,
                )
                return
            except Exception as exc:  # noqa: BLE001 - startup readiness intentionally retries.
                last_error = exc
            await asyncio.sleep(0.1)

        raise RuntimeError(
            f"web server did not answer the expected LJS build on {connect_host}:{port} "
            f"within {timeout_seconds:.1f}s; expected build={expected_build_id} "
            f"assets={expected_asset_version}; last probe error: {last_error}"
        )

    async def probe(self, host: str, port: int, *, timeout: float = 0.75) -> HTTPProbeResponse:
        """Read and parse one complete bounded response from ``/api/live``."""
        deadline = asyncio.get_running_loop().time() + timeout
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=self._remaining(deadline)
        )
        try:
            request = (
                "GET /api/live HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                "Accept: application/json\r\n"
                "Connection: close\r\n"
                "User-Agent: ljs-startup-probe\r\n"
                "\r\n"
            )
            writer.write(request.encode("ascii"))
            await asyncio.wait_for(writer.drain(), timeout=self._remaining(deadline))
            header_block = await asyncio.wait_for(
                reader.readuntil(b"\r\n\r\n"), timeout=self._remaining(deadline)
            )
            if len(header_block) > self._MAX_HEADER_BYTES:
                raise ValueError("readiness response headers exceed safety limit")
            status_code, headers = self._parse_headers(header_block)
            body = await self._read_body(reader, headers, deadline)
            return HTTPProbeResponse(status_code=status_code, headers=headers, body=body)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _read_body(
        self,
        reader: asyncio.StreamReader,
        headers: dict[str, str],
        deadline: float,
    ) -> bytes:
        raw_length = headers.get("content-length")
        if raw_length is not None:
            try:
                content_length = int(raw_length)
            except ValueError as exc:
                raise ValueError("invalid readiness response content-length") from exc
            if content_length < 0 or content_length > self._MAX_BODY_BYTES:
                raise ValueError("readiness response body exceeds safety limit")
            if content_length == 0:
                return b""
            return await asyncio.wait_for(
                reader.readexactly(content_length), timeout=self._remaining(deadline)
            )

        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=self._remaining(deadline))
            if not chunk:
                return b"".join(chunks)
            total += len(chunk)
            if total > self._MAX_BODY_BYTES:
                raise ValueError("readiness response body exceeds safety limit")
            chunks.append(chunk)

    def _parse_headers(self, header_block: bytes) -> tuple[int, dict[str, str]]:
        text = header_block.decode("iso-8859-1")
        lines = text.split("\r\n")
        status_parts = lines[0].split(" ", 2)
        if len(status_parts) < 2 or not status_parts[1].isdigit():
            raise ValueError(f"invalid readiness HTTP status line: {lines[0]!r}")
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if not line:
                continue
            if ":" not in line:
                raise ValueError(f"invalid readiness HTTP header: {line!r}")
            name, value = line.split(":", 1)
            headers[name.strip().casefold()] = value.strip()
        return int(status_parts[1]), headers

    def validate(
        self,
        response: HTTPProbeResponse,
        *,
        expected_build_id: str,
        expected_asset_version: str,
    ) -> None:
        """Validate liveness markers plus exact backend and browser identities."""
        if response.status_code != 200:
            raise ValueError(f"readiness endpoint returned HTTP {response.status_code}")
        payload = response.json_object()
        if payload.get("service") != "ljs-live" or payload.get("status") != "ok":
            raise ValueError(f"unexpected readiness service payload: {response.preview()!r}")
        actual_build = str(payload.get("build_id") or "")
        actual_assets = str(payload.get("asset_version") or "")
        if actual_build != expected_build_id:
            raise ValueError(
                f"stale/different LJS backend answered readiness: "
                f"expected build={expected_build_id}, received build={actual_build or '<missing>'}"
            )
        if actual_assets != expected_asset_version:
            raise ValueError(
                f"stale/different browser bundle answered readiness: "
                f"expected assets={expected_asset_version}, received assets={actual_assets or '<missing>'}"
            )

    def _raise_early_server_exit(self, server_task: asyncio.Task[Any], port: int) -> None:
        exc = server_task.exception()
        if exc:
            raise RuntimeError(
                f"web server task exited before answering /api/live on port {port}: {exc}"
            ) from exc
        raise RuntimeError(f"web server task exited before answering /api/live on port {port}")

    def _remaining(self, deadline: float) -> float:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError("readiness probe timed out")
        return remaining
