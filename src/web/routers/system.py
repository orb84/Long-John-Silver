"""
System router for LJS.

Provides endpoints for system operations: logs, torrent races, browser
runtime, Jackett management, Trakt OAuth, user auth, comms bridges,
and WhatsApp webhooks.
"""

import secrets
from pathlib import Path
from src.utils.log_sanitizer import redact_secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from loguru import logger

from src.core.models import ActionCommand, ActionSource
from src.integrations.trakt import TraktClient
from src.integrations.trakt_defaults import resolve_trakt_client_id, trakt_redirect_uri_for_client, is_bundled_trakt_client_id
from src.web.dependencies import WebDependencies, verify_auth


class SystemRouter:
    """Class-based router for system and infrastructure endpoints."""

    _TRAKT_CONNECTED_HTML = """
<html>
    <body style="font-family: sans-serif; display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; background: #121212; color: #eee;">
        <h1>Trakt Connected!</h1>
        <p>LJS is now linked to your Trakt account.</p>
        <button onclick="window.close()" style="padding: 10px 20px; background: #007bff; border: none; color: white; border-radius: 5px; cursor: pointer;">Close this window</button>
        <script>
            if (window.opener) {
                window.opener.postMessage('trakt_connected', '*');
            }
            setTimeout(function() {
                 window.location.href = '/settings';
            }, 2000);
        </script>
    </body>
</html>
"""

    def __init__(self, deps: WebDependencies) -> None:
        self._deps = deps

    async def _execute_action(self, name: str, arguments: dict) -> dict:
        """Execute an action through the gateway and return the data dict.

        Raises HTTPException on failure with an appropriate status code.
        """
        result = await self._deps.action_gateway.execute(ActionCommand(
            name=name,
            arguments=arguments,
            source=ActionSource.UI,
        ))
        if not result.ok:
            code = 404 if 'not found' in (result.error or '').lower() else 400
            raise HTTPException(status_code=code, detail=result.error or 'Action failed')
        return result.data

    def get_router(self) -> APIRouter:
        """Build and return an APIRouter with system and infrastructure endpoints."""
        router = APIRouter()
        router.add_api_route("/api/system/logs", self._get_logs, methods=["GET"])
        router.add_api_route("/api/races", self._get_race_status, methods=["GET"])
        router.add_api_route("/api/races/{download_id}", self._get_race_detail, methods=["GET"])
        router.add_api_route("/api/browser/health", self._browser_health, methods=["GET"])
        router.add_api_route("/api/browser/install", self._install_playwright, methods=["POST"])
        router.add_api_route("/api/search/health", self._search_health, methods=["GET"])
        router.add_api_route("/api/web-search/health", self._web_search_health, methods=["GET"])
        router.add_api_route("/api/web-search/test", self._web_search_test, methods=["POST"])
        router.add_api_route("/api/jackett/health", self._jackett_health, methods=["GET"])
        router.add_api_route("/api/jackett/install", self._jackett_install, methods=["POST"])
        router.add_api_route("/api/jackett/start", self._jackett_start, methods=["POST"])
        router.add_api_route("/api/soulseek/health", self._soulseek_health, methods=["GET"])
        router.add_api_route("/api/soulseek/install", self._soulseek_install, methods=["POST"])
        router.add_api_route("/api/soulseek/start", self._soulseek_start, methods=["POST"])
        router.add_api_route("/api/soulseek/check-login", self._soulseek_check_login, methods=["POST"])
        router.add_api_route("/api/soulseek/stop", self._soulseek_stop, methods=["POST"])
        router.add_api_route("/api/jackett/configure-default-indexers", self._jackett_configure_default_indexers, methods=["POST"])
        router.add_api_route("/api/jackett/indexers", self._jackett_indexers, methods=["GET"])
        router.add_api_route("/api/jackett/configure-indexers", self._jackett_configure_indexers, methods=["POST"])
        router.add_api_route("/api/jackett/indexers/{indexer_id}/config", self._jackett_indexer_config, methods=["GET"])
        router.add_api_route("/api/jackett/indexers/{indexer_id}/configure", self._jackett_configure_custom_indexer, methods=["POST"])
        router.add_api_route("/api/trakt/auth", self._trakt_auth, methods=["GET"])
        router.add_api_route("/api/trakt/callback", self._trakt_callback, methods=["GET"])
        router.add_api_route("/api/auth/login", self._auth_login, methods=["POST"])
        router.add_api_route("/api/auth/register", self._auth_register, methods=["POST"])
        router.add_api_route("/api/comms/bridges", self._list_comms_bridges, methods=["GET"])
        router.add_api_route("/api/comms/bridges/{bridge_id}/install", self._install_comms_bridge, methods=["POST"])
        router.add_api_route("/api/comms/bridges/{bridge_id}/status", self._comms_bridge_status, methods=["GET"])
        router.add_api_route("/api/comms/whatsapp/webhook", self._whatsapp_webhook_verify, methods=["GET"])
        router.add_api_route("/api/comms/whatsapp/webhook", self._whatsapp_webhook_receive, methods=["POST"])
        return router

    async def _get_logs(
        self,
        lines: int = 100,
        level: str = Query("all"),
        _auth: bool = Depends(verify_auth),
    ):
        """Return a bounded, secret-redacted tail of the app log.

        Voyage Logs is a browser terminal, not a full log archival viewer.  The
        endpoint clamps requested line counts so a long-running dashboard cannot
        accidentally ask the server and browser to shuttle megabytes every poll.
        ``level=warnings`` returns only WARNING/ERROR/CRITICAL rows so the UI can
        expose actionable diagnostics without asking the user to scan debug logs.
        """
        log_path = Path("logs/ljs.log")
        if not log_path.exists():
            return {"logs": ["Log file not found."], "line_limit": 0, "level": level}
        try:
            requested = int(lines or 100)
        except (TypeError, ValueError):
            requested = 100
        requested = max(1, min(requested, 500))
        wanted = str(level or "all").lower()
        try:
            with open(log_path, "r") as f:
                all_lines = f.readlines()
            if wanted == "warnings":
                all_lines = [line for line in all_lines if self._is_warning_or_error_log_line(line)]
            elif wanted == "errors":
                all_lines = [line for line in all_lines if self._is_error_log_line(line)]
            return {
                "logs": [redact_secrets(line) for line in all_lines[-requested:]],
                "line_limit": requested,
                "level": wanted,
            }
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": f"Failed to read logs: {e}"})

    @staticmethod
    def _is_warning_or_error_log_line(line: str) -> bool:
        """Return whether a loguru/stdlib line is actionable in the UI."""
        text = str(line or "")
        return any(token in text for token in ("| WARNING", "| ERROR", "| CRITICAL", " WARNING ", " ERROR ", " CRITICAL "))

    @staticmethod
    def _is_error_log_line(line: str) -> bool:
        """Return whether a line represents an error/critical diagnostic."""
        text = str(line or "")
        return any(token in text for token in ("| ERROR", "| CRITICAL", " ERROR ", " CRITICAL "))

    async def _get_race_status(self):
        deps = self._deps
        if deps.torrent_racer:
            return {"active_races": deps.torrent_racer.active_race_count}
        return {"active_races": 0}

    async def _get_race_detail(self, download_id: str):
        deps = self._deps
        if not deps.torrent_racer:
            return {"active": False}
        status = deps.torrent_racer.get_race_status(download_id)
        if not status:
            return {"active": False}
        return {"active": True, **{k: v for k, v in status.items() if k != "primary_magnet"}}

    async def _browser_health(self):
        deps = self._deps
        if not deps.browser_runtime:
            return {"available": False, "error": "Browser runtime not configured"}
        try:
            health = await deps.browser_runtime.health_check()
            return health.model_dump()
        except Exception as e:
            return {"available": False, "error": str(e)}

    async def _install_playwright(self, _auth: bool = Depends(verify_auth)):
        return await self._execute_action('system_install_playwright', {})


    async def _search_health(self):
        """Return torrent search and web-search health in one endpoint."""
        deps = self._deps
        torrent = {"primary_provider": None, "provider_count": 0, "providers": [], "degraded": True}
        if deps.search_aggregator:
            torrent = await deps.search_aggregator.health_check()
        from src.search.web.service import WebSearchService

        web_health = await WebSearchService(deps.settings_manager.settings.web_search).health_check()
        return {"torrent": torrent, "web_search": web_health.model_dump()}

    async def _web_search_health(self):
        """Return health for the configured general web-search provider."""
        from src.search.web.service import WebSearchService

        service = WebSearchService(self._deps.settings_manager.settings.web_search)
        health = await service.health_check()
        return health.model_dump()

    async def _web_search_test(self, request: Request, _auth: bool = Depends(verify_auth)):
        """Run a harmless provider test query for setup diagnostics."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        query = str(body.get("query") or "ubuntu")
        max_results = int(body.get("max_results") or 5)
        from src.search.web.service import WebSearchService

        service = WebSearchService(self._deps.settings_manager.settings.web_search)
        result = await service.search(query, max_results=max_results)
        return result.model_dump()

    async def _jackett_health(self):
        deps = self._deps
        if not deps.jackett_manager:
            return {"installed": False, "running": False, "error": "Jackett manager not configured"}
        return await deps.jackett_manager.health_check()

    async def _jackett_install(self, _auth: bool = Depends(verify_auth)):
        return await self._execute_action('system_install_jackett', {})

    async def _jackett_start(self, _auth: bool = Depends(verify_auth)):
        return await self._execute_action('system_start_jackett', {})

    async def _soulseek_health(self, _auth: bool = Depends(verify_auth)):
        deps = self._deps
        if not deps.slskd_manager:
            return {"installed": False, "running": False, "error": "slskd manager not configured"}
        return await deps.slskd_manager.health_check(deps.settings_manager.settings)

    async def _soulseek_install(self, _auth: bool = Depends(verify_auth)):
        return await self._execute_action('system_install_soulseek', {})

    async def _soulseek_start(self, _auth: bool = Depends(verify_auth)):
        return await self._execute_action('system_start_soulseek', {})

    async def _soulseek_check_login(self, request: Request, _auth: bool = Depends(verify_auth)):
        try:
            body = await request.json()
        except Exception:
            body = {}
        return await self._execute_action('system_check_soulseek_login', body if isinstance(body, dict) else {})

    async def _soulseek_stop(self, _auth: bool = Depends(verify_auth)):
        return await self._execute_action('system_stop_soulseek', {"disable": True})

    async def _jackett_configure_default_indexers(self, _auth: bool = Depends(verify_auth)):
        return await self._execute_action('system_configure_default_indexers', {})

    async def _jackett_indexers(self, _auth: bool = Depends(verify_auth)):
        return await self._execute_action('system_jackett_indexer_diagnostics', {})

    async def _jackett_configure_indexers(self, request: Request, _auth: bool = Depends(verify_auth)):
        try:
            body = await request.json()
        except Exception:
            body = {}
        profile = str(body.get('profile') or 'balanced_public')
        return await self._execute_action('system_configure_jackett_indexers', {'profile': profile})

    async def _jackett_indexer_config(self, indexer_id: str, _auth: bool = Depends(verify_auth)):
        return await self._execute_action('system_jackett_indexer_config_schema', {'indexer_id': indexer_id})

    async def _jackett_configure_custom_indexer(self, indexer_id: str, request: Request, _auth: bool = Depends(verify_auth)):
        try:
            body = await request.json()
        except Exception:
            body = {}
        values = body.get('values') or body.get('config') or {}
        return await self._execute_action('system_configure_jackett_custom_indexer', {
            'indexer_id': indexer_id,
            'values': values,
        })

    async def _trakt_auth(self, request: Request, client_id: str = None, _auth: bool = Depends(verify_auth)):
        deps = self._deps
        settings = deps.settings_manager.settings
        actual_client_id = resolve_trakt_client_id(settings, client_id)
        if not actual_client_id:
            return JSONResponse(
                status_code=400,
                content={"error": "This build does not include the bundled Trakt Client ID, and no custom Client ID was provided."},
            )
        client = TraktClient(actual_client_id)
        verifier, challenge = client.generate_pkce_pair()
        state = secrets.token_urlsafe(16)
        base_url = str(request.base_url).rstrip("/")
        redirect_uri = trakt_redirect_uri_for_client(actual_client_id, base_url)
        deps.trakt_pkce_store[state] = {
            "verifier": verifier,
            "client_id": actual_client_id,
            "redirect_uri": redirect_uri,
            "flow": "oob" if is_bundled_trakt_client_id(actual_client_id) else "callback",
        }
        logger.info(
            "Generating Trakt auth URL. base_url: {}, redirect_uri: {}, flow: {}",
            str(request.base_url),
            redirect_uri,
            deps.trakt_pkce_store[state]["flow"],
        )
        auth_url = client.get_auth_url(redirect_uri, state, challenge)
        return {"auth_url": auth_url, "flow": deps.trakt_pkce_store[state]["flow"]}

    async def _trakt_callback(self, request: Request, code: str = None, state: str = None, error: str = None):
        deps = self._deps
        if error:
            return HTMLResponse(f"<h1>Authentication Failed</h1><p>Error from Trakt: {error}</p>")
        if not code:
            return HTMLResponse("<h1>Error</h1><p>Missing authorization code.</p>")
        
        pkce_record = None
        if state:
            pkce_record = deps.trakt_pkce_store.pop(state, None)
        if not pkce_record and deps.trakt_pkce_store:
            # Fallback for OOB flows where Trakt does not echo state.
            latest_state = list(deps.trakt_pkce_store.keys())[-1]
            pkce_record = deps.trakt_pkce_store.pop(latest_state)

        if not pkce_record:
            return HTMLResponse("<h1>Error</h1><p>Invalid state or session expired.</p>")

        verifier = pkce_record["verifier"]
        settings = deps.settings_manager.settings
        client_id = resolve_trakt_client_id(settings, pkce_record.get("client_id"))
        client = TraktClient(client_id)
        redirect_uri = pkce_record.get("redirect_uri") or trakt_redirect_uri_for_client(client_id, str(request.base_url).rstrip("/"))
        logger.info(
            "Exchanging Trakt code for token. base_url: {}, redirect_uri: {}, flow: {}",
            str(request.base_url),
            redirect_uri,
            pkce_record.get("flow") or ("oob" if is_bundled_trakt_client_id(client_id) else "callback"),
        )
        tokens = await client.exchange_code_for_token(code, redirect_uri, verifier)
        if not tokens:
            logger.error("Trakt exchange failed (no tokens returned).")
            return HTMLResponse("<h1>Error</h1><p>Failed to exchange code for token.</p>")
        media = settings.category_settings.setdefault("media", {})
        services = media.setdefault("services", {})
        trakt = services.setdefault("trakt", {})
        if not isinstance(trakt, dict):
            trakt = {}
            services["trakt"] = trakt
        trakt["client_id"] = client_id
        trakt["access_token"] = tokens.get("access_token")
        trakt["refresh_token"] = tokens.get("refresh_token")
        deps.settings_manager.save(settings)
        return HTMLResponse(self._TRAKT_CONNECTED_HTML)

    async def _auth_login(self, request: Request):
        deps = self._deps
        body = await request.json()
        username = body.get("username", "")
        password = body.get("password", "")
        user_row = await deps.db.users.get_user_by_username(username)
        if not user_row:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        if not deps.auth_service.verify_password(password, user_row["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        token = deps.auth_service.create_token(username)
        session_id = f"web_{username}"
        await deps.db.users.create_session(session_id=session_id, user_id=user_row["id"], channel="web")
        return {"token": token, "username": username}

    async def _auth_register(self, request: Request, _auth: bool = Depends(verify_auth)):
        deps = self._deps
        body = await request.json()
        username = body.get("username", "").strip()
        password = body.get("password", "")
        if not username or not password:
            raise HTTPException(status_code=400, detail="Username and password are required")
        data = await self._execute_action('system_auth_register', {
            'username': username, 'password': password,
        })
        if not data.get('id'):
            raise HTTPException(status_code=409, detail=data.get('error', 'Registration failed'))
        return data

    async def _list_comms_bridges(self):
        deps = self._deps
        if not deps.comms_registry:
            return {"bridges": []}
        return {"bridges": deps.comms_registry.list_bridges()}

    async def _install_comms_bridge(self, bridge_id: str):
        data = await self._execute_action('system_install_comms_bridge', {'bridge_id': bridge_id})
        if data.get('status') != 'installed':
            raise HTTPException(status_code=500, detail=data.get('error', f"Failed to install bridge '{bridge_id}'"))
        return data

    async def _comms_bridge_status(self, bridge_id: str):
        deps = self._deps
        if not deps.comms_registry:
            return {"installed": False, "configured": False}
        installed = deps.comms_registry.is_bridge_installed(bridge_id)
        bridges = deps.comms_registry.list_bridges()
        bridge_info = next((b for b in bridges if b["id"] == bridge_id), None)
        return {
            "installed": installed,
            "configured": bridge_info is not None,
            "running": deps.comms_registry.get_running(bridge_id) is not None,
        }

    async def _whatsapp_webhook_verify(self, request: Request):
        deps = self._deps
        mode = request.query_params.get("hub.mode", "")
        token = request.query_params.get("hub.verify_token", "")
        challenge = request.query_params.get("hub.challenge", "")
        bridge = deps.comms_registry.get_running("whatsapp") if deps.comms_registry else None
        if not bridge:
            bridge = deps.comms_registry.get_instance("whatsapp") if deps.comms_registry else None
        if bridge and hasattr(bridge, "verify_webhook"):
            result = bridge.verify_webhook(mode, challenge, token)
            if result is not None:
                return HTMLResponse(content=result)
        raise HTTPException(status_code=403, detail="Webhook verification failed")

    async def _whatsapp_webhook_receive(self, request: Request):
        deps = self._deps
        body = await request.json()
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                messages = value.get("messages", [])
                for msg in messages:
                    if msg.get("type") != "text":
                        continue
                    from_phone = msg.get("from", "")
                    text_body = msg.get("text", {}).get("body", "")
                    if not from_phone or not text_body:
                        continue
                    bridge = deps.comms_registry.get_running("whatsapp") if deps.comms_registry else None
                    if not bridge:
                        bridge = deps.comms_registry.get_instance("whatsapp") if deps.comms_registry else None
                    if bridge and hasattr(bridge, "handle_incoming"):
                        bridge.set_last_notification_phone(from_phone)
                        response = await bridge.handle_incoming(from_phone, text_body)
                        if response:
                            await bridge.send_message(from_phone, response)
        return {"status": "ok"}
