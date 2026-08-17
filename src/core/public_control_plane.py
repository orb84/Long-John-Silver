"""Protocol-neutral, bounded public read/control services for LJS adapters."""

from __future__ import annotations

from typing import Any

from src.core.domain_models.enums import ActionSource
from src.core.library_objects import CanonicalLibraryObjectBuilder
from src.core.models import ActionCommand, InvocationCapability, InvocationPrincipal
from src.core.invocation import InvocationCapabilityGuard


class PublicStatusService:
    """Expose a small application-health summary without UI coupling."""

    def __init__(self, storage_monitor: Any | None = None) -> None:
        self._storage_monitor = storage_monitor

    def get(self, principal: InvocationPrincipal) -> dict[str, Any]:
        """Return bounded application status for an authorized principal."""
        InvocationCapabilityGuard.require(principal, InvocationCapability.STATUS_READ)
        storage = {"ok": True, "warnings": [], "critical": []}
        if self._storage_monitor is not None:
            try:
                report = self._storage_monitor.build_report()
                storage = {
                    "ok": bool(report.ok),
                    "warnings": list(report.warnings or []),
                    "critical": list(report.critical or []),
                }
            except Exception:
                storage = {
                    "ok": False,
                    "warnings": [],
                    "critical": ["Storage status is temporarily unavailable. See diagnostics for details."],
                }
        return {"status": "ok" if storage["ok"] else "degraded", "storage": storage}


class PublicLibraryService:
    """Expose bounded canonical library summaries and exact item lookup."""

    def __init__(
        self,
        *,
        settings_manager: Any,
        database: Any,
        downloader: Any,
        category_registry: Any | None = None,
    ) -> None:
        self._settings_manager = settings_manager
        self._database = database
        self._downloader = downloader
        self._builder = CanonicalLibraryObjectBuilder(database, category_registry)

    async def list_items(
        self,
        principal: InvocationPrincipal,
        *,
        offset: int = 0,
        limit: int = 25,
    ) -> dict[str, Any]:
        """Return one bounded page of category-neutral tracked-item summaries."""
        InvocationCapabilityGuard.require(principal, InvocationCapability.LIBRARY_READ)
        tracked = [item for item in self._settings_manager.settings.tracked_items if item.enabled]
        start = max(0, int(offset))
        page_size = max(1, min(int(limit), 100))
        selected = tracked[start:start + page_size]
        active = await self._downloader.get_active_downloads()
        canonical = await self._builder.build_many(selected, active_downloads=active)
        return {
            "offset": start,
            "limit": page_size,
            "total": len(tracked),
            "items": [self._summary(row) for row in canonical],
        }

    async def get_item(
        self,
        principal: InvocationPrincipal,
        *,
        category_id: str,
        item_id: str,
    ) -> dict[str, Any] | None:
        """Return one canonical object selected by stable category/item identity."""
        InvocationCapabilityGuard.require(principal, InvocationCapability.LIBRARY_READ)
        category_key = str(category_id or "").strip()
        item_key = str(item_id or "").strip()
        settings_item = next(
            (
                item
                for item in self._settings_manager.settings.tracked_items
                if str(getattr(item, "key", "") or "") == item_key
                and str(getattr(item, "category_id", getattr(item, "item_type", "media")) or "media") == category_key
            ),
            None,
        )
        row = await self._builder.build(
            category_key,
            item_key,
            settings_item=settings_item,
            active_downloads=await self._downloader.get_active_downloads(),
        )
        if settings_item is None and str(row.get("status") or "") == "configured":
            repository_item = await self._database.media.get_category_item(category_key, item_key)
            if repository_item is None:
                return None
        return PublicLibraryRedactor.redact(row)

    @staticmethod
    def _summary(row: dict[str, Any]) -> dict[str, Any]:
        """Strip potentially large child envelopes from library list results."""
        return {
            "category_id": row.get("category_id"),
            "item_id": row.get("item_id"),
            "display_name": row.get("display_name"),
            "status": row.get("status"),
            "computed": row.get("computed") or {},
            "state": row.get("state") or {},
        }


