"""
Settings router for LJS.

All settings-related endpoints: LLM, quality, tokens, auto-download,
tiers, library paths, bandwidth, search providers, integrations,
bridges, password, and WhatsApp settings.

All mutation endpoints delegate to ActionGateway for unified audit
and event emission.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from src.core.models import ActionCommand, ActionSource
from src.core.models import SizeLimitMode
from src.llm_providers.context_limits import (
    FALLBACK_CONTEXT_LIMIT,
    MAX_MANUAL_CONTEXT_LIMIT,
    MIN_USER_CONTEXT_LIMIT,
    probe_endpoint_context_limit,
)
from src.web.dependencies import WebDependencies, verify_auth


class SettingsRouter:
    """Class-based router for settings endpoints."""

    def __init__(self, deps: WebDependencies) -> None:
        self._deps = deps

    def get_router(self) -> APIRouter:
        """Build and return an APIRouter with settings endpoints."""
        router = APIRouter()
        router.add_api_route("/api/settings", self._get_settings, methods=["GET"])
        router.add_api_route("/api/settings", self._save_settings, methods=["POST"])
        router.add_api_route("/api/settings/llm", self._update_llm, methods=["POST"])
        router.add_api_route("/api/settings/llm/context", self._get_llm_context, methods=["GET"])
        router.add_api_route("/settings/quality", self._update_quality_form, methods=["POST"])
        router.add_api_route("/api/settings/quality", self._update_quality_api, methods=["POST"])
        router.add_api_route("/settings/tokens", self._update_tokens, methods=["POST"])
        router.add_api_route("/api/settings/auto_download", self._update_auto_download, methods=["POST"])
        router.add_api_route("/api/settings/tiers", self._update_tiers, methods=["POST"])
        router.add_api_route("/api/settings/embeddings", self._update_embeddings, methods=["POST"])
        router.add_api_route("/api/settings/embeddings/status", self._embedding_status, methods=["GET"])
        router.add_api_route("/api/settings/embeddings/reindex", self._embedding_reindex, methods=["POST"])
        router.add_api_route("/api/settings/library", self._update_library, methods=["POST"])
        router.add_api_route("/api/settings/bandwidth_data", self._get_bandwidth_data, methods=["GET"])
        router.add_api_route("/api/settings/bandwidth", self._update_bandwidth, methods=["POST"])
        router.add_api_route("/api/settings/search", self._update_search, methods=["POST"])
        router.add_api_route("/api/settings/integrations", self._update_integrations, methods=["POST"])
        router.add_api_route("/api/settings/bridges", self._update_bridges, methods=["POST"])
        router.add_api_route("/api/settings/password", self._update_password, methods=["POST"])
        router.add_api_route("/api/settings/whatsapp", self._update_whatsapp, methods=["POST"])
        router.add_api_route("/api/settings/sharing", self._update_sharing, methods=["POST"])
        router.add_api_route("/api/settings/startup", self._update_startup, methods=["POST"])
        router.add_api_route("/api/settings/taste-signals", self._list_taste_signals, methods=["GET"])
        router.add_api_route("/api/settings/taste-signals/{signal_id}", self._update_taste_signal, methods=["POST"])
        router.add_api_route("/api/settings/taste-signals/{signal_id}", self._delete_taste_signal, methods=["DELETE"])
        return router

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

    async def _get_settings(self, _auth: bool = Depends(verify_auth)) -> dict:
        """Retrieve the current system settings."""
        categories = []
        if self._deps.category_registry:
            settings = self._deps.settings_manager.settings
            categories = []
            for cat in self._deps.category_registry.list_all():
                manifest = cat.manifest(settings=settings)
                data = manifest.model_dump()
                categories.append(data)
        return {
            "settings": self._deps.settings_manager.settings.model_dump(),
            "categories": categories,
            "config_files": {
                "global_live": str(self._deps.settings_manager.settings_path),
                "global_template": str(self._deps.settings_manager.settings_template_path),
                "categories_dir": str(self._deps.settings_manager.category_config_dir),
                "category_templates_dir": str(self._deps.settings_manager.category_template_dir),
            },
        }


    async def _get_llm_context(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        refresh: bool = False,
        _auth: bool = Depends(verify_auth),
    ) -> dict:
        """Return endpoint-discovered context-window metadata for the selected model.

        The UI uses this to expose a user cap from the minimum selectable window to the endpoint maximum while
        defaulting to the endpoint/model value.  Some providers do not expose a
        context length in their model-list response; those report a conservative
        fallback so the control remains usable but visibly marks the source.
        """
        settings = self._deps.settings_manager.settings
        llm = settings.llm
        provider_id = provider or llm.active_provider
        model_id = model or llm.model
        endpoint_limit = None
        source = "configured fallback"
        endpoint_reported = False
        loaded_context = None
        max_context = None

        if provider_id and model_id and self._deps.llm_manager:
            try:
                models = await self._deps.llm_manager.get_models_for_provider(provider_id, force_refresh=refresh)
                match = next((m for m in models if m.id == model_id or m.id.endswith(f"/{model_id}") or model_id.endswith(f"/{m.id}")), None)
                if match and match.context and match.context.max_context_tokens:
                    endpoint_limit = int(match.context.max_context_tokens)
                    max_context = endpoint_limit
                    source = "provider_model_endpoint"
                    endpoint_reported = True
            except Exception:
                endpoint_limit = None
                endpoint_reported = False

            try:
                preset = self._deps.llm_manager.registry.get_preset(provider_id)
                api_base = llm.api_base or self._deps.llm_manager.registry.get_resolved_api_base(provider_id) or (preset.api_base if preset else None)
                active_key = self._deps.llm_manager.keys.get_active_key(provider_id)
                api_key = llm.api_key or (active_key.key if active_key else None)
                probe = await probe_endpoint_context_limit(
                    base_url=api_base,
                    model_id=model_id,
                    api_key=api_key,
                    provider_id=provider_id,
                    fallback_tokens=FALLBACK_CONTEXT_LIMIT,
                )
                if probe.endpoint_reported:
                    endpoint_limit = int(probe.usable_context_tokens)
                    loaded_context = probe.loaded_context_tokens
                    max_context = probe.max_context_tokens
                    source = probe.source
                    endpoint_reported = True
                elif endpoint_limit is None:
                    endpoint_limit = int(probe.usable_context_tokens)
                    source = probe.source
                    endpoint_reported = False
            except Exception:
                pass

        default_context = int(endpoint_limit or FALLBACK_CONTEXT_LIMIT)
        max_selectable = default_context if endpoint_reported else max(MAX_MANUAL_CONTEXT_LIMIT, default_context)
        min_selectable = min(MIN_USER_CONTEXT_LIMIT, max_selectable)
        configured = llm.max_context_tokens
        selected = default_context if configured is None else min(max(min_selectable, int(configured)), max_selectable)
        return {
            "provider_id": provider_id,
            "model": model_id,
            "endpoint_max_context_tokens": endpoint_limit if endpoint_reported else None,
            "endpoint_context_reported": endpoint_reported,
            "loaded_context_tokens": loaded_context,
            "model_max_context_tokens": max_context,
            "manual_max_context_tokens": MAX_MANUAL_CONTEXT_LIMIT,
            "fallback_context_tokens": FALLBACK_CONTEXT_LIMIT,
            "max_selectable_context_tokens": max_selectable,
            "min_selectable_context_tokens": min_selectable,
            "configured_context_tokens": configured,
            "selected_context_tokens": selected,
            "default_context_tokens": default_context,
            "source": source,
            "note": (
                "Endpoint supplied context metadata; user caps are clamped to that reported maximum."
                if endpoint_reported
                else "Provider did not expose context metadata; the fallback is only the automatic default. Explicit user caps may exceed it for known-capable local endpoints."
            ),
        }

    async def _save_settings(self, request: Request, _auth: bool = Depends(verify_auth)):
        """Save the updated system preferences."""
        body = await request.json()
        default_quality = body.get("default_quality", {})
        if default_quality:
            args = {}
            if "size_limit_mode" in default_quality:
                args["size_limit_mode"] = default_quality["size_limit_mode"]
            if "max_bitrate_kbps" in default_quality:
                args["max_bitrate_kbps"] = default_quality["max_bitrate_kbps"]
            if "max_file_size_mb" in default_quality:
                args["max_file_size_mb"] = default_quality["max_file_size_mb"]
            if "preferred_resolution" in default_quality:
                args["preferred_resolution"] = default_quality["preferred_resolution"]
            if "max_download_speed_kbps" in default_quality:
                args["max_download_speed_kbps"] = default_quality["max_download_speed_kbps"]
            if "max_upload_speed_kbps" in default_quality:
                args["max_upload_speed_kbps"] = default_quality["max_upload_speed_kbps"]
            if "language" in default_quality:
                args["language"] = default_quality["language"]
            await self._execute_action('settings_update_quality', args)
        return {"status": "ok", "message": "Preferences secured!"}

    async def _update_llm(self, request: Request, _auth: bool = Depends(verify_auth)):
        deps = self._deps
        body = await request.json()
        args = {}
        if "model" in body and body["model"]:
            args["model"] = body["model"]
        if "api_base" in body:
            args["api_base"] = body["api_base"] or None
        if "provider" in body and body["provider"]:
            args["provider"] = body["provider"]
        if "api_key" in body:
            args["api_key"] = body["api_key"] or None
        for key in (
            "max_context_tokens",
            "context_budget_percent",
            "reserved_output_tokens",
            "raw_recent_context_percent",
            "max_recent_conversation_turns",
            "auto_compress_context",
            "conversation_summary_max_tokens",
        ):
            if key in body:
                args[key] = body[key]
        await self._execute_action('settings_update_llm', args)
        return {"status": "ok"}

    async def _update_quality_form(
        self,
        size_limit_mode: str = Form("smart"),
        max_bitrate_kbps: Optional[int] = Form(None),
        max_file_size_mb: Optional[int] = Form(None),
        preferred_resolution: str = Form("1080p"),
        max_dl_speed: Optional[int] = Form(None),
        max_ul_speed: Optional[int] = Form(None),
        _auth: bool = Depends(verify_auth),
    ):
        await self._execute_action('settings_update_quality', {
            'size_limit_mode': size_limit_mode,
            'max_bitrate_kbps': max_bitrate_kbps,
            'max_file_size_mb': max_file_size_mb,
            'preferred_resolution': preferred_resolution,
            'max_download_speed_kbps': max_dl_speed,
            'max_upload_speed_kbps': max_ul_speed,
        })
        return RedirectResponse(url="/", status_code=303)

    async def _update_quality_api(self, request: Request, _auth: bool = Depends(verify_auth)):
        deps = self._deps
        body = await request.json()
        args = {}
        if "size_limit_mode" in body:
            args["size_limit_mode"] = body["size_limit_mode"]
        if "max_bitrate_kbps" in body:
            args["max_bitrate_kbps"] = body["max_bitrate_kbps"] or None
        if "max_file_size_mb" in body:
            args["max_file_size_mb"] = body["max_file_size_mb"] or None
        if "preferred_resolution" in body:
            args["preferred_resolution"] = body["preferred_resolution"]
        if "max_download_speed_kbps" in body:
            args["max_download_speed_kbps"] = body["max_download_speed_kbps"] or None
        if "max_upload_speed_kbps" in body:
            args["max_upload_speed_kbps"] = body["max_upload_speed_kbps"] or None
        if "language" in body:
            args["language"] = body["language"]
        data = await self._execute_action('settings_update_quality', args)
        return {"status": "ok", "quality": data.get("quality")}

    async def _update_tokens(
        self,
        discord_token: Optional[str] = Form(None),
        telegram_token: Optional[str] = Form(None),
        _auth: bool = Depends(verify_auth),
    ):
        args = {}
        if discord_token:
            args["discord_token"] = discord_token
        if telegram_token:
            args["telegram_token"] = telegram_token
        await self._execute_action('settings_update_tokens', args)
        return RedirectResponse(url="/", status_code=303)

    async def _update_auto_download(self, request: Request, _auth: bool = Depends(verify_auth)):
        deps = self._deps
        body = await request.json()
        args = {}
        if "auto_download" in body:
            args["auto_download"] = bool(body["auto_download"])
        if "auto_discover" in body:
            args["auto_discover"] = bool(body["auto_discover"])
        return await self._execute_action('settings_update_auto_download', args)

    async def _update_tiers(self, request: Request, _auth: bool = Depends(verify_auth)):
        deps = self._deps
        body = await request.json()
        args = {}
        for tier_key in ("lightweight", "standard", "heavy"):
            if tier_key in body:
                args[tier_key] = body[tier_key]
        await self._execute_action('settings_update_tiers', args)
        return {"status": "ok"}


    async def _update_embeddings(self, request: Request, _auth: bool = Depends(verify_auth)):
        """Update semantic-memory embedding runtime settings."""
        body = await request.json()
        allowed = {
            "enabled", "provider", "builtin_model", "dimension", "cache_dir",
            "auto_download", "warmup_on_startup", "max_model_size_mb",
        }
        args = {key: body[key] for key in allowed if key in body}
        data = await self._execute_action('settings_update_embeddings', args)
        return {"status": "ok", "embeddings": data.get("embeddings")}

    async def _embedding_status(self, _auth: bool = Depends(verify_auth)) -> dict:
        """Return semantic-memory embedding health for settings diagnostics."""
        store = getattr(self._deps, "vector_store", None)
        if not store or not hasattr(store, "health_status"):
            return {"status": "unavailable", "initialized": False}
        health = await store.health_status()
        return {"status": "ok", "health": health}

    async def _embedding_reindex(self, request: Request, _auth: bool = Depends(verify_auth)) -> dict:
        """Rebuild semantic-memory vectors for the active embedding namespace.

        Body may include ``mode``: ``conversations`` (legacy/default),
        ``taste_signals``, or ``all``. This keeps review/edit operations for
        category taste evidence connected to the same maintenance path.
        """
        store = getattr(self._deps, "vector_store", None)
        if not store or not hasattr(store, "reindex_conversations"):
            raise HTTPException(status_code=400, detail="Vector store is not available")
        body = await request.json() if request.headers.get("content-length") not in {None, "0"} else {}
        limit = int(body.get("limit", 10000) or 10000) if isinstance(body, dict) else 10000
        mode = str(body.get("mode", "conversations") if isinstance(body, dict) else "conversations")
        if mode == "all" and hasattr(store, "reindex_all_memory"):
            result = await store.reindex_all_memory(limit=limit)
        elif mode == "taste_signals" and hasattr(store, "reindex_taste_signals"):
            result = await store.reindex_taste_signals(limit=limit)
        elif mode in {"conversations", "conversation_turns", "chat"}:
            result = await store.reindex_conversations(limit=limit)
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported reindex mode: {mode}")
        return {"status": "ok", "result": result}

    async def _list_taste_signals(self, request: Request, _auth: bool = Depends(verify_auth)) -> dict:
        """List reviewable category-scoped taste evidence."""
        if not getattr(self._deps, "db", None) or not getattr(self._deps.db, "system", None):
            raise HTTPException(status_code=400, detail="Database is not available")
        category_id = request.query_params.get("category_id") or None
        signal_type = request.query_params.get("signal_type")
        signal_types = [signal_type] if signal_type else None
        limit = int(request.query_params.get("limit", "200") or 200)
        rows = await self._deps.db.system.list_taste_signals(
            category_id=category_id,
            signal_types=signal_types,
            limit=limit,
        )
        return {"status": "ok", "signals": rows}

    async def _update_taste_signal(self, signal_id: int, request: Request, _auth: bool = Depends(verify_auth)) -> dict:
        """Update review fields for one taste signal."""
        body = await request.json()
        if "confidence" not in body and "weight" not in body:
            raise HTTPException(status_code=400, detail="confidence or weight is required")
        confidence = float(body.get("confidence", 1.0))
        weight = float(body["weight"]) if body.get("weight") is not None else None
        ok = await self._deps.db.system.update_taste_signal_confidence(signal_id, confidence, weight)
        if not ok:
            raise HTTPException(status_code=404, detail="Taste signal not found")
        return {"status": "ok"}

    async def _delete_taste_signal(self, signal_id: int, _auth: bool = Depends(verify_auth)) -> dict:
        """Delete one taste signal during user/operator review."""
        ok = await self._deps.db.system.delete_taste_signal(signal_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Taste signal not found")
        return {"status": "ok"}

    async def _update_library(self, request: Request, _auth: bool = Depends(verify_auth)):
        deps = self._deps
        body = await request.json()
        args = {}
        if "download_dir" in body:
            args["download_dir"] = body["download_dir"]
        if "max_concurrent" in body:
            args["max_concurrent"] = int(body["max_concurrent"])
        if "library_paths" in body:
            args["library_paths"] = body["library_paths"]
        if "category_settings" in body:
            args["category_settings"] = body["category_settings"]
        if "stall_check_interval_minutes" in body:
            args["stall_check_interval_minutes"] = int(body["stall_check_interval_minutes"])
        if "stall_alternative_hours" in body:
            args["stall_alternative_hours"] = float(body["stall_alternative_hours"])
        if "stall_cancel_hours" in body:
            args["stall_cancel_hours"] = float(body["stall_cancel_hours"])
        for field in (
            "stall_health_window_minutes",
            "stall_test_interval_minutes",
            "stall_test_duration_minutes",
            "stall_alternative_cooldown_minutes",
            "stall_min_progress_bytes",
            "stall_idle_rate_bps",
        ):
            if field in body:
                args[field] = body[field]
        await self._execute_action('settings_update_library', args)
        return {"status": "ok"}

    async def _get_bandwidth_data(self, _auth: bool = Depends(verify_auth)):
        deps = self._deps
        return {"schedules": [s.model_dump() for s in deps.settings_manager.settings.bandwidth_schedules]}

    async def _update_bandwidth(self, request: Request, _auth: bool = Depends(verify_auth)):
        body = await request.json()
        args = {}
        if "bandwidth_schedules" in body:
            args["bandwidth_schedules"] = body["bandwidth_schedules"]
        await self._execute_action('settings_update_bandwidth', args)
        return {"status": "ok"}

    async def _update_search(self, request: Request, _auth: bool = Depends(verify_auth)):
        body = await request.json()
        args = {}
        if "jackett_url" in body:
            args["jackett_url"] = body["jackett_url"] or None
        if "jackett_api_key" in body:
            args["jackett_api_key"] = body["jackett_api_key"] or None
        if "direct_scraper_fallback" in body:
            args["direct_scraper_fallback"] = bool(body["direct_scraper_fallback"])
        if "web_search" in body and isinstance(body["web_search"], dict):
            args["web_search"] = body["web_search"]
        await self._execute_action('settings_update_search', args)
        return {"status": "ok"}

    async def _update_integrations(self, request: Request, _auth: bool = Depends(verify_auth)):
        body = await request.json()
        args = {}
        if "tmdb_api_key" in body:
            args["tmdb_api_key"] = body["tmdb_api_key"] or None
        if "trakt_client_id" in body:
            args["trakt_client_id"] = body["trakt_client_id"] or None
        if "plex_url" in body:
            args["plex_url"] = body["plex_url"] or None
        if "plex_token" in body:
            args["plex_token"] = body["plex_token"] or None
        if "opensubtitles_api_key" in body:
            args["opensubtitles_api_key"] = body["opensubtitles_api_key"] or None
        await self._execute_action('settings_update_integrations', args)
        return {"status": "ok"}

    async def _update_bridges(self, request: Request, _auth: bool = Depends(verify_auth)):
        body = await request.json()
        args = {}
        if "discord_token" in body:
            args["discord_token"] = body["discord_token"] or None
        if "discord_channel_id" in body:
            val = body["discord_channel_id"]
            args["discord_channel_id"] = int(val) if val else None
        if "telegram_token" in body:
            args["telegram_token"] = body["telegram_token"] or None
        if "whatsapp_token" in body:
            args["whatsapp_token"] = body["whatsapp_token"] or None
        if "whatsapp_phone_number_id" in body:
            args["whatsapp_phone_number_id"] = body["whatsapp_phone_number_id"] or None
        if "whatsapp_verify_token" in body:
            args["whatsapp_verify_token"] = body["whatsapp_verify_token"] or None
        await self._execute_action('settings_update_bridges', args)
        return {"status": "ok"}

    async def _update_password(self, request: Request, _auth: bool = Depends(verify_auth)):
        body = await request.json()
        new_password = body.get("new_password", "")
        confirm = body.get("confirm", "")
        if not new_password:
            raise HTTPException(status_code=400, detail="Password cannot be empty")
        if new_password != confirm:
            raise HTTPException(status_code=400, detail="Passwords do not match")
        await self._execute_action('settings_update_password', {'new_password': new_password})
        return {"status": "ok"}

    async def _update_whatsapp(self, request: Request, _auth: bool = Depends(verify_auth)):
        body = await request.json()
        args = {}
        if "whatsapp_token" in body:
            args["whatsapp_token"] = body["whatsapp_token"] or None
        if "whatsapp_phone_number_id" in body:
            args["whatsapp_phone_number_id"] = body["whatsapp_phone_number_id"] or None
        if "whatsapp_verify_token" in body:
            args["whatsapp_verify_token"] = body["whatsapp_verify_token"] or None
        await self._execute_action('settings_update_whatsapp', args)
        return {"status": "ok"}


    async def _update_startup(self, request: Request, _auth: bool = Depends(verify_auth)):
        """Update launch-at-login preference and OS entry."""
        body = await request.json()
        return await self._execute_action('settings_update_startup', {'enabled': bool(body.get('enabled'))})

    async def _update_sharing(self, request: Request, _auth: bool = Depends(verify_auth)):
        """Update seed-in-place library sharing preferences."""
        body = await request.json()
        allowed = {
            "enabled", "mode", "library_upload_speed_kbps", "active_seed_slots",
            "seed_ratio_target", "seed_duration_hours", "pause_when_downloading",
            "category_overrides",
        }
        args = {key: body[key] for key in allowed if key in body}
        return await self._execute_action('settings_update_sharing', args)
