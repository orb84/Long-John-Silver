"""Canonical LLM settings mutation with route-owned credential semantics."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Protocol

from src.llm_providers.context_limits import MIN_USER_CONTEXT_LIMIT


class LLMSettingsRuntime(Protocol):
    """Runtime surface required after persisting LLM configuration."""

    def update_settings(self, settings: Any) -> None:
        """Reload one persisted settings snapshot into the live LLM runtime."""
        ...

    def llm_route_summary(self) -> dict[str, Any]:
        """Return the live effective route summary after a successful reload."""
        ...


class LLMSettingsStore(Protocol):
    """Settings persistence surface used by the mutation service."""

    @property
    def settings(self) -> Any:
        """Return the currently persisted application settings snapshot."""
        ...

    def save(self, settings: Any) -> None:
        """Persist one complete application settings snapshot."""
        ...


class LLMSettingsMutationService:
    """Apply one authoritative LLM route change with rollback-safe semantics.

    The service preserves the identity of the live ``Settings`` and ``LLMConfig``
    objects because other LJS collaborators may legitimately retain references
    to them.  It computes changes on a detached candidate, then copies the
    candidate into the live LLM config only at the commit boundary.

    Provider credentials belong to the provider/endpoint route on which they
    were configured. Switching either route component clears an inherited
    credential unless the caller explicitly supplies a replacement.
    """

    def __init__(self, settings_store: LLMSettingsStore, runtime: LLMSettingsRuntime, provider_manager: Any) -> None:
        self._store = settings_store
        self._runtime = runtime
        self._providers = provider_manager

    async def update(self, **values: Any) -> dict[str, Any]:
        """Apply one LLM route change atomically across persistence/runtime state."""
        settings = self._store.settings
        live_llm = settings.llm
        previous = live_llm.model_copy(deep=True)
        candidate = previous.model_copy(deep=True)

        self._apply_route(candidate, values)
        self._apply_budget(candidate, values)
        tiers = values.get("tiers") if isinstance(values.get("tiers"), dict) else {}
        self._apply_tiers(candidate, tiers)
        if bool(values.get("apply_base_to_all")):
            candidate.clear_route_overrides()

        previous_registry_provider = self._registry_active_provider()
        self._copy_llm_state(live_llm, candidate)
        try:
            self._store.save(settings)
        except Exception as persistence_error:
            self._copy_llm_state(live_llm, previous)
            self._restore_registry(previous_registry_provider)
            self._rollback_persistence(settings, persistence_error)
            raise

        try:
            self._sync_registry_provider(candidate.active_provider)
            self._runtime.update_settings(settings)
            route_summary = self._runtime.llm_route_summary()
        except Exception as runtime_error:
            self._copy_llm_state(live_llm, previous)
            self._restore_registry(previous_registry_provider)
            self._rollback(settings, runtime_error)
            raise

        return {
            "status": "ok",
            "apply_base_to_all": bool(values.get("apply_base_to_all")),
            **route_summary,
        }

    def _rollback_persistence(self, settings: Any, persistence_error: Exception) -> None:
        """Restore the previous in-memory state to disk after a partial save failure."""
        try:
            self._store.save(settings)
        except Exception as rollback_error:
            raise RuntimeError(
                f"LLM settings persistence failed and rollback was incomplete: {rollback_error}"
            ) from persistence_error

    def _rollback(self, settings: Any, runtime_error: Exception) -> None:
        """Restore persistence and runtime after route activation/reload failure."""
        errors: list[str] = []
        try:
            self._store.save(settings)
        except Exception as exc:
            errors.append(f"settings rollback failed: {exc}")
        try:
            self._runtime.update_settings(settings)
        except Exception as exc:
            errors.append(f"runtime rollback failed: {exc}")
        if errors:
            raise RuntimeError(
                "LLM runtime reload failed and rollback was incomplete: " + "; ".join(errors)
            ) from runtime_error

    def _apply_route(self, llm: Any, values: dict[str, Any]) -> None:
        if values.get("model"):
            llm.model = values["model"]

        previous_provider = str(getattr(llm, "active_provider", "") or "")
        previous_base = getattr(llm, "api_base", None)
        requested_provider = str(values.get("provider") or "").strip()
        provider_changed = bool(requested_provider and requested_provider != previous_provider)
        if requested_provider:
            llm.active_provider = requested_provider

        if "api_base" in values:
            requested_base = values["api_base"] or None
            base_changed = requested_base != previous_base
            llm.api_base = requested_base
        elif provider_changed:
            llm.api_base = self._provider_default_base(requested_provider)
            base_changed = True
        else:
            base_changed = False

        if "api_key" in values:
            llm.api_key = values["api_key"] or None
        elif provider_changed or base_changed:
            llm.api_key = None

    def _apply_tiers(self, llm: Any, tiers: dict[str, Any]) -> None:
        scalar_fields = ("model", "max_tokens", "temperature", "max_context_tokens")
        for tier_key in ("lightweight", "standard", "heavy"):
            tier_data = tiers.get(tier_key)
            if not isinstance(tier_data, dict):
                continue
            existing = getattr(llm, tier_key)
            previous_provider = str(getattr(existing, "provider", "") or "")
            previous_base = getattr(existing, "api_base", None)
            requested_provider = str(tier_data.get("provider") or "").strip() if "provider" in tier_data else ""
            provider_changed = "provider" in tier_data and requested_provider != previous_provider

            for field_name in scalar_fields:
                if field_name in tier_data:
                    value = tier_data[field_name]
                    setattr(existing, field_name, value if value != "" else None)

            if "provider" in tier_data:
                existing.provider = requested_provider or None

            if "api_base" in tier_data:
                requested_base = tier_data["api_base"] or None
                base_changed = requested_base != previous_base
                existing.api_base = requested_base
            elif provider_changed and requested_provider:
                existing.api_base = self._provider_default_base(requested_provider)
                base_changed = True
            elif provider_changed:
                existing.api_base = None
                base_changed = True
            else:
                base_changed = False

            if "api_key" in tier_data:
                existing.api_key = tier_data["api_key"] or None
            elif provider_changed or base_changed:
                existing.api_key = None

    def _provider_default_base(self, provider_id: str) -> str | None:
        if not provider_id:
            return None
        resolved = self._providers.registry.get_resolved_api_base(provider_id)
        if resolved:
            return resolved
        preset = self._providers.registry.get_preset(provider_id)
        return preset.api_base if preset else None

    def _registry_active_provider(self) -> str | None:
        getter = getattr(self._providers.registry, "get_active_provider_id", None)
        if not callable(getter):
            return None
        value = getter()
        return str(value) if value else None

    def _sync_registry_provider(self, provider_id: str) -> None:
        """Keep the provider-library fallback authority aligned when possible."""
        if not provider_id:
            return
        preset_getter = getattr(self._providers.registry, "get_preset", None)
        setter = getattr(self._providers.registry, "set_active_provider", None)
        if not callable(setter):
            return
        if callable(preset_getter) and preset_getter(provider_id) is None:
            # Some dependency-light tests use a deliberately incomplete registry;
            # runtime route authority still comes from Settings in that case.
            return
        setter(provider_id)

    def _restore_registry(self, provider_id: str | None) -> None:
        """Restore provider-library fallback state during transaction rollback."""
        if not provider_id:
            return
        setter = getattr(self._providers.registry, "set_active_provider", None)
        if callable(setter):
            setter(provider_id)

    @staticmethod
    def _copy_llm_state(target: Any, source: Any) -> None:
        """Copy a complete LLMConfig while preserving the target object's identity."""
        field_names = getattr(type(source), "model_fields", {})
        for field_name in field_names:
            setattr(target, field_name, deepcopy(getattr(source, field_name)))

    @staticmethod
    def _apply_budget(llm: Any, values: dict[str, Any]) -> None:
        if "max_context_tokens" in values:
            value = values["max_context_tokens"]
            llm.max_context_tokens = None if value is None else max(MIN_USER_CONTEXT_LIMIT, int(value))
        if "context_budget_percent" in values and values["context_budget_percent"] is not None:
            llm.context_budget_percent = max(20, min(100, int(values["context_budget_percent"])))
        if "reserved_output_tokens" in values:
            value = values["reserved_output_tokens"]
            llm.reserved_output_tokens = None if value is None else max(0, int(value))
        if "raw_recent_context_percent" in values and values["raw_recent_context_percent"] is not None:
            llm.raw_recent_context_percent = max(0, min(100, int(values["raw_recent_context_percent"])))
        if "max_recent_conversation_turns" in values and values["max_recent_conversation_turns"] is not None:
            llm.max_recent_conversation_turns = max(0, int(values["max_recent_conversation_turns"]))
        if "auto_compress_context" in values:
            llm.auto_compress_context = bool(values["auto_compress_context"])
        if "conversation_summary_max_tokens" in values and values["conversation_summary_max_tokens"] is not None:
            llm.conversation_summary_max_tokens = max(0, int(values["conversation_summary_max_tokens"]))