class PublicLibraryRedactor:
    """Remove host-local path evidence from externally readable canonical objects."""

    _SENSITIVE_PATH_KEYS = frozenset({
        "path",
        "file_path",
        "local_path",
        "absolute_path",
        "source_path",
        "target_path",
        "download_path",
        "library_path",
        "library_root",
        "download_root",
    })

    @classmethod
    def redact(cls, value: Any) -> Any:
        """Return a recursively redacted copy without mutating canonical state."""
        if isinstance(value, dict):
            return {
                key: cls.redact(child)
                for key, child in value.items()
                if str(key).casefold() not in cls._SENSITIVE_PATH_KEYS
            }
        if isinstance(value, list):
            return [cls.redact(child) for child in value]
        if isinstance(value, tuple):
            return [cls.redact(child) for child in value]
        return value


class PublicDownloadService:
    """Expose bounded active-download state without search/orchestration semantics."""

    def __init__(self, downloader: Any) -> None:
        self._downloader = downloader

    async def list_active(self, principal: InvocationPrincipal, *, limit: int = 100) -> dict[str, Any]:
        """Return active download summaries without magnets or private paths."""
        InvocationCapabilityGuard.require(principal, InvocationCapability.DOWNLOADS_READ)
        rows = await self._downloader.get_active_downloads()
        bounded = rows[:max(1, min(int(limit), 200))]
        return {"active": [self._summary(row) for row in bounded], "total": len(rows)}

    @staticmethod
    def _summary(row: Any) -> dict[str, Any]:
        status = getattr(row, "status", "")
        priority = getattr(row, "priority", "")
        return {
            "id": getattr(row, "id", ""),
            "category_id": getattr(row, "category_id", ""),
            "item_id": getattr(row, "item_id", ""),
            "item_name": getattr(row, "item_name", ""),
            "status": getattr(status, "value", status),
            "priority": getattr(priority, "value", priority),
            "progress": getattr(row, "progress", 0.0),
            "download_rate": getattr(row, "download_rate", 0.0),
            "eta_seconds": getattr(row, "eta_seconds", 0.0),
            "reason": getattr(row, "reason", ""),
        }


