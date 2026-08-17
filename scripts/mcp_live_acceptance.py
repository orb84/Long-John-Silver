"""Live, read-only acceptance probe for LJS's local MCP v2 endpoint.

This script intentionally depends on the real ``mcp>=2,<3`` and ``httpx2``
runtime installed by ``requirements.txt``. It does not fake the transport.
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import os
import secrets
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError


class MCPLiveAcceptance:
    """Exercise the real local Streamable-HTTP endpoint without intended writes."""

    EXPECTED_TOOLS = {
        "ljs.agent_message",
        "ljs.agent_cancel",
        "ljs.agent_close",
        "ljs.status",
        "ljs.capabilities",
        "ljs.library_list",
        "ljs.library_get",
        "ljs.downloads_list",
        "ljs.llm_get",
        "ljs.llm_test",
        "ljs.llm_set",
        "ljs.diagnostics_recent",
    }
    EXPECTED_RESOURCES = {
        "ljs://status",
        "ljs://capabilities",
        "ljs://library/summary",
        "ljs://downloads/active",
        "ljs://configuration/llm",
    }

    def __init__(self, *, url: str, token: str) -> None:
        self._url = url.rstrip("/")
        self._token = token
        self._evidence: dict[str, Any] = {
            "url": self._url,
            "checks": {},
        }
        self._assert_loopback_url()

    async def run(self) -> dict[str, Any]:
        """Run transport/auth/catalog/read/delegation checks and return evidence."""
        await self._check_unauthenticated_boundary()
        await self._check_non_loopback_boundary()
        async with httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=httpx2.Timeout(30.0, read=300.0),
            follow_redirects=True,
            trust_env=False,
        ) as http_client:
            transport = streamable_http_client(self._url, http_client=http_client)
            async with Client(transport) as client:
                await self._check_catalog(client)
                await self._check_static_resources(client)
                await self._check_reads(client)
                await self._check_read_only_write_denial(client)
                await self._check_read_only_probe_denial(client)
                await self._check_conversation_continuity(client)
        self._evidence["result"] = "PASS"
        return self._evidence

    async def _check_unauthenticated_boundary(self) -> None:
        async with httpx2.AsyncClient(follow_redirects=False, trust_env=False) as client:
            response = await client.post(self._url, content=b"{}")
        if response.status_code != 401:
            raise AssertionError(f"Expected unauthenticated MCP request to return 401, got {response.status_code}")
        self._evidence["checks"]["unauthenticated_boundary"] = {"status": 401}

    async def _check_non_loopback_boundary(self) -> None:
        lan_host = self._discover_non_loopback_ipv4()
        if lan_host is None:
            self._evidence["checks"]["non_loopback_boundary"] = {
                "status": "SKIP",
                "reason": "No non-loopback IPv4 address was discoverable on this host",
            }
            return
        parts = urlsplit(self._url)
        port = parts.port
        if port is None:
            port = 443 if parts.scheme == "https" else 80
        lan_url = urlunsplit((parts.scheme, f"{lan_host}:{port}", parts.path, "", ""))
        async with httpx2.AsyncClient(follow_redirects=False, trust_env=False) as client:
            response = await client.post(lan_url, content=b"{}")
        if response.status_code != 403:
            raise AssertionError(
                f"Expected non-loopback MCP request to return 403, got {response.status_code} via {lan_url}"
            )
        self._evidence["checks"]["non_loopback_boundary"] = {
            "status": 403,
            "tested_host": lan_host,
        }

    async def _check_catalog(self, client: Client) -> None:
        tools = await client.list_tools()
        tool_names = {tool.name for tool in tools.tools}
        if tool_names != self.EXPECTED_TOOLS:
            raise AssertionError(
                f"MCP tool surface drift: expected {sorted(self.EXPECTED_TOOLS)}, got {sorted(tool_names)}"
            )
        resources = await client.list_resources()
        resource_uris = {str(resource.uri) for resource in resources.resources}
        if resource_uris != self.EXPECTED_RESOURCES:
            raise AssertionError(
                f"MCP resource surface drift: expected {sorted(self.EXPECTED_RESOURCES)}, got {sorted(resource_uris)}"
            )
        self._evidence["checks"]["catalog"] = {
            "tools": sorted(tool_names),
            "resources": sorted(resource_uris),
        }

    async def _check_static_resources(self, client: Client) -> None:
        """Read every fixed resource so registration and request-principal propagation are proven live."""
        read: dict[str, int] = {}
        for uri in sorted(self.EXPECTED_RESOURCES):
            result = await client.read_resource(uri)
            if not result.contents:
                raise AssertionError(f"Static MCP resource {uri} returned no content")
            read[uri] = len(result.contents)
        self._evidence["checks"]["static_resources"] = read

    async def _check_reads(self, client: Client) -> None:
        status = self._structured(await client.call_tool("ljs.status", {}))
        capabilities = self._structured(await client.call_tool("ljs.capabilities", {}))
        library = self._structured(await client.call_tool("ljs.library_list", {"limit": 5}))
        downloads = self._structured(await client.call_tool("ljs.downloads_list", {"limit": 5}))
        llm = self._structured(await client.call_tool("ljs.llm_get", {}))
        self._evidence["checks"]["reads"] = {
            "status_ok": bool(status),
            "capabilities": capabilities.get("capabilities", []),
            "library_count": self._count_items(library),
            "download_count": self._count_items(downloads),
            "llm_configured_base": llm.get("configured_base", {}),
        }

    async def _check_read_only_write_denial(self, client: Client) -> None:
        llm_result = self._structured(await client.call_tool("ljs.llm_get", {}))
        configured = dict(llm_result.get("configured_base") or {})
        harmless_same_value = {
            key: configured[key]
            for key in ("provider", "model", "api_base")
            if key in configured
        }
        denied = False
        denial_detail = ""
        try:
            result = await client.call_tool("ljs.llm_set", {"values": harmless_same_value})
            denied = bool(result.is_error)
            denial_detail = self._content_text(result)
        except MCPError as exc:
            denied = True
            denial_detail = str(exc)
        if not denied:
            raise AssertionError("Default read-only MCP principal unexpectedly executed ljs.llm_set")
        if "config.llm.write" not in denial_detail:
            raise AssertionError(f"ljs.llm_set failed for the wrong reason: {denial_detail}")
        self._evidence["checks"]["read_only_write_denial"] = {
            "denied": True,
            "required_capability": "config.llm.write",
            "detail": denial_detail[:500],
        }

    async def _check_read_only_probe_denial(self, client: Client) -> None:
        """Prove a read-only credential cannot trigger authenticated provider egress."""
        denied = False
        detail = ""
        try:
            result = await client.call_tool("ljs.llm_test", {})
            denied = bool(result.is_error)
            detail = self._content_text(result)
        except MCPError as exc:
            denied = True
            detail = str(exc)
        if not denied or "config.llm.probe" not in detail:
            raise AssertionError(f"ljs.llm_test was not denied specifically by config.llm.probe: {detail}")
        self._evidence["checks"]["read_only_probe_denial"] = {
            "denied": True,
            "required_capability": "config.llm.probe",
            "detail": detail[:500],
        }

    async def _check_conversation_continuity(self, client: Client) -> None:
        nonce = f"LJS-MCP-{secrets.token_hex(8)}"
        first = self._structured(
            await client.call_tool(
                "ljs.agent_message",
                {
                    "message": (
                        f"Do not use tools or change anything. Remember this exact nonce for my next message: {nonce}. "
                        "Reply briefly to acknowledge it."
                    )
                },
            )
        )
        conversation_id = str(first.get("conversation_id") or "")
        if not conversation_id:
            raise AssertionError("ljs.agent_message did not return a conversation_id")
        # Continuation must survive a completely fresh HTTP client + MCP Client.
        async with httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=httpx2.Timeout(30.0, read=300.0),
            follow_redirects=True,
            trust_env=False,
        ) as fresh_http_client:
            fresh_transport = streamable_http_client(self._url, http_client=fresh_http_client)
            async with Client(fresh_transport) as fresh_client:
                second = self._structured(
                    await fresh_client.call_tool(
                        "ljs.agent_message",
                        {
                            "conversation_id": conversation_id,
                            "message": "Do not use tools. What exact nonce did I ask you to remember in my previous message?",
                        },
                    )
                )
                if str(second.get("conversation_id") or "") != conversation_id:
                    raise AssertionError("Conversation continuation returned a different conversation_id")
                second_message = str(second.get("message") or "")
                if nonce not in second_message:
                    raise AssertionError(
                        "Delegated conversation did not demonstrate prior-turn continuity: expected nonce missing from second reply"
                    )
                closed = self._structured(
                    await fresh_client.call_tool("ljs.agent_close", {"conversation_id": conversation_id})
                )
                if str(closed.get("status") or "") != "closed":
                    raise AssertionError(f"Delegated conversation cleanup failed: {closed}")
        self._evidence["checks"]["conversation_continuity"] = {
            "conversation_id": conversation_id,
            "first_turn_id": first.get("turn_id"),
            "second_turn_id": second.get("turn_id"),
            "nonce_recalled": True,
            "fresh_client": True,
            "closed": True,
        }

    def _assert_loopback_url(self) -> None:
        parts = urlsplit(self._url)
        host = parts.hostname or ""
        if host.casefold() == "localhost":
            return
        try:
            if ipaddress.ip_address(host).is_loopback:
                return
        except ValueError:
            pass
        raise ValueError("Live acceptance must target the loopback MCP URL, e.g. http://127.0.0.1:8088/mcp")

    @staticmethod
    def _discover_non_loopback_ipv4() -> str | None:
        candidates: set[str] = set()
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                candidates.add(str(info[4][0]))
        except OSError:
            pass
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.connect(("192.0.2.1", 9))
                candidates.add(str(sock.getsockname()[0]))
            finally:
                sock.close()
        except OSError:
            pass
        for candidate in sorted(candidates):
            try:
                address = ipaddress.ip_address(candidate)
            except ValueError:
                continue
            if address.version == 4 and not address.is_loopback and not address.is_unspecified:
                return candidate
        return None

    @staticmethod
    def _structured(result: Any) -> dict[str, Any]:
        if getattr(result, "is_error", False):
            raise AssertionError(f"MCP tool returned an error: {MCPLiveAcceptance._content_text(result)}")
        payload = getattr(result, "structured_content", None)
        if not isinstance(payload, dict):
            raise AssertionError(f"Expected structured MCP tool output, got: {payload!r}")
        return payload

    @staticmethod
    def _content_text(result: Any) -> str:
        parts: list[str] = []
        for block in getattr(result, "content", []) or []:
            text = getattr(block, "text", None)
            if text is not None:
                parts.append(str(text))
        return "\n".join(parts)

    @staticmethod
    def _count_items(payload: dict[str, Any]) -> int:
        for key in ("items", "downloads", "results", "active"):
            value = payload.get(key)
            if isinstance(value, list):
                return len(value)
        count = payload.get("count")
        return int(count) if isinstance(count, int) else 0


class MCPLiveAcceptanceCLI:
    """Small command-line boundary for the live acceptance probe."""

    @staticmethod
    def run() -> int:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--url", default=os.getenv("LJS_MCP_URL", "http://127.0.0.1:8088/mcp"))
        parser.add_argument("--token", default=os.getenv("LJS_MCP_ACCEPTANCE_TOKEN", ""))
        parser.add_argument("--evidence", default="mcp_live_acceptance_evidence.json")
        args = parser.parse_args()
        if not args.token:
            parser.error("Provide --token or set LJS_MCP_ACCEPTANCE_TOKEN")
        evidence = asyncio.run(MCPLiveAcceptance(url=args.url, token=args.token).run())
        evidence_path = Path(args.evidence)
        evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
        print("MCP_LIVE_ACCEPTANCE_PASS")
        print(f"evidence={evidence_path.resolve()}")
        return 0


if __name__ == "__main__":
    raise SystemExit(MCPLiveAcceptanceCLI.run())
