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
        router.add_api_route("/api/web-research/test", self._web_research_test, methods=["POST"])
        router.add_api_route("/api/web-research/evidence", self._web_research_evidence, methods=["GET"])
        router.add_api_route("/api/category-web-research/test", self._category_web_research_test, methods=["POST"])
        router.add_api_route("/api/web-information-watches", self._web_information_watches, methods=["GET"])
        router.add_api_route("/api/web-information-watches", self._create_web_information_watch, methods=["POST"])
        router.add_api_route("/api/web-information-watches/{watch_id}/run", self._run_web_information_watch, methods=["POST"])
        router.add_api_route("/api/web-information-watches/{watch_id}/disable", self._disable_web_information_watch, methods=["POST"])
        router.add_api_route("/api/searxng/health", self._searxng_health, methods=["GET"])
        router.add_api_route("/api/searxng/install", self._searxng_install, methods=["POST"])
        router.add_api_route("/api/searxng/start", self._searxng_start, methods=["POST"])
        router.add_api_route("/api/searxng/repair", self._searxng_repair, methods=["POST"])
        router.add_api_route("/api/searxng/upgrade", self._searxng_upgrade, methods=["POST"])
        router.add_api_route("/api/searxng/rollback", self._searxng_rollback, methods=["POST"])
        router.add_api_route("/api/searxng/uninstall", self._searxng_uninstall, methods=["POST"])
        router.add_api_route("/api/searxng/stop", self._searxng_stop, methods=["POST"])
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


    async def _web_research_test(self, request: Request, _auth: bool = Depends(verify_auth)):
        """Run a bounded web-research smoke test and persist provenance when DB is available."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        from src.ai.web_reader import WebReader
        from src.core.models import WebResearchBudget, WebResearchRequest
        from src.search.web.research import WebResearchService

        settings = self._deps.settings_manager.settings
        research_request = WebResearchRequest(
            query=str(body.get("query") or "SearXNG documentation"),
            intent=str(body.get("intent") or "manual_test"),
            category_id=str(body.get("category_id") or ""),
            item_id=str(body.get("item_id") or ""),
            item_name=str(body.get("item_name") or ""),
            categories=body.get("categories") or settings.web_search.default_categories,
            language=str(body.get("language") or settings.web_search.default_language),
            time_range=str(body.get("time_range") or ""),
            max_results=int(body.get("max_results") or settings.web_search.max_results),
            budget=WebResearchBudget(max_urls_to_fetch=int(body.get("max_urls_to_fetch") or 3)),
        )
        repository = getattr(self._deps.db, "web_research", None) if self._deps.db else None
        bundle = await WebResearchService(
            settings.web_search,
            web_reader=WebReader(),
            repository=repository,
        ).collect_evidence(research_request)
        return bundle.model_dump()

    async def _web_research_evidence(
        self,
        category_id: str = Query(""),
        item_id: str = Query(""),
        limit: int = Query(50),
        _auth: bool = Depends(verify_auth),
    ):
        """Return recent persisted web-source evidence for diagnostics."""
        repository = getattr(self._deps.db, "web_research", None) if self._deps.db else None
        if not repository:
            return {"evidence": [], "error": "Web research repository is not configured."}
        rows = await repository.list_evidence(category_id=category_id, item_id=item_id, limit=limit)
        return {"evidence": rows}

    async def _category_web_research_test(self, request: Request, _auth: bool = Depends(verify_auth)):
        """Run a category-owned web-research diagnostic through the neutral orchestrator."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        category_id = str(body.get("category_id") or "").strip()
        item_id = str(body.get("item_id") or body.get("item_name") or "").strip()
        if not category_id or not item_id:
            raise HTTPException(status_code=400, detail="category_id and item_id/item_name are required")
        from src.ai.web_reader import WebReader
        from src.core.models import CategoryWebResearchInput
        from src.search.web.category_research import CategoryWebResearchService

        settings = self._deps.settings_manager.settings
        repository = getattr(self._deps.db, "web_research", None) if self._deps.db else None
        result = await CategoryWebResearchService(
            category_registry=self._deps.category_registry,
            config=settings.web_search,
            web_reader=WebReader(),
            repository=repository,
        ).research(CategoryWebResearchInput(
            category_id=category_id,
            item_id=item_id,
            item_name=str(body.get("item_name") or item_id),
            intent=str(body.get("intent") or "general_research"),
            language=str(body.get("language") or settings.web_search.default_language),
            context=body.get("context") if isinstance(body.get("context"), dict) else {},
        ))
        return result.model_dump(mode="json")

    def _make_web_information_watch_service(self):
        """Build the watch service used by web API endpoints."""
        from src.ai.web_reader import WebReader
        from src.core.models import WebSearchConfig
        from src.search.web.information_watch import WebInformationWatchService

        repository = getattr(self._deps.db, "web_research", None) if self._deps.db else None
        config = self._deps.settings_manager.settings.web_search if self._deps.settings_manager else WebSearchConfig()
        return WebInformationWatchService(
            repository=repository,
            config=config,
            web_reader=WebReader(),
            category_registry=self._deps.category_registry,
        )

    async def _web_information_watches(
        self,
        enabled_only: bool = Query(False),
        category_id: str = Query(""),
        item_id: str = Query(""),
        limit: int = Query(100),
        _auth: bool = Depends(verify_auth),
    ):
        """List durable web-information watches for the UI and diagnostics."""
        service = self._make_web_information_watch_service()
        watches = await service.list_watches(
            enabled_only=bool(enabled_only),
            category_id=str(category_id or ""),
            item_id=str(item_id or ""),
            limit=int(limit or 100),
        )
        return {"ok": True, "watches": watches}

    async def _create_web_information_watch(self, request: Request, _auth: bool = Depends(verify_auth)):
        """Create a durable web-information watch and schedule its bounded recurring check when available."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        service = self._make_web_information_watch_service()
        settings = self._deps.settings_manager.settings
        watch = await service.create_watch(
            title=str(body.get("title") or body.get("objective") or "Web information watch"),
            objective=str(body.get("objective") or body.get("query") or body.get("title") or ""),
            query=str(body.get("query") or ""),
            intent=str(body.get("intent") or "general_research"),
            owner_type="category_item" if body.get("category_id") or body.get("item_id") else "user_task",
            category_id=str(body.get("category_id") or ""),
            item_id=str(body.get("item_id") or body.get("item_name") or ""),
            item_name=str(body.get("item_name") or body.get("item_id") or ""),
            language=str(body.get("language") or settings.web_search.default_language),
            cadence_minutes=int(body.get("cadence_minutes") or 10080),
            delay_minutes=body.get("delay_minutes"),
            notify_only_if_meaningful=bool(body.get("notify_only_if_meaningful", True)),
            llm_evaluation_required=bool(body.get("llm_evaluation_required", True)),
            allow_download_queueing=bool(body.get("allow_download_queueing", False)),
            query_plan=body.get("query_plan") if isinstance(body.get("query_plan"), dict) else {},
            user_feedback=body.get("user_feedback") if isinstance(body.get("user_feedback"), dict) else {},
        )
        scheduled_task = None
        if self._deps.prompt_scheduler:
            from src.search.web.information_watch import WebInformationWatchPromptBuilder

            scheduled_task = await self._deps.prompt_scheduler.create_task(
                prompt=WebInformationWatchPromptBuilder.scheduled_prompt(watch),
                interval_minutes=watch.cadence_minutes,
                user_id=str(body.get("user_id") or "web"),
                channel=str(body.get("channel") or "web"),
                title=f"Watch: {watch.title}",
                task_type="condition_check",
                schedule_type="recurring",
                delay_minutes=body.get("delay_minutes") if body.get("delay_minutes") is not None else watch.cadence_minutes,
                session_id=str(body.get("session_id") or "web_rest"),
            )
        return {
            "ok": True,
            "watch": watch.model_dump(mode="json"),
            "scheduled_task": {
                "id": scheduled_task.id,
                "next_run_at": scheduled_task.next_run_at.isoformat() if scheduled_task.next_run_at else None,
                "interval_minutes": scheduled_task.interval_minutes,
            } if scheduled_task else None,
            "message": "Created web information watch. It will use fetched evidence and LLM review before notifying.",
        }

    async def _run_web_information_watch(self, watch_id: str, _auth: bool = Depends(verify_auth)):
        """Run one durable web-information watch immediately."""
        service = self._make_web_information_watch_service()
        return await service.run_watch(str(watch_id or ""))

    async def _disable_web_information_watch(self, watch_id: str, request: Request, _auth: bool = Depends(verify_auth)):
        """Disable a web-information watch without deleting its history."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        service = self._make_web_information_watch_service()
        watch = await service.disable_watch(str(watch_id or ""), reason=str(body.get("reason") or "user_requested"))
        return {"ok": bool(watch), "watch": watch, "message": "Watch disabled." if watch else "Watch not found."}


    async def _searxng_health(self, _auth: bool = Depends(verify_auth)):
        """Return managed SearXNG sidecar health without adopting manual instances."""
        deps = self._deps
        if not deps.searxng_manager:
            return {"installed": False, "running": False, "error": "SearXNG manager not configured"}
        return await deps.searxng_manager.health_check(deps.settings_manager.settings)

    async def _searxng_install(self, _auth: bool = Depends(verify_auth)):
        return await self._execute_action('system_install_searxng', {})

    async def _searxng_start(self, _auth: bool = Depends(verify_auth)):
        return await self._execute_action('system_start_searxng', {})

    async def _searxng_repair(self, _auth: bool = Depends(verify_auth)):
        return await self._execute_action('system_repair_searxng', {})

    async def _searxng_upgrade(self, _auth: bool = Depends(verify_auth)):
        return await self._execute_action('system_upgrade_searxng', {})

    async def _searxng_rollback(self, _auth: bool = Depends(verify_auth)):
        return await self._execute_action('system_rollback_searxng', {})

    async def _searxng_uninstall(self, _auth: bool = Depends(verify_auth)):
        return await self._execute_action('system_uninstall_searxng', {})

    async def _searxng_stop(self, _auth: bool = Depends(verify_auth)):
        return await self._execute_action('system_stop_searxng', {})

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
