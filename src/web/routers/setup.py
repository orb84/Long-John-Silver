"""
Setup wizard router for LJS.

Handles the first-time setup flow: password, paths, LLM, channels,
language, and completion. All endpoints are unprotected (no auth
required) since setup runs before a password is configured.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger

from src.core.models import ActionCommand, ActionSource
from src.integrations.trakt_defaults import has_bundled_trakt_client_id, resolve_trakt_client_id
from src.web.dependencies import WebDependencies, verify_auth


class SetupRouter:
    """Class-based router for setup wizard endpoints."""

    def __init__(self, deps: WebDependencies) -> None:
        self._deps = deps

    async def _execute_action(self, name: str, arguments: dict) -> dict:
        """Execute an action through the gateway and return the data dict."""
        result = await self._deps.action_gateway.execute(ActionCommand(
            name=name,
            arguments=arguments,
            source=ActionSource.UI,
        ))
        if not result.ok:
            raise HTTPException(status_code=400, detail=result.error or 'Action failed')
        return result.data

    def get_router(self) -> APIRouter:
        """Build and return an APIRouter with setup wizard endpoints."""
        router = APIRouter()
        router.add_api_route("/api/setup/requirements", self._setup_requirements, methods=["GET"])
        router.add_api_route("/api/setup/password", self._setup_password, methods=["POST"])
        router.add_api_route("/api/setup/paths", self._setup_paths, methods=["POST"])
        router.add_api_route("/api/setup/category-config", self._setup_category_config, methods=["POST"])
        router.add_api_route("/api/setup/llm", self._setup_llm, methods=["POST"])
        router.add_api_route("/api/setup/embeddings", self._setup_embeddings, methods=["POST"])
        router.add_api_route("/api/setup/channels", self._setup_channels, methods=["POST"])
        router.add_api_route("/api/setup/language", self._setup_language, methods=["POST"])
        router.add_api_route("/api/setup/sharing", self._setup_sharing, methods=["POST"])
        router.add_api_route("/api/setup/startup", self._setup_startup, methods=["POST"])
        router.add_api_route("/api/setup/complete", self._setup_complete, methods=["POST"])
        return router


    async def _setup_requirements(self):
        """Return category-first setup requirements and current status."""
        deps = self._deps
        settings = deps.settings_manager.settings
        categories = []
        if deps.category_registry:
            for category in deps.category_registry.list_all():
                manifest = category.manifest(settings=settings)
                categories.append({
                    "category_id": manifest.category_id,
                    "display_name": manifest.display_name,
                    "description": manifest.description,
                    "requirements": [req.model_dump() for req in manifest.setup_requirements],
                })
        return {
            "categories": categories,
            "global": {
                "download_dir": settings.download_dir,
                "settings_path": str(deps.settings_manager.settings_path),
                "settings_template_path": str(deps.settings_manager.settings_template_path),
                "category_config_dir": str(deps.settings_manager.category_config_dir),
                "category_config_templates_dir": str(deps.settings_manager.category_template_dir),
                "category_definitions_dir": str(deps.settings_manager.category_definition_dir),
                "jackett_configured": bool(settings.jackett_url and settings.jackett_api_key),
                "direct_scraper_fallback": bool(settings.direct_scraper_fallback),
                "web_search": settings.web_search.model_dump(),
                "embeddings": settings.embeddings.model_dump(),
                "tmdb_configured": bool(settings.category_service_value("media", "tmdb", "api_key")),
                "trakt_client_available": bool(resolve_trakt_client_id(settings)),
                "trakt_uses_builtin_client": has_bundled_trakt_client_id(),
                "trakt_connected": bool(settings.category_service_value("media", "trakt", "access_token")),
            },
        }

    async def _setup_password(self, request: Request, _auth: bool = Depends(verify_auth)):
        body = await request.json()
        password = body.get("password", "")
        confirm = body.get("confirm", "")
        if password and password != confirm:
            raise HTTPException(status_code=400, detail="Passwords do not match")
        return await self._execute_action('setup_password', {
            'password': password, 'confirm': confirm,
        })

    async def _setup_paths(self, request: Request, _auth: bool = Depends(verify_auth)):
        body = await request.json()
        return await self._execute_action('setup_paths', dict(body))

    async def _setup_llm(self, request: Request, _auth: bool = Depends(verify_auth)):
        body = await request.json()
        return await self._execute_action('setup_llm', dict(body))

    async def _setup_category_config(self, request: Request, _auth: bool = Depends(verify_auth)):
        """Save first-run category-local services/preferences.

        This is the setup-safe counterpart of Compass category saves.  It keeps
        TMDB/Trakt/Plex/OpenSubtitles, paths, and media download preferences in
        ignored category config rather than global settings.
        """
        body = await request.json()
        payload = body.get("category_settings") if isinstance(body, dict) else None
        return await self._execute_action('setup_category_config', {'category_settings': payload or {}})


    async def _setup_embeddings(self, request: Request, _auth: bool = Depends(verify_auth)):
        """Save first-run semantic-memory embedding settings."""
        body = await request.json()
        allowed = {
            "enabled", "provider", "builtin_model", "dimension", "cache_dir",
            "auto_download", "warmup_on_startup", "max_model_size_mb",
        }
        return await self._execute_action('setup_embeddings', {key: body[key] for key in allowed if key in body})

    async def _setup_channels(self, request: Request, _auth: bool = Depends(verify_auth)):
        body = await request.json()
        return await self._execute_action('setup_channels', dict(body))

    async def _setup_language(self, request: Request, _auth: bool = Depends(verify_auth)):
        body = await request.json()
        lang = body.get("language", "English")
        return await self._execute_action('setup_language', {'language': lang})

    async def _setup_sharing(self, request: Request, _auth: bool = Depends(verify_auth)):
        """Save first-run sharing and seed-in-place choices."""
        body = await request.json()
        allowed = {
            "enabled", "mode", "library_upload_speed_kbps", "active_seed_slots",
            "seed_ratio_target", "seed_duration_hours", "pause_when_downloading",
            "category_overrides",
        }
        return await self._execute_action('setup_sharing', {key: body[key] for key in allowed if key in body})


    async def _setup_startup(self, request: Request, _auth: bool = Depends(verify_auth)):
        """Save first-run launch-at-login preference."""
        body = await request.json()
        return await self._execute_action('setup_startup', {'enabled': bool(body.get('enabled'))})

    async def _setup_complete(self, request: Request, _auth: bool = Depends(verify_auth)):
        """Complete setup only when required category-first checks pass."""
        validation = await self._validate_setup()
        if validation["missing_required"]:
            return {
                "status": "blocked",
                "setup_complete": False,
                "missing_required": validation["missing_required"],
                "warnings": validation["warnings"],
            }
        data = await self._execute_action('setup_complete', {})
        data["warnings"] = validation["warnings"]
        self._start_post_setup_tasks()
        return data

    def _start_post_setup_tasks(self) -> None:
        """Start services that become configurable during first-run setup.

        The user should not need to restart LJS after setup.  Library paths and
        communication-channel tokens are collected by the setup wizard, so this
        method schedules an immediate library scan and restarts configured
        communication bridges in the background after setup is marked complete.
        """
        deps = self._deps
        supervisor = getattr(deps, "supervisor", None)
        if not supervisor:
            logger.warning("Post-setup tasks skipped: task supervisor is unavailable")
            return

        if getattr(deps, "scheduler", None):
            if hasattr(deps.scheduler, "request_library_scan"):
                deps.scheduler.request_library_scan(force=True, refresh_metadata=True, reason="post_setup")
            else:
                supervisor.spawn_one_shot(
                    "post_setup_library_scan",
                    deps.scheduler.scan_library(force=True),
                )

        if getattr(deps, "comms_registry", None):
            async def _restart_comms() -> None:
                try:
                    await deps.comms_registry.restart_configured(
                        settings=deps.settings_manager.settings,
                        assistant=deps.assistant,
                        notifications=deps.notifications,
                        supervisor=deps.supervisor,
                    )
                except Exception as exc:  # pragma: no cover - defensive background logging
                    logger.warning(f"Post-setup communication bridge restart failed: {exc}")

            supervisor.spawn_one_shot("post_setup_comms_restart", _restart_comms())

    async def _validate_setup(self) -> dict:
        """Validate essentials before allowing first-run setup completion."""
        deps = self._deps
        settings = deps.settings_manager.settings
        missing: list[dict] = []
        warnings: list[dict] = []

        if not settings.web_password_hash:
            warnings.append({
                "id": "web_password",
                "label": "Web password",
                "message": "No admin password is set. LJS will allow open local access until you set one in Settings.",
            })
        if not settings.download_dir:
            missing.append({"id": "download_dir", "label": "Download folder", "message": "Choose where active downloads are stored."})
        if not settings.llm.active_provider or not settings.llm.model:
            missing.append({"id": "llm", "label": "LLM provider", "message": "Choose an LLM provider and model."})
        if settings.llm.active_provider not in {"ollama", "lm_studio", "local"} and not settings.llm.api_key:
            warnings.append({"id": "llm_api_key", "label": "LLM API key", "message": "Remote LLM providers usually need an API key."})

        if deps.category_registry:
            for category in deps.category_registry.list_all():
                for requirement in category.setup_requirements(settings):
                    if requirement.required and not requirement.configured:
                        missing.append({
                            "id": f"{category.category_id}.{requirement.id}",
                            "label": requirement.label,
                            "message": requirement.description,
                        })
                    elif not requirement.configured and requirement.severity in {"recommended", "warning"}:
                        warnings.append({
                            "id": f"{category.category_id}.{requirement.id}",
                            "label": requirement.label,
                            "message": requirement.description,
                        })
        if not resolve_trakt_client_id(settings):
            warnings.append({
                "id": "trakt_client",
                "label": "Trakt client",
                "message": "This build is missing the bundled public Trakt Client ID; add it to src/integrations/trakt_defaults.py or set LJS_BUNDLED_TRAKT_CLIENT_ID.",
            })
        elif not settings.category_service_value("media", "trakt", "access_token"):
            warnings.append({
                "id": "trakt_account",
                "label": "Trakt account",
                "message": "Trakt is optional, but linking it enables personalized recommendations and watch-state aware automation.",
            })


        if getattr(settings, "embeddings", None) and settings.embeddings.enabled and settings.embeddings.provider == "disabled":
            warnings.append({
                "id": "semantic_memory",
                "label": "Semantic memory",
                "message": "Semantic embeddings are disabled, so long-term memory recall and taste profiling will be much weaker.",
            })

        if settings.web_search.enabled:
            from src.search.web.service import WebSearchService

            health = await WebSearchService(settings.web_search).health_check()
            if not health.ok:
                warnings.append({"id": "web_search", "label": "Web search", "message": health.last_error or "Web search provider is not healthy."})
        return {"missing_required": missing, "warnings": warnings}