class PublicLLMConfigurationService:
    """Read, test, and mutate LJS LLM routing through canonical authorities."""

    def __init__(self, *, settings_manager: Any, assistant: Any, llm_manager: Any, action_gateway: Any) -> None:
        self._settings_manager = settings_manager
        self._assistant = assistant
        self._llm_manager = llm_manager
        self._action_gateway = action_gateway

    def get(self, principal: InvocationPrincipal) -> dict[str, Any]:
        """Return configured base route plus effective runtime routes without secrets."""
        InvocationCapabilityGuard.require(principal, InvocationCapability.CONFIG_LLM_READ)
        llm = self._settings_manager.settings.llm
        return {
            "configured_base": {
                "provider": llm.active_provider,
                "model": llm.model,
                "api_base": llm.api_base,
                "max_context_tokens": llm.max_context_tokens,
                "context_budget_percent": llm.context_budget_percent,
                "reserved_output_tokens": llm.reserved_output_tokens,
            },
            **self._assistant.llm_route_summary(),
        }

    async def test(self, principal: InvocationPrincipal) -> dict[str, Any]:
        """Probe the configured provider catalog and verify the selected model is visible."""
        InvocationCapabilityGuard.require(principal, InvocationCapability.CONFIG_LLM_PROBE)
        llm = self._settings_manager.settings.llm
        models = await self._llm_manager.get_models_for_provider(llm.active_provider, force_refresh=True)
        selected = next(
            (
                model
                for model in models
                if model.id == llm.model or model.id.endswith(f"/{llm.model}") or llm.model.endswith(f"/{model.id}")
            ),
            None,
        )
        return {
            "ok": bool(models),
            "provider": llm.active_provider,
            "configured_model": llm.model,
            "configured_model_visible": selected is not None,
            "model_count": len(models),
        }

    async def set(
        self,
        principal: InvocationPrincipal,
        *,
        values: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Apply an LLM update through ActionGateway and return durable receipt truth."""
        InvocationCapabilityGuard.require(principal, InvocationCapability.CONFIG_LLM_WRITE)
        arguments = self._sanitize_values(values)
        if not arguments:
            raise ValueError("No supported LLM configuration fields were supplied")
        if self._contains_endpoint_change(arguments):
            InvocationCapabilityGuard.require(principal, InvocationCapability.CONFIG_LLM_ENDPOINT_WRITE)
        result = await self._action_gateway.execute(
            ActionCommand(
                name="settings_update_llm",
                arguments=arguments,
                source=ActionSource.EXTERNAL,
                user_id=principal.user_id,
                actor=principal.principal_id,
                idempotency_key=idempotency_key,
            )
        )
        return {
            "ok": result.ok,
            "status": result.status,
            "data": result.data,
            "error": result.error,
            "command_id": result.command_id,
            "correlation_id": result.correlation_id,
            "replayed": result.replayed,
            "receipt_persisted": result.receipt_persisted,
        }

    @staticmethod
    def _contains_endpoint_change(values: dict[str, Any]) -> bool:
        """Return whether a public LLM mutation includes a custom endpoint field."""
        if "api_base" in values:
            return True
        tiers = values.get("tiers")
        return isinstance(tiers, dict) and any(
            isinstance(tier, dict) and "api_base" in tier
            for tier in tiers.values()
        )

    @staticmethod
    def _sanitize_values(values: dict[str, Any]) -> dict[str, Any]:
        """Keep only public mutable fields and strip credential-bearing tier values."""
        allowed = {
            "model",
            "api_base",
            "provider",
            "max_context_tokens",
            "context_budget_percent",
            "reserved_output_tokens",
            "raw_recent_context_percent",
            "max_recent_conversation_turns",
            "auto_compress_context",
            "conversation_summary_max_tokens",
            "tiers",
            "apply_base_to_all",
        }
        arguments = {key: value for key, value in dict(values or {}).items() if key in allowed}
        tiers = arguments.get("tiers")
        if isinstance(tiers, dict):
            safe_tiers: dict[str, Any] = {}
            allowed_tier_fields = {
                "model",
                "api_base",
                "max_tokens",
                "temperature",
                "provider",
                "max_context_tokens",
            }
            allowed_tier_names = {"lightweight", "standard", "heavy"}
            for tier_name, tier_values in tiers.items():
                normalized_tier_name = str(tier_name)
                if normalized_tier_name not in allowed_tier_names or not isinstance(tier_values, dict):
                    continue
                safe_tiers[normalized_tier_name] = {
                    key: value
                    for key, value in tier_values.items()
                    if key in allowed_tier_fields
                }
            arguments["tiers"] = safe_tiers
        return arguments


class PublicDiagnosticsService:
    """Expose bounded secret-redacted diagnostics suitable for external agents."""

    def __init__(self, llm_activity_monitor: Any | None = None) -> None:
        self._monitor = llm_activity_monitor

    def recent(self, principal: InvocationPrincipal, *, limit: int = 20) -> dict[str, Any]:
        """Return recent LLM activity without exact prompt/tool context."""
        InvocationCapabilityGuard.require(principal, InvocationCapability.DIAGNOSTICS_READ)
        if self._monitor is None:
            return {"ok": True, "active_count": 0, "active": [], "last_call": None, "calls": [], "events": []}
        return self._monitor.snapshot(limit=max(1, min(int(limit), 40)), include_context=False)
