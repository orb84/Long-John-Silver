"""Small async client for the slskd HTTP API.

The wrapper intentionally stays conservative: it returns recoverable result
dicts instead of raising through the agent turn, and it keeps Soulseek transfers
separate from torrent/magnet queueing.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx
from loguru import logger

from src.core.models import SoulseekSettings


@dataclass(frozen=True)
class SoulseekCandidate:
    """LLM-facing normalized slskd search candidate."""

    username: str
    filename: str
    size_bytes: int | None = None
    extension: str = ""
    bitrate: int | None = None
    bit_depth: int | None = None
    sample_rate: int | None = None
    length_seconds: int | None = None
    is_locked: bool = False
    has_free_upload_slot: bool | None = None
    queue_length: int | None = None
    upload_speed: int | None = None
    source: str = "slskd"
    raw: dict[str, Any] = field(default_factory=dict)

    def as_public_dict(self, index: int | None = None) -> dict[str, Any]:
        """Return a compact dict suitable for LLM/tool responses."""
        item = {
            "source": self.source,
            "username": self.username,
            "filename": self.filename,
            "folder": _remote_parent(self.filename),
            "size_bytes": self.size_bytes,
            "extension": self.extension,
            "bitrate": self.bitrate,
            "bit_depth": self.bit_depth,
            "sample_rate": self.sample_rate,
            "length_seconds": self.length_seconds,
            "is_locked": self.is_locked,
            "has_free_upload_slot": self.has_free_upload_slot,
            "queue_length": self.queue_length,
            "upload_speed": self.upload_speed,
        }
        if index is not None:
            item["index"] = index
        return {k: v for k, v in item.items() if v not in (None, "", [], {})}


@dataclass(frozen=True)
class SearchNormalizationStats:
    """Diagnostics for Soulseek candidate filtering.

    slskd may expose locked/private files alongside queueable results.  LJS uses
    these stats to make it explicit that inaccessible files were filtered out
    instead of being silently shown to the LLM or user as selectable targets.
    """

    total_file_rows: int = 0
    candidate_files: int = 0
    filtered_locked: int = 0
    filtered_private: int = 0
    filtered_duplicates: int = 0

    def as_public_dict(self) -> dict[str, int]:
        """Return serializable filtering counters for tool responses and UI diagnostics."""
        return {
            "total_file_rows": int(self.total_file_rows),
            "candidate_files": int(self.candidate_files),
            "filtered_locked": int(self.filtered_locked),
            "filtered_private": int(self.filtered_private),
            "filtered_duplicates": int(self.filtered_duplicates),
        }


class SlskdClient:
    """Conservative slskd API wrapper used by LJS tools."""

    def __init__(self, settings: SoulseekSettings, *, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._client = client

    @property
    def configured(self) -> bool:
        """Return whether enough settings exist to call slskd."""
        return bool(self.settings.api_configured)

    def _base_url(self) -> str:
        base = self.settings.host.rstrip("/")
        url_base = self.settings.url_base.rstrip("/")
        if url_base and url_base != "/":
            base += url_base
        return base.rstrip("/")

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        if not self.configured:
            return {"ok": False, "recoverable": True, "error_code": "SLSKD_NOT_CONFIGURED", "error": "Soulseek/slskd is disabled or missing an API key."}
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.setdefault("X-API-Key", self.settings.api_key)
        url = self._base_url() + path
        timeout = kwargs.pop("timeout", self.settings.search_timeout_seconds)
        close_client = False
        client = self._client
        if client is None:
            client = httpx.AsyncClient(verify=self.settings.verify_ssl, timeout=timeout)
            close_client = True
        try:
            resp = await client.request(method, url, headers=headers, timeout=timeout, **kwargs)
            if resp.status_code == 401 or resp.status_code == 403:
                return {"ok": False, "recoverable": True, "error_code": "SLSKD_AUTH_FAILED", "error": "slskd rejected the configured API key."}
            if resp.status_code == 404:
                return {"ok": False, "recoverable": True, "error_code": "SLSKD_ENDPOINT_NOT_FOUND", "error": f"slskd endpoint not found: {path}"}
            resp.raise_for_status()
            if not resp.content:
                return {"ok": True}
            try:
                return resp.json()
            except ValueError:
                return {"ok": True, "text": resp.text}
        except httpx.RequestError as exc:
            return {"ok": False, "recoverable": True, "error_code": "SLSKD_UNREACHABLE", "error": str(exc)}
        except httpx.HTTPStatusError as exc:
            body = ""
            try:
                body = (exc.response.text or "")[:1000]
            except Exception:
                body = ""
            error = str(exc)
            if body:
                error = f"{error}; response={body}"
            return {"ok": False, "recoverable": True, "error_code": "SLSKD_HTTP_ERROR", "error": error}
        except Exception as exc:
            # Some tests and older client adapters raise non-httpx exceptions
            # from raise_for_status(); keep this recoverable for search fallbacks.
            return {"ok": False, "recoverable": True, "error_code": "SLSKD_REQUEST_FAILED", "error": str(exc)}
        finally:
            if close_client:
                await client.aclose()


    async def _request_no_auth(self, method: str, path: str, **kwargs: Any) -> Any:
        """Send a slskd request without X-API-Key/session auth, used for web login."""
        url = self._base_url() + path
        timeout = kwargs.pop("timeout", self.settings.search_timeout_seconds)
        close_client = False
        client = self._client
        if client is None:
            client = httpx.AsyncClient(verify=self.settings.verify_ssl, timeout=timeout)
            close_client = True
        try:
            resp = await client.request(method, url, timeout=timeout, **kwargs)
            if resp.status_code == 401 or resp.status_code == 403:
                return {"ok": False, "recoverable": True, "error_code": "SLSKD_WEB_AUTH_FAILED", "error": "slskd rejected the web login request."}
            if resp.status_code == 404:
                return {"ok": False, "recoverable": True, "error_code": "SLSKD_ENDPOINT_NOT_FOUND", "error": f"slskd endpoint not found: {path}"}
            resp.raise_for_status()
            if not resp.content:
                return {"ok": True}
            try:
                return resp.json()
            except ValueError:
                return {"ok": True, "text": resp.text}
        except httpx.RequestError as exc:
            return {"ok": False, "recoverable": True, "error_code": "SLSKD_UNREACHABLE", "error": str(exc)}
        except httpx.HTTPStatusError as exc:
            body = ""
            try:
                body = (exc.response.text or "")[:1000]
            except Exception:
                body = ""
            error = str(exc)
            if body:
                error = f"{error}; response={body}"
            return {"ok": False, "recoverable": True, "error_code": "SLSKD_HTTP_ERROR", "error": error}
        finally:
            if close_client:
                await client.aclose()

    async def _request_with_bearer(self, method: str, path: str, *, token: str, token_type: str = "Bearer", **kwargs: Any) -> Any:
        """Send a slskd request using a login token instead of the API key."""
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.setdefault("Authorization", f"{token_type or 'Bearer'} {token}")
        url = self._base_url() + path
        timeout = kwargs.pop("timeout", self.settings.search_timeout_seconds)
        close_client = False
        client = self._client
        if client is None:
            client = httpx.AsyncClient(verify=self.settings.verify_ssl, timeout=timeout)
            close_client = True
        try:
            resp = await client.request(method, url, headers=headers, timeout=timeout, **kwargs)
            if resp.status_code == 401 or resp.status_code == 403:
                return {"ok": False, "recoverable": True, "error_code": "SLSKD_TOKEN_AUTH_FAILED", "error": "slskd rejected the login token."}
            if resp.status_code == 404:
                return {"ok": False, "recoverable": True, "error_code": "SLSKD_ENDPOINT_NOT_FOUND", "error": f"slskd endpoint not found: {path}"}
            resp.raise_for_status()
            if not resp.content:
                return {"ok": True}
            try:
                return resp.json()
            except ValueError:
                return {"ok": True, "text": resp.text}
        except httpx.RequestError as exc:
            return {"ok": False, "recoverable": True, "error_code": "SLSKD_UNREACHABLE", "error": str(exc)}
        except httpx.HTTPStatusError as exc:
            body = ""
            try:
                body = (exc.response.text or "")[:1000]
            except Exception:
                body = ""
            error = str(exc)
            if body:
                error = f"{error}; response={body}"
            return {"ok": False, "recoverable": True, "error_code": "SLSKD_HTTP_ERROR", "error": error}
        finally:
            if close_client:
                await client.aclose()

    async def login_session(self) -> dict[str, Any]:
        """Log in to slskd web/session auth and return a token payload when available."""
        username = str(getattr(self.settings, "web_username", "") or "").strip()
        password = str(getattr(self.settings, "web_password", "") or "")
        if not username or not password:
            return {"ok": False, "recoverable": True, "error_code": "SLSKD_WEB_LOGIN_MISSING", "error": "slskd web username/password are not configured."}
        payloads = (
            {"username": username, "password": password},
            {"name": username, "password": password},
        )
        paths = ("/api/v0/session", "/api/v0/session/login")
        last: dict[str, Any] | None = None
        for path in paths:
            for payload in payloads:
                data = await self._request_no_auth("POST", path, json=payload)
                if isinstance(data, dict) and data.get("error_code") == "SLSKD_ENDPOINT_NOT_FOUND":
                    last = data
                    continue
                if isinstance(data, dict) and data.get("ok") is False:
                    last = data
                    continue
                if isinstance(data, dict):
                    token = str(data.get("token") or data.get("accessToken") or data.get("jwt") or "").strip()
                    token_type = str(data.get("tokenType") or data.get("token_type") or "Bearer").strip() or "Bearer"
                    if token:
                        return {"ok": True, "token": token, "token_type": token_type, "raw": data}
                last = {"ok": False, "recoverable": True, "error_code": "SLSKD_SESSION_TOKEN_MISSING", "error": f"slskd login endpoint {path} did not return a token.", "raw": data}
        return last or {"ok": False, "recoverable": True, "error_code": "SLSKD_SESSION_LOGIN_FAILED", "error": "slskd web login failed."}

    async def stop_application(self) -> dict[str, Any]:
        """Best-effort request for slskd to shut down.

        The application stop endpoint requires web/session token auth on some
        slskd versions, so this first tries the API-key shape for compatibility
        and then retries using a session token.
        """
        attempts = (
            ("POST", "/api/v0/application/stop"),
            ("PUT", "/api/v0/application/stop"),
            ("DELETE", "/api/v0/application"),
        )
        last: dict[str, Any] | None = None
        for method, path in attempts:
            data = await self._request(method, path)
            if isinstance(data, dict) and data.get("error_code") in {"SLSKD_ENDPOINT_NOT_FOUND", "SLSKD_AUTH_FAILED", "SLSKD_HTTP_ERROR"}:
                last = data
                continue
            if isinstance(data, dict) and data.get("ok") is False:
                last = data
                continue
            return {"ok": True, "source": "slskd", "method": method, "path": path, "receipt": data}

        session = await self.login_session()
        if not (isinstance(session, dict) and session.get("ok") and session.get("token")):
            return {"ok": False, "recoverable": True, "error_code": "SLSKD_STOP_AUTH_FAILED", "error": "Could not authenticate to slskd web session for application stop.", "last": last, "session": session}
        token = str(session.get("token") or "")
        token_type = str(session.get("token_type") or "Bearer")
        for method, path in attempts:
            data = await self._request_with_bearer(method, path, token=token, token_type=token_type)
            if isinstance(data, dict) and data.get("error_code") == "SLSKD_ENDPOINT_NOT_FOUND":
                last = data
                continue
            if isinstance(data, dict) and data.get("ok") is False:
                last = data
                continue
            return {"ok": True, "source": "slskd", "method": method, "path": path, "receipt": data}
        return last or {"ok": False, "recoverable": True, "error_code": "SLSKD_STOP_ENDPOINT_NOT_FOUND", "error": "slskd application stop endpoint was not found."}

    async def state(self) -> dict[str, Any]:
        """Return slskd application state if reachable."""
        for path in ("/api/v0/application", "/api/v0/application/state"):
            data = await self._request("GET", path)
            if isinstance(data, dict) and data.get("error_code") == "SLSKD_ENDPOINT_NOT_FOUND":
                continue
            return data if isinstance(data, dict) else {"ok": True, "data": data}
        return {"ok": False, "recoverable": True, "error_code": "SLSKD_ENDPOINT_NOT_FOUND", "error": "slskd application state endpoint was not found."}

    async def server_state(self) -> dict[str, Any]:
        """Return slskd Soulseek server state if the endpoint is available."""
        for path in ("/api/v0/server", "/api/v0/server/state"):
            data = await self._request("GET", path)
            if isinstance(data, dict) and data.get("error_code") == "SLSKD_ENDPOINT_NOT_FOUND":
                continue
            return data if isinstance(data, dict) else {"ok": True, "data": data}
        return {"ok": False, "recoverable": True, "error_code": "SLSKD_ENDPOINT_NOT_FOUND", "error": "slskd server state endpoint was not found."}

    async def connect_server(self) -> dict[str, Any]:
        """Best-effort request for slskd to connect to the Soulseek server.

        slskd versions have changed some API shapes.  The official Python API
        exposes a ServerApi.connect() operation, so LJS tries the common server
        endpoints and treats 404s as harmless version mismatch.
        """
        for method, path in (
            ("POST", "/api/v0/server"),
            ("PUT", "/api/v0/server"),
            ("POST", "/api/v0/server/connect"),
        ):
            data = await self._request(method, path)
            if isinstance(data, dict) and data.get("error_code") == "SLSKD_ENDPOINT_NOT_FOUND":
                continue
            return data if isinstance(data, dict) else {"ok": True, "data": data}
        return {"ok": False, "recoverable": True, "error_code": "SLSKD_ENDPOINT_NOT_FOUND", "error": "slskd server connect endpoint was not found."}

    async def connection_status(self, *, log_text: str = "") -> dict[str, Any]:
        """Return a best-effort Soulseek network login/connection status.

        slskd API shapes can vary across releases, so this method combines the
        application state endpoint with optional managed-runtime log snippets.
        It deliberately separates API reachability from Soulseek account login:
        a reachable web/API process is not enough for LJS to mark Soulseek as
        usable.
        """
        state = await self.state()
        if isinstance(state, dict) and state.get("ok") is False:
            return {
                "api_reachable": False,
                "authenticated_to_soulseek": False,
                "credentials_rejected": False,
                "connection_state": "api_unreachable",
                "error": state.get("error") or "slskd API is not reachable.",
                "raw_state": state,
            }
        server_state = await self.server_state()
        payload: Any = state
        if not (isinstance(server_state, dict) and server_state.get("ok") is False):
            payload = {"application": state, "server": server_state}
        interpreted = self.interpret_connection_payload(payload, log_text=log_text)
        if not interpreted.get("authenticated_to_soulseek") and not interpreted.get("credentials_rejected"):
            # Some slskd starts leave the server disconnected until explicitly
            # asked to connect.  Try once, then re-read state.  Unknown endpoint
            # versions simply return a recoverable 404 and are ignored.
            await self.connect_server()
            refreshed = await self.server_state()
            if not (isinstance(refreshed, dict) and refreshed.get("ok") is False):
                payload = {"application": state, "server": refreshed}
                interpreted = self.interpret_connection_payload(payload, log_text=log_text)
        interpreted["api_reachable"] = True
        interpreted["raw_state"] = payload
        return interpreted

    @staticmethod
    def interpret_connection_payload(payload: Any, *, log_text: str = "") -> dict[str, Any]:
        """Interpret slskd state/log payloads for Soulseek login validation."""
        strings = []
        try:
            strings.append(json.dumps(payload, default=str))
        except Exception:
            strings.append(str(payload))
        if log_text:
            strings.append(str(log_text))
        combined = "\n".join(strings).lower()

        rejected_phrases = (
            "username and/or password invalid",
            "invalid username",
            "invalid password",
            "invalid credentials",
            "not connecting to the soulseek server",
            "authentication failed",
            "login failed",
            "logon failed",
        )
        if any(phrase in combined for phrase in rejected_phrases):
            return {
                "authenticated_to_soulseek": False,
                "credentials_rejected": True,
                "connection_state": "auth_failed",
                "error": "Soulseek rejected these credentials. Use an existing account or try a different new username/password.",
            }

        not_logged_phrases = (
            "client is not logged in",
            "client isn't logged in",
            "connect to server to perform a search",
            "not logged in to soulseek",
            "not connected to the soulseek server",
        )
        if any(phrase in combined for phrase in not_logged_phrases):
            return {
                "authenticated_to_soulseek": False,
                "credentials_rejected": False,
                "connection_state": "not_logged_in",
                "error": "slskd is reachable but not connected/logged in to the Soulseek network yet.",
            }

        positive_phrases = (
            "connected to the soulseek server",
            "connected to soulseek",
            "logged in to soulseek",
            "login succeeded",
            "logon succeeded",
            "soulseek connection established",
        )
        if any(phrase in combined for phrase in positive_phrases):
            return {
                "authenticated_to_soulseek": True,
                "credentials_rejected": False,
                "connection_state": "connected",
                "error": "",
            }

        found_positive = False
        found_negative = False
        state_label = "unknown"
        for key, value in SlskdClient._walk_payload(payload):
            normalized_key = key.replace("_", "").replace("-", "").replace(".", "").lower()
            if isinstance(value, bool):
                if value and SlskdClient._looks_like_soulseek_connection_key(normalized_key):
                    found_positive = True
                    state_label = key or "connected"
                if (not value) and SlskdClient._looks_like_soulseek_connection_key(normalized_key):
                    found_negative = True
                continue
            value_text = str(value or "").strip().lower()
            if not value_text:
                continue
            if SlskdClient._looks_like_soulseek_connection_key(normalized_key) or any(token in normalized_key for token in ("state", "status", "connection")):
                if value_text in {"connected", "online", "authenticated", "loggedin", "logged_in", "ready"} or "connected" in value_text:
                    if "disconnect" not in value_text and "not connected" not in value_text:
                        found_positive = True
                        state_label = value_text
                if value_text in {"disconnected", "offline", "unauthenticated", "notconnected", "not_connected"} or "disconnected" in value_text:
                    found_negative = True
                    state_label = value_text

        if found_positive:
            return {
                "authenticated_to_soulseek": True,
                "credentials_rejected": False,
                "connection_state": state_label or "connected",
                "error": "",
            }
        if found_negative:
            return {
                "authenticated_to_soulseek": False,
                "credentials_rejected": False,
                "connection_state": state_label or "disconnected",
                "error": "slskd is reachable but not connected to the Soulseek network yet.",
            }
        return {
            "authenticated_to_soulseek": False,
            "credentials_rejected": False,
            "connection_state": "unknown",
            "error": "slskd is reachable, but LJS could not confirm Soulseek network login yet.",
        }

    @staticmethod
    def _looks_like_soulseek_connection_key(key: str) -> bool:
        if key in {
            "connected",
            "isconnected",
            "online",
            "authenticated",
            "loggedin",
            "isloggedin",
            "loggedintoserver",
            "serverisconnected",
            "serverisloggedin",
            "applicationserverisconnected",
            "applicationserverisloggedin",
        }:
            return True
        if key.endswith("isconnected") or key.endswith("isloggedin") or key.endswith("loggedin"):
            return True
        return any(token in key for token in (
            "soulseekconnected",
            "serverconnected",
            "serverisconnected",
            "serverisloggedin",
            "networkconnected",
            "connectionstate",
            "serverconnection",
        ))

    @staticmethod
    def _walk_payload(payload: Any, prefix: str = "") -> list[tuple[str, Any]]:
        rows: list[tuple[str, Any]] = []
        if isinstance(payload, dict):
            for key, value in payload.items():
                key_text = f"{prefix}.{key}" if prefix else str(key)
                rows.append((key_text, value))
                rows.extend(SlskdClient._walk_payload(value, key_text))
        elif isinstance(payload, list):
            for idx, value in enumerate(payload):
                key_text = f"{prefix}.{idx}" if prefix else str(idx)
                rows.extend(SlskdClient._walk_payload(value, key_text))
        return rows

    @staticmethod
    def _payload_shape_summary(payload: Any, *, limit: int = 24) -> dict[str, Any]:
        """Return a small non-secret JSON-shape summary for diagnostics."""
        keys: list[str] = []
        file_like = 0
        response_like = 0

        def walk(value: Any, prefix: str = "") -> None:
            nonlocal file_like, response_like
            if len(keys) >= limit:
                return
            if isinstance(value, dict):
                if SlskdClient._looks_like_file_row(value):
                    file_like += 1
                if any(k in value for k in ("files", "lockedFiles", "locked_files")):
                    response_like += 1
                for key, child in value.items():
                    key_text = f"{prefix}.{key}" if prefix else str(key)
                    if len(keys) < limit:
                        if isinstance(child, dict):
                            keys.append(f"{key_text}:dict")
                        elif isinstance(child, list):
                            keys.append(f"{key_text}:list[{len(child)}]")
                        else:
                            keys.append(f"{key_text}:{type(child).__name__}")
                    if isinstance(child, (dict, list)):
                        walk(child, key_text)
            elif isinstance(value, list):
                for idx, child in enumerate(value[:8]):
                    walk(child, f"{prefix}.{idx}" if prefix else str(idx))

        walk(payload)
        return {"keys": keys[:limit], "file_like": file_like, "response_like": response_like}

    async def search(self, query: str, *, timeout_seconds: float | None = None, max_results: int | None = None) -> dict[str, Any]:
        """Search slskd and return normalized file/folder candidates.

        slskd search state and response payloads are not the same thing: the
        documented ``search_text`` call returns state only, while ``state(...,
        includeResponses=True)`` or ``search_responses(id)`` expose results.
        Real deployments have also shown completed states with non-zero
        ``fileCount`` but delayed/empty ``responses`` arrays, so this method
        polls the state and response endpoints independently and records a
        diagnostic dump when slskd reports files that LJS still cannot parse.
        """
        text = str(query or "").strip()
        if not text:
            return {"ok": False, "recoverable": True, "error_code": "EMPTY_QUERY", "error": "Soulseek search query is empty."}
        if not self.configured:
            return {"ok": False, "recoverable": True, "error_code": "SLSKD_NOT_CONFIGURED", "error": "Soulseek/slskd is disabled or missing an API key."}

        timeout = max(2.0, float(timeout_seconds or self.settings.search_timeout_seconds))
        limit = int(max_results or self.settings.max_search_results)
        requested_id = str(uuid.uuid4())
        search_timeout_ms = int(max(timeout, 5.0) * 1000)
        create_payloads = (
            {
                "id": requested_id,
                "searchText": text,
                "fileLimit": max(500, min(limit * 200, 10000)),
                "filterResponses": False,
                "maximumPeerQueueLength": 1000000,
                "minimumPeerUploadSpeed": 0,
                "minimumResponseFileCount": 1,
                "responseLimit": max(100, min(limit * 10, 500)),
                "searchTimeout": search_timeout_ms,
            },
            {"searchText": text, "filterResponses": False, "searchTimeout": search_timeout_ms},
            {"text": text},
        )
        created: Any = None
        last_error: Any = None
        for payload in create_payloads:
            created = await self._request("POST", "/api/v0/searches", json=payload, timeout=timeout)
            if isinstance(created, dict) and created.get("ok") is False:
                last_error = created
                if created.get("error_code") == "SLSKD_ENDPOINT_NOT_FOUND":
                    continue
            else:
                break
        if isinstance(created, dict) and created.get("ok") is False:
            return created

        rest_id = self._search_rest_id(created) or requested_id
        protocol_token = self._search_protocol_token(created)
        logger.info(f"slskd search started: query={text!r} id={rest_id!r} token={protocol_token!r}")

        candidates: list[SoulseekCandidate] = []
        stats = SearchNormalizationStats()
        last_state: Any = {}
        last_response_payload: Any = {}
        endpoint_shapes: list[dict[str, Any]] = []
        raw_response_count = 0
        raw_file_count = 0
        deadline = asyncio.get_event_loop().time() + timeout
        grace_deadline = deadline + min(8.0, max(2.0, timeout * 0.5))

        while asyncio.get_event_loop().time() < grace_deadline and len(candidates) < limit:
            state = await self._search_state(rest_id, include_responses=True)
            last_state = state
            raw_response_count = max(raw_response_count, _payload_count(state, "responseCount"))
            raw_file_count = max(raw_file_count, _payload_count(state, "fileCount"))
            state_candidates, state_stats = self.normalize_search_payload_detailed(state)
            endpoint_shapes.append({"endpoint": "state_include_responses", "shape": self._payload_shape_summary(state)})
            if state_candidates:
                candidates, stats = state_candidates, state_stats
                break

            # Query the response endpoint separately.  Try both the documented
            # REST UUID and the protocol token because old/new slskd builds and
            # wrappers have disagreed in the wild.
            response_payloads = await self._search_response_payloads(rest_id, protocol_token)
            for response_payload in response_payloads:
                last_response_payload = response_payload
                raw_response_count = max(raw_response_count, _payload_count(response_payload, "responseCount"), _top_level_len(response_payload))
                raw_file_count = max(raw_file_count, _payload_count(response_payload, "fileCount"), _deep_file_count(response_payload))
                endpoint_shapes.append({"endpoint": "responses", "shape": self._payload_shape_summary(response_payload)})
                response_candidates, response_stats = self.normalize_search_payload_detailed(response_payload)
                if response_candidates:
                    candidates, stats = response_candidates, response_stats
                    break
            if candidates:
                break

            # A few slskd versions expose complete results only from the search
            # list.  Pull it only when state says files exist but the primary
            # endpoints are still empty.
            if raw_file_count > 0 or raw_response_count > 0:
                all_payload = await self._matching_search_from_all(rest_id, protocol_token, text)
                if all_payload:
                    endpoint_shapes.append({"endpoint": "searches_list_match", "shape": self._payload_shape_summary(all_payload)})
                    list_candidates, list_stats = self.normalize_search_payload_detailed(all_payload)
                    if list_candidates:
                        candidates, stats = list_candidates, list_stats
                        break

            complete = self._search_complete(state)
            # Do not stop just because slskd marks the search complete if it
            # also reports files but has not materialized response details yet.
            if complete and not (raw_file_count > 0 and asyncio.get_event_loop().time() < grace_deadline):
                break
            if asyncio.get_event_loop().time() >= deadline and raw_file_count <= 0 and raw_response_count <= 0:
                break
            await asyncio.sleep(0.75)

        shape_summary = {
            "state": self._payload_shape_summary(last_state),
            "responses": self._payload_shape_summary(last_response_payload),
            "attempts": endpoint_shapes[-8:],
        } if not candidates else {}
        diagnostic_dump = ""
        if not candidates and (raw_file_count > 0 or raw_response_count > 0):
            diagnostic_dump = self._write_search_debug_dump(
                query=text,
                rest_id=rest_id,
                protocol_token=protocol_token,
                state=last_state,
                responses=last_response_payload,
                endpoint_shapes=endpoint_shapes,
            )
        logger.info(
            f"slskd search finished: query={text!r} id={rest_id!r} token={protocol_token!r} candidates={len(candidates)} "
            f"total_file_rows={stats.total_file_rows} filtered_locked_private={stats.filtered_locked + stats.filtered_private} "
            f"raw_response_count={raw_response_count} raw_file_count={raw_file_count} "
            f"shape={shape_summary if shape_summary else ''} diagnostic_dump={diagnostic_dump!r}"
        )
        if not candidates and last_error:
            return last_error if isinstance(last_error, dict) else {"ok": False, "recoverable": True, "error": str(last_error)}
        notes = [
            "Locked/private Soulseek files were filtered out.",
            "Results may still queue behind the remote user's upload queue when no free slot is available.",
        ]
        if not candidates and raw_file_count > 0:
            notes.append("slskd reported raw Soulseek files, but LJS could not parse the response payload. Diagnostic dump was written for debugging.")
        return {
            "ok": True,
            "query": text,
            "source": "slskd",
            "search_id": rest_id,
            "search_token": protocol_token,
            "candidates": self._public_candidates(candidates, limit, query=text),
            "raw_response_count": raw_response_count,
            "raw_file_count": raw_file_count,
            "payload_shape": shape_summary,
            "diagnostic_dump": diagnostic_dump,
            "filtering": {
                **stats.as_public_dict(),
                "locked_or_private_filtered": int(stats.filtered_locked + stats.filtered_private),
            },
            "notes": notes,
        }

    async def _search_state(self, token: str | None, *, include_responses: bool = False) -> Any:
        if not token:
            return {}
        quoted = quote(str(token), safe="")
        attempts: list[tuple[str, dict[str, Any] | None]] = []
        if include_responses:
            # Keep the query-string variant because older tests/servers observed
            # the raw URL, and also try official params below.
            attempts.extend([
                (f"/api/v0/searches/{quoted}?includeResponses=true", None),
                (f"/api/v0/searches/{quoted}/state?includeResponses=true", None),
            ])
        params = {"includeResponses": include_responses} if include_responses else None
        attempts.extend([
            (f"/api/v0/searches/{quoted}", params),
            (f"/api/v0/searches/{quoted}/state", params),
        ])
        for path, params_payload in attempts:
            data = await self._request("GET", path, params=params_payload)
            if isinstance(data, dict) and data.get("error_code") == "SLSKD_ENDPOINT_NOT_FOUND":
                continue
            return data
        return {}

    async def _search_response_payloads(self, rest_id: str | None, protocol_token: str | None = None) -> list[Any]:
        payloads: list[Any] = []
        seen: set[str] = set()
        for token in (rest_id, protocol_token):
            if not token:
                continue
            quoted = quote(str(token), safe="")
            for path, params in (
                (f"/api/v0/searches/{quoted}/responses", None),
                (f"/api/v0/searches/{quoted}/responses", {"includeFiles": True}),
                (f"/api/v0/searches/{quoted}", {"includeResponses": True}),
                (f"/api/v0/searches/{quoted}/state", {"includeResponses": True}),
            ):
                data = await self._request("GET", path, params=params)
                if isinstance(data, dict) and data.get("error_code") == "SLSKD_ENDPOINT_NOT_FOUND":
                    continue
                sig = _payload_signature(data)
                if sig in seen:
                    continue
                seen.add(sig)
                payloads.append(data)
        return payloads

    async def _search_responses(self, token: str | None) -> Any:
        payloads = await self._search_response_payloads(token, None)
        return payloads[0] if payloads else {}

    async def _matching_search_from_all(self, rest_id: str | None, protocol_token: str | None, query: str) -> Any:
        data = await self._request("GET", "/api/v0/searches")
        if isinstance(data, dict) and data.get("ok") is False:
            return {}
        items = data if isinstance(data, list) else (list(data.values()) if isinstance(data, dict) else [])
        query_cf = str(query or "").casefold()
        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or item.get("searchId") or "")
            item_token = str(item.get("token") or "")
            item_query = str(item.get("searchText") or item.get("text") or "").casefold()
            if (rest_id and item_id == str(rest_id)) or (protocol_token and item_token == str(protocol_token)) or (query_cf and item_query == query_cf):
                return item
        return {}

    @staticmethod
    def _search_rest_id(payload: Any) -> str | None:
        if isinstance(payload, dict):
            for key in ("id", "searchId", "searchID", "uuid"):
                if payload.get(key) not in (None, ""):
                    return str(payload[key])
            nested = payload.get("data")
            if isinstance(nested, dict):
                return SlskdClient._search_rest_id(nested)
        return None

    @staticmethod
    def _search_protocol_token(payload: Any) -> str | None:
        if isinstance(payload, dict):
            for key in ("searchToken", "token"):
                if payload.get(key) not in (None, ""):
                    return str(payload[key])
            nested = payload.get("data")
            if isinstance(nested, dict):
                return SlskdClient._search_protocol_token(nested)
        return None

    @staticmethod
    def _search_token(payload: Any) -> str | None:
        """Compatibility alias: return REST search id first, then protocol token."""
        return SlskdClient._search_rest_id(payload) or SlskdClient._search_protocol_token(payload)

    @staticmethod
    def _search_complete(payload: Any) -> bool:
        if isinstance(payload, dict):
            for key in ("isComplete", "complete"):
                if payload.get(key) is True:
                    return True
            state = str(payload.get("state") or "").lower()
            return state in {"completed", "complete", "stopped"}
        return False

    def _write_search_debug_dump(
        self,
        *,
        query: str,
        rest_id: str | None,
        protocol_token: str | None,
        state: Any,
        responses: Any,
        endpoint_shapes: list[dict[str, Any]],
    ) -> str:
        """Write a sanitized local diagnostic dump for impossible slskd shapes."""
        try:
            root = Path(str(getattr(self.settings, "app_dir", "./data/slskd") or "./data/slskd")).expanduser()
            debug_dir = root / "debug" / "search_payloads"
            debug_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            safe_id = re_safe_filename(str(rest_id or protocol_token or "search"))
            target = debug_dir / f"{stamp}_{safe_id}.json"
            payload = {
                "query": query,
                "rest_id": rest_id,
                "protocol_token": protocol_token,
                "state_shape": self._payload_shape_summary(state, limit=80),
                "responses_shape": self._payload_shape_summary(responses, limit=80),
                "endpoint_shapes": endpoint_shapes[-20:],
                "state": _redact_search_payload(state),
                "responses": _redact_search_payload(responses),
            }
            target.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
            return str(target)
        except Exception as exc:
            logger.debug(f"Could not write slskd search diagnostic dump: {exc}")
            return ""


    async def download_transfers(self) -> dict[str, Any]:
        """Return slskd download transfer state using tolerant endpoint fallbacks."""
        for path in (
            "/api/v0/transfers/downloads",
            "/api/v0/transfers/downloads?includeRemoved=false",
            "/api/v0/transfers",
        ):
            data = await self._request("GET", path)
            if isinstance(data, dict) and data.get("error_code") == "SLSKD_ENDPOINT_NOT_FOUND":
                continue
            if isinstance(data, dict) and data.get("ok") is False:
                return data
            return {"ok": True, "source": "slskd", "transfers": data}
        return {"ok": False, "recoverable": True, "error_code": "SLSKD_ENDPOINT_NOT_FOUND", "error": "slskd transfer endpoint was not found."}

    async def cancel_download(self, *, username: str, transfer_id: str, remove: bool = True) -> dict[str, Any]:
        """Cancel/remove a single slskd download transfer.

        slskd exposes transfer cancellation as ``DELETE /transfers/downloads/{username}/{id}``;
        the optional ``remove`` flag also removes the row/partial payload when
        supported by the running slskd version.
        """
        user = str(username or "").strip()
        transfer = str(transfer_id or "").strip()
        if not user or not transfer:
            return {"ok": False, "recoverable": True, "error_code": "MISSING_SLSKD_TRANSFER", "error": "Soulseek username and transfer id are required."}
        quoted_user = quote(user, safe="")
        quoted_id = quote(transfer, safe="")
        attempts = (
            (f"/api/v0/transfers/downloads/{quoted_user}/{quoted_id}", {"remove": bool(remove)}),
            (f"/api/v0/transfers/downloads/{quoted_user}/{quoted_id}", None),
        )
        last: dict[str, Any] | None = None
        for path, params in attempts:
            data = await self._request("DELETE", path, params=params) if params is not None else await self._request("DELETE", path)
            if isinstance(data, dict) and data.get("error_code") == "SLSKD_ENDPOINT_NOT_FOUND":
                last = data
                continue
            if isinstance(data, dict) and data.get("ok") is False:
                return data
            return {"ok": True, "source": "slskd", "username": user, "transfer_id": transfer, "removed": bool(remove), "receipt": data}
        return last or {"ok": False, "recoverable": True, "error_code": "SLSKD_ENDPOINT_NOT_FOUND", "error": "slskd download cancel endpoint was not found."}

    async def remove_completed_downloads(self) -> dict[str, Any]:
        """Ask slskd to clear completed download rows from its transfer list."""
        for path in (
            "/api/v0/transfers/downloads/all/completed",
            "/api/v0/transfers/downloads/completed",
        ):
            data = await self._request("DELETE", path)
            if isinstance(data, dict) and data.get("error_code") == "SLSKD_ENDPOINT_NOT_FOUND":
                continue
            if isinstance(data, dict) and data.get("ok") is False:
                return data
            return {"ok": True, "source": "slskd", "receipt": data}
        return {"ok": False, "recoverable": True, "error_code": "SLSKD_ENDPOINT_NOT_FOUND", "error": "slskd remove-completed-downloads endpoint was not found."}

    async def enqueue_download(
        self,
        *,
        username: str,
        filename: str = "",
        filenames: list[str] | None = None,
        file_requests: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Queue one or more Soulseek files in slskd.

        slskd's ``POST /transfers/downloads/{username}`` endpoint expects the
        request body to be an array of QueueDownloadRequest objects, not the
        older object wrapper shape.  Each object needs at least ``filename`` and
        may include ``size`` when search results provided it.
        """
        user = str(username or "").strip()
        requests = self._queue_download_requests(filename=filename, filenames=filenames, file_requests=file_requests)
        if not user or not requests:
            return {"ok": False, "recoverable": True, "error_code": "MISSING_SLSKD_TARGET", "error": "Soulseek username and filename(s) are required."}
        quoted = quote(user, safe="")
        data = await self._request("POST", f"/api/v0/transfers/downloads/{quoted}", json=requests)
        if isinstance(data, dict) and data.get("ok") is False:
            data.setdefault("slskd_payload_shape", "array_of_queue_download_request")
            data.setdefault("queued_file_count", len(requests))
            return data
        return {
            "ok": True,
            "source": "slskd",
            "username": user,
            "filename": str(requests[0].get("filename") or ""),
            "filenames": [str(item.get("filename") or "") for item in requests],
            "file_count": len(requests),
            "receipt": data,
        }

    @staticmethod
    def _queue_download_requests(
        *,
        filename: str = "",
        filenames: list[str] | None = None,
        file_requests: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Normalize candidate cache/tool input into slskd QueueDownloadRequest rows."""
        requests: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add(name: Any, size: Any = None) -> None:
            text = str(name or "").strip()
            if not text or text.casefold() in seen:
                return
            seen.add(text.casefold())
            row: dict[str, Any] = {"filename": text}
            try:
                if size not in (None, ""):
                    row["size"] = int(float(size))
            except (TypeError, ValueError):
                pass
            requests.append(row)

        for item in file_requests or []:
            if not isinstance(item, dict):
                continue
            add(item.get("filename") or item.get("name") or item.get("fullName"), item.get("size") or item.get("size_bytes") or item.get("length"))

        for item in filenames or []:
            add(item)

        if not requests and str(filename or "").strip():
            add(filename)

        return requests

    @staticmethod
    def _search_token(payload: Any) -> str | None:
        """Return the slskd search identifier used by state/response endpoints.

        slskd search responses include both ``id`` and ``token``.  The public
        slskd Python API documents ``id`` as the UUID used for ``state()`` and
        ``search_responses()``; ``token`` is the Soulseek protocol token and is
        not the stable REST path identifier.  Prefer id/searchId before token so
        LJS does not poll the wrong endpoint and falsely report zero results.
        """
        if isinstance(payload, dict):
            for key in ("id", "searchId", "searchID", "uuid", "searchToken", "token"):
                if payload.get(key) not in (None, ""):
                    return str(payload[key])
            nested = payload.get("data")
            if isinstance(nested, dict):
                return SlskdClient._search_token(nested)
        return None

    @staticmethod
    def _search_complete(payload: Any) -> bool:
        if isinstance(payload, dict):
            for key in ("isComplete", "complete"):
                if payload.get(key) is True:
                    return True
            state = str(payload.get("state") or "").lower()
            return state in {"completed", "complete", "stopped"}
        return False

    @staticmethod
    def normalize_search_payload(payload: Any) -> list[SoulseekCandidate]:
        """Normalize common slskd search-response shapes into public candidates.

        Locked/private/inaccessible files are intentionally excluded.
        """
        candidates, _ = SlskdClient.normalize_search_payload_detailed(payload)
        return candidates

    @staticmethod
    def normalize_search_payload_detailed(payload: Any) -> tuple[list[SoulseekCandidate], SearchNormalizationStats]:
        """Normalize slskd search payloads and return filtering diagnostics.

        slskd search JSON has varied across releases and endpoints.  In
        addition to the documented SearchState.responses -> SearchResponseItem
        shape, real REST responses may be maps keyed by username, maps keyed by
        folder, or directory buckets whose file rows do not repeat the folder
        name.  Preserve inherited username/folder context while walking the
        payload so a valid album folder does not disappear just because its
        response object is one level deeper than expected.
        """
        responses = SlskdClient._collect_search_response_items(payload)

        candidates: list[SoulseekCandidate] = []
        seen: set[tuple[str, str]] = set()
        total_files = 0
        filtered_locked = 0
        filtered_private = 0
        filtered_duplicates = 0

        for response in responses:
            username = SlskdClient._extract_username(response)
            files = SlskdClient._coerce_file_rows(response.get("files"))
            locked_files = SlskdClient._coerce_file_rows(response.get("lockedFiles") or response.get("locked_files"))
            response_locked = SlskdClient._truthy_flag(response, "isLocked", "locked")
            response_private = SlskdClient._truthy_flag(response, "isPrivate", "private", "requiresFriend", "requiresPrivileges", "requiresPermission")
            response_folder = SlskdClient._response_folder(response)

            for file_info, from_locked_bucket in [(row, False) for row in files] + [(row, True) for row in locked_files]:
                total_files += 1
                filename = SlskdClient._file_name(file_info)
                if not filename:
                    continue
                inherited_folder = ""
                if isinstance(file_info, dict):
                    inherited_folder = str(file_info.get("_ljs_folder") or "").strip()
                    if not username:
                        username = SlskdClient._extract_username(file_info)
                folder = inherited_folder or response_folder
                full_filename = SlskdClient._join_remote_path(folder, filename)
                if SlskdClient._truthy_flag(file_info if isinstance(file_info, dict) else {}, "isPrivate", "private", "requiresFriend", "requiresPrivileges", "requiresPermission") or response_private:
                    filtered_private += 1
                    continue
                if from_locked_bucket or SlskdClient._truthy_flag(file_info if isinstance(file_info, dict) else {}, "isLocked", "locked") or response_locked:
                    filtered_locked += 1
                    continue
                key = (username.casefold(), full_filename.casefold())
                if key in seen:
                    filtered_duplicates += 1
                    continue
                seen.add(key)
                attrs = SlskdClient._file_attributes(file_info)
                candidates.append(SoulseekCandidate(
                    username=username,
                    filename=full_filename,
                    size_bytes=_int_or_none(_first_present(file_info, "size", "length") if isinstance(file_info, dict) else None),
                    extension=str((_first_present(file_info, "extension", "ext") if isinstance(file_info, dict) else None) or Path(full_filename).suffix.lstrip(".")).strip().lower(),
                    bitrate=_int_or_none(_first_present(file_info, "bitRate", "bitrate") if isinstance(file_info, dict) else None) or _int_or_none(attrs.get("bitRate") or attrs.get("bitrate")),
                    bit_depth=_int_or_none(_first_present(file_info, "bitDepth", "bit_depth") if isinstance(file_info, dict) else None) or _int_or_none(attrs.get("bitDepth") or attrs.get("bit_depth")),
                    sample_rate=_int_or_none(_first_present(file_info, "sampleRate", "sample_rate") if isinstance(file_info, dict) else None) or _int_or_none(attrs.get("sampleRate") or attrs.get("sample_rate")),
                    length_seconds=_int_or_none(_first_present(file_info, "duration", "lengthSeconds") if isinstance(file_info, dict) else None) or _int_or_none(attrs.get("length") or attrs.get("duration")),
                    is_locked=False,
                    has_free_upload_slot=_bool_or_none(_first_present(response, "hasFreeUploadSlot", "hasSlotsFree", "slotsFree")),
                    queue_length=_int_or_none(_first_present(response, "queueLength", "queue_position", "queuePosition")),
                    upload_speed=_int_or_none(_first_present(response, "uploadSpeed", "speed", "averageSpeed")),
                    raw={"response": response, "file": file_info},
                ))

        candidates = sorted(candidates, key=SlskdClient._candidate_sort_key)
        stats = SearchNormalizationStats(
            total_file_rows=total_files,
            candidate_files=len(candidates),
            filtered_locked=filtered_locked,
            filtered_private=filtered_private,
            filtered_duplicates=filtered_duplicates,
        )
        return candidates, stats

    @staticmethod
    def _collect_search_response_items(payload: Any) -> list[dict[str, Any]]:
        """Collect nested slskd SearchResponseItem-like dictionaries.

        The walker carries username/folder hints from parent map keys and
        wrapper objects.  This covers common slskd REST shapes such as:
        ``responses: {username: {files: [...]}}`` and
        ``files: {folder: [...]}``.
        """
        collected: list[dict[str, Any]] = []

        def inherit_username(obj: dict[str, Any], fallback: str = "") -> str:
            return SlskdClient._extract_username(obj) or (fallback if _looks_like_username(fallback) else "")

        def walk(value: Any, *, username_hint: str = "", folder_hint: str = "", map_key: str = "") -> None:
            if isinstance(value, list):
                # A username -> [files] map is a valid compact response shape.
                if username_hint and value and all(SlskdClient._looks_like_file_row(row) for row in value):
                    collected.append({"username": username_hint, "folder": folder_hint, "files": SlskdClient._tag_file_rows(value, folder_hint)})
                    return
                for item in value:
                    walk(item, username_hint=username_hint, folder_hint=folder_hint)
                return
            if not isinstance(value, dict):
                return

            current_username = inherit_username(value, username_hint)
            current_folder = SlskdClient._response_folder(value) or folder_hint
            has_files = any(isinstance(value.get(key), (list, dict, str)) for key in ("files", "lockedFiles", "locked_files"))
            if has_files:
                response = dict(value)
                if current_username and not SlskdClient._extract_username(response):
                    response["username"] = current_username
                if current_folder and not SlskdClient._response_folder(response):
                    response["folder"] = current_folder
                collected.append(response)
                return

            for key, child in value.items():
                key_text = str(key or "")
                child_username = current_username
                child_folder = current_folder
                if _looks_like_username(key_text):
                    child_username = key_text
                elif _looks_like_remote_folder(key_text):
                    child_folder = key_text
                walk(child, username_hint=child_username, folder_hint=child_folder, map_key=key_text)

        walk(payload)
        return collected

    @staticmethod
    def _extract_username(payload: dict[str, Any] | Any) -> str:
        if not isinstance(payload, dict):
            return ""
        for key in ("username", "userName", "user"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                nested = SlskdClient._extract_username(value)
                if nested:
                    return nested
                for nested_key in ("name", "displayName", "id"):
                    nested_value = value.get(nested_key)
                    if isinstance(nested_value, str) and nested_value.strip():
                        return nested_value.strip()
        return ""

    @staticmethod
    def _looks_like_file_row(row: Any) -> bool:
        if isinstance(row, str):
            return bool(Path(row.replace("\\", "/")).suffix)
        if not isinstance(row, dict):
            return False
        return any(row.get(key) not in (None, "") for key in ("filename", "fullName", "full_name", "name", "fileName", "path"))

    @staticmethod
    def _tag_file_rows(rows: list[Any], folder: str = "") -> list[Any]:
        if not folder:
            return list(rows)
        tagged: list[Any] = []
        for row in rows:
            if isinstance(row, dict):
                cloned = dict(row)
                cloned.setdefault("_ljs_folder", folder)
                tagged.append(cloned)
            else:
                tagged.append({"filename": str(row), "_ljs_folder": folder})
        return tagged

    @staticmethod
    def _coerce_file_rows(value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            rows: list[Any] = []
            for key, child in value.items():
                folder = str(key or "") if _looks_like_remote_folder(str(key or "")) else ""
                if isinstance(child, list):
                    rows.extend(SlskdClient._tag_file_rows(child, folder))
                elif isinstance(child, dict):
                    if SlskdClient._looks_like_file_row(child):
                        cloned = dict(child)
                        if folder:
                            cloned.setdefault("_ljs_folder", folder)
                        rows.append(cloned)
                    else:
                        nested = SlskdClient._coerce_file_rows(child)
                        rows.extend(SlskdClient._tag_file_rows(nested, folder))
                elif isinstance(child, str):
                    rows.extend(SlskdClient._tag_file_rows([child], folder))
            return rows
        return []

    @staticmethod
    def _file_name(file_info: Any) -> str:
        if isinstance(file_info, str):
            return file_info.strip()
        if not isinstance(file_info, dict):
            return ""
        return str(_first_present(file_info, "filename", "fullName", "full_name", "name", "fileName", "path") or "").strip()

    @staticmethod
    def _response_folder(response: dict[str, Any]) -> str:
        folder = str(_first_present(response, "directory", "folder", "folderName", "folder_name", "path", "fullName", "full_name") or "").strip()
        return folder if _looks_like_remote_folder(folder) else ""

    @staticmethod
    def _join_remote_path(folder: str, filename: str) -> str:
        folder = str(folder or "").strip().strip("/\\")
        filename = str(filename or "").strip().strip("/\\")
        if not folder:
            return filename
        if not filename:
            return folder
        if filename.casefold().startswith(folder.casefold() + "/") or filename.casefold().startswith(folder.casefold() + "\\"):
            return filename
        return f"{folder}/{filename}"

    @staticmethod
    def _file_attributes(file_info: Any) -> dict[str, Any]:
        if not isinstance(file_info, dict):
            return {}
        attrs = file_info.get("attributes")
        if not isinstance(attrs, list):
            return {}
        parsed: dict[str, Any] = {}
        numeric_type_map = {0: "bitRate", 1: "length", 2: "vbr", 4: "sampleRate", 5: "bitDepth"}
        for attr in attrs:
            if not isinstance(attr, dict):
                continue
            raw_type = attr.get("type")
            value = attr.get("value")
            key = None
            if isinstance(raw_type, str):
                lowered = raw_type.replace("_", "").replace("-", "").casefold()
                key = {
                    "bitrate": "bitRate",
                    "bitratekbps": "bitRate",
                    "length": "length",
                    "duration": "length",
                    "samplerate": "sampleRate",
                    "bitdepth": "bitDepth",
                }.get(lowered)
            else:
                try:
                    key = numeric_type_map.get(int(raw_type))
                except Exception:
                    key = None
            if key and value not in (None, ""):
                parsed[key] = value
        return parsed

    @staticmethod
    def _public_candidates(candidates: list[SoulseekCandidate], limit: int, query: str = "") -> list[dict[str, Any]]:
        """Return folder groups first, then individual file candidates.

        Album searches often return one row per track from the same remote
        folder.  Presenting a folder candidate prevents the assistant from
        downloading only track 1 when the user asked for the album.
        """
        groups: dict[tuple[str, str], list[SoulseekCandidate]] = {}
        for cand in candidates:
            folder = _remote_parent(cand.filename)
            if folder:
                groups.setdefault((cand.username, folder), []).append(cand)
        query_tokens = _name_tokens(query)
        public: list[dict[str, Any]] = []
        folder_rows: list[tuple[float, str, str, list[SoulseekCandidate]]] = []
        for (username, folder), rows in groups.items():
            audio_rows = [row for row in rows if SlskdClient._is_audio_candidate(row)]
            if len(audio_rows) < 2:
                continue
            folder_rows.append((_folder_query_score(folder, query_tokens), username, folder, rows))
        for score, username, folder, rows in sorted(folder_rows, key=lambda item: (-item[0], -len([r for r in item[3] if SlskdClient._is_audio_candidate(r)]), item[1].casefold(), item[2].casefold())):
            audio_rows = [row for row in rows if SlskdClient._is_audio_candidate(row)]
            support_rows = [row for row in rows if row not in audio_rows and SlskdClient._is_safe_supporting_candidate(row)]
            folder_payload_rows = sorted(audio_rows, key=_remote_track_sort_key) + sorted(support_rows, key=lambda row: row.filename.casefold())
            first = sorted(audio_rows, key=SlskdClient._candidate_sort_key)[0]
            size_total = sum(int(row.size_bytes or 0) for row in folder_payload_rows) or None
            relevance = "strong" if score >= 0.72 else ("partial" if score >= 0.45 else "weak")
            folder_filenames = [row.filename for row in folder_payload_rows]
            folder_file_requests = [
                {"filename": row.filename, **({"size": int(row.size_bytes)} if row.size_bytes is not None else {})}
                for row in folder_payload_rows
            ]
            public.append({
                "index": len(public) + 1,
                "candidate_id": _soulseek_candidate_id(username=username, filename=folder, filenames=folder_filenames),
                "source": "slskd",
                "candidate_type": "folder",
                "username": username,
                "folder": folder,
                "filename": folder,
                "filenames": folder_filenames,
                "file_requests": folder_file_requests,
                "audio_filenames": [row.filename for row in sorted(audio_rows, key=_remote_track_sort_key)],
                "supporting_filenames": [row.filename for row in sorted(support_rows, key=lambda row: row.filename.casefold())],
                "file_count": len(folder_payload_rows),
                "audio_file_count": len(audio_rows),
                "supporting_file_count": len(support_rows),
                "size_bytes": size_total,
                "extension": "folder",
                "folder_query_match_score": round(score, 3),
                "folder_relevance": relevance,
                "has_free_upload_slot": first.has_free_upload_slot,
                "queue_length": first.queue_length,
                "upload_speed": first.upload_speed,
                "note": "Folder candidate assembled from multiple files in the same Soulseek result. A folder whose name resembles the requested album/release is strong evidence that it contains the full album plus useful sidecars such as cover/cue/log files; inspect filenames and pass filenames to enqueue_soulseek_download only when the category guidance says the whole folder is appropriate.",
                "llm_evaluation_hint": "For music album requests, prefer this whole-folder candidate when the folder name matches the requested artist/album and the file list looks like the album tracklist. For single-track requests, prefer the specific track instead.",
            })
            if len(public) >= limit:
                return public[:limit]
        for cand in candidates:
            item = cand.as_public_dict(len(public) + 1)
            item.setdefault("candidate_type", "file")
            item.setdefault("candidate_id", _soulseek_candidate_id(username=cand.username, filename=cand.filename))
            item.setdefault("file_requests", [{"filename": cand.filename, **({"size": int(cand.size_bytes)} if cand.size_bytes is not None else {})}])
            public.append(item)
            if len(public) >= limit:
                break
        return public[:limit]


    @staticmethod
    def _is_audio_candidate(candidate: SoulseekCandidate) -> bool:
        return (candidate.extension or "").lower() in {"mp3", "flac", "m4a", "aac", "ogg", "opus", "wav", "aiff", "alac", "ape"}

    @staticmethod
    def _is_safe_supporting_candidate(candidate: SoulseekCandidate) -> bool:
        suffix = (candidate.extension or Path(candidate.filename).suffix.lstrip(".")).lower()
        return suffix in {"jpg", "jpeg", "png", "webp", "gif", "cue", "log", "m3u", "m3u8", "txt", "nfo", "pdf"}

    @staticmethod
    def _candidate_sort_key(candidate: SoulseekCandidate) -> tuple:
        free_slot_rank = 0 if candidate.has_free_upload_slot is True else (1 if candidate.has_free_upload_slot is None else 2)
        queue_rank = int(candidate.queue_length) if candidate.queue_length is not None else 999999
        bitrate_rank = -int(candidate.bitrate or 0)
        speed_rank = -int(candidate.upload_speed or 0)
        size_rank = -int(candidate.size_bytes or 0)
        return (
            free_slot_rank,
            queue_rank,
            bitrate_rank,
            speed_rank,
            size_rank,
            str(candidate.username or "").casefold(),
            str(candidate.filename or "").casefold(),
        )

    @staticmethod
    def _truthy_flag(payload: dict[str, Any], *keys: str) -> bool:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str):
                if value.strip().lower() in {"1", "true", "yes", "locked", "private"}:
                    return True
                continue
            if value:
                return True
        return False



def _payload_count(payload: Any, key: str) -> int:
    """Return the largest integer count named ``key`` anywhere in a payload."""
    best = 0
    if isinstance(payload, dict):
        for item_key, value in payload.items():
            if str(item_key) == key:
                best = max(best, _int_or_none(value) or 0)
            if isinstance(value, (dict, list)):
                best = max(best, _payload_count(value, key))
    elif isinstance(payload, list):
        for value in payload:
            best = max(best, _payload_count(value, key))
    return best


def _top_level_len(payload: Any) -> int:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in ("responses", "data", "results", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return len(value)
            if isinstance(value, dict):
                return len(value)
    return 0


def _deep_file_count(payload: Any) -> int:
    """Best-effort count of file-looking rows in a nested slskd payload."""
    if isinstance(payload, list):
        return sum(_deep_file_count(item) for item in payload)
    if isinstance(payload, dict):
        if SlskdClient._looks_like_file_row(payload):
            return 1
        count = 0
        for value in payload.values():
            if isinstance(value, (dict, list)):
                count += _deep_file_count(value)
        return count
    return 0


def _payload_signature(payload: Any) -> str:
    try:
        return json.dumps(payload, sort_keys=True, default=str)[:4000]
    except Exception:
        return repr(payload)[:4000]


def _redact_search_payload(payload: Any, *, max_string: int = 500) -> Any:
    """Trim payloads for local diagnostics without including app secrets."""
    secret_keys = {"api_key", "apikey", "password", "token", "jwt", "key", "authorization"}
    if isinstance(payload, dict):
        out: dict[str, Any] = {}
        for key, value in payload.items():
            key_text = str(key)
            if key_text.replace("-", "_").casefold() in secret_keys:
                out[key_text] = "********"
            else:
                out[key_text] = _redact_search_payload(value, max_string=max_string)
        return out
    if isinstance(payload, list):
        return [_redact_search_payload(item, max_string=max_string) for item in payload[:50]]
    if isinstance(payload, str):
        return payload if len(payload) <= max_string else payload[:max_string] + "…"
    return payload


def re_safe_filename(value: str) -> str:
    """Return a filesystem-safe diagnostic filename fragment."""
    import re
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip("._")
    return text[:80] or "search"

def _looks_like_username(value: str) -> bool:
    text = str(value or "").strip()
    if not text or len(text) > 96:
        return False
    if "/" in text or "\\" in text:
        return False
    lowered = text.casefold()
    reserved = {"responses", "files", "lockedfiles", "locked_files", "data", "items", "results", "directories", "folder", "directory"}
    if lowered in reserved:
        return False
    # Soulseek usernames commonly contain dots, underscores, digits, and letters.
    import re
    return bool(re.fullmatch(r"[A-Za-z0-9_.@ -]+", text))


def _looks_like_remote_folder(value: str) -> bool:
    text = str(value or "").strip().strip("/\\")
    if not text:
        return False
    lowered = text.casefold()
    if lowered in {"files", "lockedfiles", "locked_files", "responses", "items", "results", "data"}:
        return False
    if "/" in text or "\\" in text:
        return True
    # Single directory buckets from slskd can be names such as "music" or "Album".
    # Treat them as folders only when they do not look like metadata keys.
    return lowered not in {"username", "user", "filename", "size", "extension", "attributes", "queue", "speed"}


def _soulseek_candidate_id(*, username: str, filename: str = "", filenames: list[str] | None = None) -> str:
    """Return a stable, non-secret id for an LJS-visible Soulseek candidate."""
    parts = [str(username or "").casefold(), str(filename or "").casefold()]
    for item in sorted(str(v or "").casefold() for v in (filenames or []) if str(v or "").strip()):
        parts.append(item)
    raw = "slskd|" + "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()[:16]

def _remote_parent(filename: str) -> str:
    text = str(filename or "").replace("\\", "/").strip("/")
    if "/" not in text:
        return ""
    return text.rsplit("/", 1)[0]


def _first_present(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload and payload.get(key) not in (None, ""):
            return payload.get(key)
    return None


def _int_or_none(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "free"}
    return bool(value)


def _name_tokens(text: str) -> set[str]:
    import re
    tokens = {tok for tok in re.split(r"[^a-z0-9]+", str(text or "").casefold()) if len(tok) >= 2}
    # These are request-shape words, not album identity.
    return tokens - {"album", "track", "song", "songs", "download", "grab", "get", "from", "by", "the", "a", "an", "please", "music"}


def _folder_query_score(folder: str, query_tokens: set[str]) -> float:
    if not query_tokens:
        return 0.0
    folder_tokens = _name_tokens(folder)
    if not folder_tokens:
        return 0.0
    overlap = query_tokens & folder_tokens
    if not overlap:
        return 0.0
    # Weight recall of the requested words more than folder precision because
    # Soulseek folders often contain extra path segments such as /Albums/music.
    recall = len(overlap) / max(1, len(query_tokens))
    precision = len(overlap) / max(1, len(folder_tokens))
    return (0.78 * recall) + (0.22 * precision)


def _remote_track_sort_key(candidate: SoulseekCandidate) -> tuple:
    import re
    name = Path(str(candidate.filename or "").replace("\\", "/")).name
    match = re.match(r"^\s*(\d{1,3})(?:[.,_ -]|$)", name)
    number = int(match.group(1)) if match else 9999
    return (number, name.casefold())
