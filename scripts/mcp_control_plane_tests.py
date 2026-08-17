"""Dependency-light acceptance checks for the LJS public MCP control-plane slice."""

from __future__ import annotations

import asyncio
import importlib
import os
import sqlite3
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ai.agent_delegation import AgentDelegationDenied, AgentDelegationService
from src.ai.agent_delegation_admission import AgentDelegationAdmissionGate
from src.ai.chat_session_runner import ChatTurnOutcome
from src.ai.chat_turn_registry import ChatTurnRegistry
from src.ai.tool_result_evidence import ToolResultEvidenceCollector
from src.ai.tool_context import ToolExecutionContextFactory
from src.ai.tool_registry import ToolRegistry
from src.ai.category_tool_factory import CategoryToolFactory
from src.core.conversation_handle import ConversationHandleAccessError, ConversationHandleLimitError, ConversationHandleService
from src.core.categories.tv import TvShowCategory
from src.core.categories.movie import MovieCategory
from src.core.categories.registry import CategoryRegistry
from src.core.categories.base import CategoryWorkflowContext
from src.core.public_control_plane import PublicLLMConfigurationService, PublicLibraryRedactor, PublicStatusService
from src.core.models import AgentDelegationStatus, InvocationCapability, InvocationContext, InvocationEvidence, InvocationPrincipal, Settings
from src.integrations.mcp_auth import MCPAuthenticationBoundary, MCPAuthenticationError, MCPPrincipalResolver, MCPRequestPrincipalContext
from src.integrations.mcp_configuration import MCPIntegrationSettings
from src.integrations.mcp_network import LocalMCPNetworkBoundary
from src.web.action_handlers.settings import SettingsActionHandler
from src.web.action_handlers.providers import ProvidersActionHandler
from src.web.action_handlers.setup import SetupActionHandler
from src.llm_providers.credential_policy import ProviderCredentialPolicy


class _Users:
    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, object]] = {}
        self.users: dict[str, dict[str, object]] = {"local": {"id": "local", "username": "local"}}

    async def get_user_by_id(self, user_id: str) -> dict[str, object] | None:
        return self.users.get(user_id)

    async def ensure_session(self, session_id: str, **kwargs: object) -> dict[str, object]:
        row = {"id": session_id, **kwargs}
        row["user_id"] = str(row.get("user_id") or "local")
        self.sessions[session_id] = row
        return row

    async def get_user_by_username(self, username: str) -> dict[str, object] | None:
        return {"id": "user-1", "username": username} if username == "alice" else None

    async def delete_session(self, session_id: str) -> bool:
        return self.sessions.pop(session_id, None) is not None


class _Handles:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, object]] = {}

    async def create(self, **row: object) -> dict[str, object]:
        now = datetime.now(timezone.utc).isoformat()
        stored = {**row, "created_at": now, "last_active_at": now, "revoked_at": None}
        self.rows[str(row["handle_id"])] = stored
        return stored

    async def get(self, handle_id: str) -> dict[str, object] | None:
        row = self.rows.get(handle_id)
        return dict(row) if row and row.get("revoked_at") is None else None

    async def touch(self, handle_id: str) -> None:
        if handle_id in self.rows:
            self.rows[handle_id]["last_active_at"] = datetime.now(timezone.utc).isoformat()

    async def count_active_for_principal(self, principal_id: str, client_id: str) -> int:
        return sum(
            1 for row in self.rows.values()
            if row.get("principal_id") == principal_id
            and row.get("client_id") == client_id
            and row.get("revoked_at") is None
        )

    async def list_expired(self, cutoff_iso: str) -> list[dict[str, object]]:
        return [
            dict(row) for row in self.rows.values()
            if row.get("revoked_at") is None and str(row.get("last_active_at") or "") < cutoff_iso
        ]

    async def revoke_expired(self, cutoff_iso: str) -> int:
        changed = 0
        for row in self.rows.values():
            if row.get("revoked_at") is None and str(row.get("last_active_at") or "") < cutoff_iso:
                row["revoked_at"] = datetime.now(timezone.utc).isoformat()
                changed += 1
        return changed

    async def revoke(self, handle_id: str) -> bool:
        row = self.rows.get(handle_id)
        if not row or row.get("revoked_at") is not None:
            return False
        row["revoked_at"] = datetime.now(timezone.utc).isoformat()
        return True


class _Database:
    def __init__(self) -> None:
        self.users = _Users()
        self.conversation_handles = _Handles()


class _Auth:
    def verify_token(self, token: str) -> str | None:
        return "alice" if token == "web-jwt" else None


class _Runner:
    def __init__(self, message: str = "done") -> None:
        self.message = message
        self.requests: list[object] = []

    async def collect_outcome(self, request: object) -> ChatTurnOutcome:
        self.requests.append(request)
        await asyncio.sleep(0)
        return ChatTurnOutcome(status="complete", message=self.message, turn_id=getattr(request, "turn_id", None))


class _FailingRunner:
    async def collect_outcome(self, request: object) -> ChatTurnOutcome:
        raise RuntimeError("synthetic private failure details")


class _BlockingRunner:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def collect_outcome(self, request: object) -> ChatTurnOutcome:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        raise AssertionError("unreachable")


class _MCPControlPlaneAcceptance:
    @classmethod
    async def run(cls) -> None:
        cls._migration_syntax()
        cls._settings_contract()
        await cls._llm_configuration_contract()
        await cls._llm_route_ownership_and_atomicity_contract()
        await cls._tool_capability_contract()
        cls._category_capability_contract()
        await cls._category_conditional_delete_contract()
        cls._category_destructive_policy_contract()
        await cls._authentication_contract()
        await cls._authentication_boundary_contract()
        await cls._conversation_handle_contract()
        await cls._delegation_contract()
        await cls._delegation_failure_contract()
        await cls._admission_contract()
        await cls._cancellation_contract()
        await cls._conversation_close_contract()
        await cls._definition_backed_hidden_write_contract()
        cls._evidence_contract()
        cls._redaction_contract()
        cls._status_error_redaction_contract()
        await cls._network_boundary_contract()
        cls._adapter_surface_contract()

    @staticmethod
    def _migration_syntax() -> None:
        sql = Path("migrations/114_external_conversation_handles.sql").read_text()
        db = sqlite3.connect(":memory:")
        try:
            db.executescript(sql)
            columns = {row[1] for row in db.execute("PRAGMA table_info(external_conversation_handles)")}
            required = {"handle_id", "internal_session_id", "principal_id", "client_id", "revoked_at"}
            assert required.issubset(columns)
        finally:
            db.close()

    @staticmethod
    def _settings_contract() -> None:
        old = dict(os.environ)
        try:
            os.environ["LJS_MCP_ENABLED"] = "1"
            os.environ.pop("LJS_MCP_CAPABILITIES", None)
            os.environ["LJS_MCP_TOKEN"] = "x" * 32
            settings = MCPIntegrationSettings.from_environment()
            assert settings.enabled is True
            assert InvocationCapability.AGENT_DELEGATE in settings.capabilities
            assert InvocationCapability.CONFIG_LLM_WRITE not in settings.capabilities
            assert InvocationCapability.CONFIG_LLM_ENDPOINT_WRITE not in settings.capabilities
            assert settings.user_id == "local"
            os.environ.pop("LJS_MCP_TOKEN", None)
            try:
                MCPIntegrationSettings.from_environment()
            except ValueError:
                pass
            else:
                raise AssertionError("Enabled MCP without a dedicated token must fail closed")
            os.environ["LJS_MCP_TOKEN"] = "x" * 32
            os.environ["LJS_MCP_CAPABILITIES"] = "not.a.capability"
            try:
                MCPIntegrationSettings.from_environment()
            except ValueError:
                pass
            else:
                raise AssertionError("Unknown MCP capability must fail closed")
            os.environ.pop("LJS_MCP_CAPABILITIES", None)
            os.environ["LJS_MCP_TOKEN"] = "too-short"
            try:
                MCPIntegrationSettings.from_environment()
            except ValueError:
                pass
            else:
                raise AssertionError("Weak dedicated MCP tokens must fail closed")

            # Disabled MCP must remain inert even if stale MCP-only configuration is invalid.
            os.environ["LJS_MCP_ENABLED"] = "0"
            os.environ["LJS_MCP_CAPABILITIES"] = "stale.invalid.capability"
            disabled = MCPIntegrationSettings.from_environment()
            assert disabled.enabled is False
        finally:
            os.environ.clear()
            os.environ.update(old)

    @staticmethod
    async def _llm_configuration_contract() -> None:
        captured: list[object] = []

        class _Gateway:
            async def execute(self, command: object) -> object:
                captured.append(command)
                return SimpleNamespace(
                    ok=True,
                    status="ok",
                    data={"status": "ok"},
                    error=None,
                    command_id="cmd-llm",
                    correlation_id="corr-llm",
                    replayed=False,
                    receipt_persisted=True,
                )

        service = PublicLLMConfigurationService(
            settings_manager=SimpleNamespace(),
            assistant=SimpleNamespace(),
            llm_manager=SimpleNamespace(),
            action_gateway=_Gateway(),
        )
        principal = InvocationPrincipal(
            principal_id="llm-writer",
            client_id="client",
            source="mcp",
            capabilities={InvocationCapability.CONFIG_LLM_WRITE},
        )
        result = await service.set(
            principal,
            values={
                "provider": "example",
                "api_key": "top-secret",
                "tiers": {
                    "lightweight": {
                        "model": "small",
                        "api_key": "nested-secret",
                        "unexpected": "drop-me",
                    },
                    "unknown-tier": {"model": "must-drop"},
                },
            },
        )
        assert result["receipt_persisted"] is True
        command = captured[0]
        arguments = getattr(command, "arguments")
        assert "api_key" not in arguments
        assert set(arguments["tiers"]) == {"lightweight"}
        assert arguments["tiers"]["lightweight"] == {"model": "small"}

        read_only_probe_principal = principal.model_copy(update={
            "capabilities": {InvocationCapability.CONFIG_LLM_READ}
        })
        try:
            await service.test(read_only_probe_principal)
        except PermissionError as exc:
            assert "config.llm.probe" in str(exc)
        else:
            raise AssertionError("Provider probing must require config.llm.probe, not read access")

        try:
            await service.set(principal, values={"api_base": "http://127.0.0.1:9999/v1"})
        except PermissionError as exc:
            assert "config.llm.endpoint.write" in str(exc)
        else:
            raise AssertionError("LLM endpoint mutation must require its dedicated capability")

        endpoint_principal = principal.model_copy(update={
            "capabilities": {
                InvocationCapability.CONFIG_LLM_WRITE,
                InvocationCapability.CONFIG_LLM_ENDPOINT_WRITE,
            }
        })
        endpoint_result = await service.set(
            endpoint_principal, values={"api_base": "http://127.0.0.1:9999/v1"}
        )
        assert endpoint_result["receipt_persisted"] is True
        assert getattr(captured[-1], "arguments")["api_base"] == "http://127.0.0.1:9999/v1"

    @staticmethod
    async def _llm_route_ownership_and_atomicity_contract() -> None:
        class _Registry:
            _bases = {
                "provider-a": "https://a.example/v1",
                "provider-b": "https://b.example/v1",
            }

            def __init__(self) -> None:
                self.active_provider = "provider-a"

            def get_resolved_api_base(self, provider_id: str) -> str | None:
                return self._bases.get(provider_id)

            def get_preset(self, provider_id: str) -> object | None:
                base = self._bases.get(provider_id)
                return SimpleNamespace(api_base=base) if base else None

            def get_active_provider_id(self) -> str:
                return self.active_provider

            def set_active_provider(self, provider_id: str) -> None:
                self.active_provider = provider_id

        class _SettingsManager:
            def __init__(self, fail_saves: int = 0) -> None:
                self.settings = Settings()
                self.saved: list[Settings] = []
                self.fail_saves = fail_saves

            def save(self, settings: Settings) -> None:
                self.settings = settings
                self.saved.append(settings.model_copy(deep=True))
                if self.fail_saves:
                    self.fail_saves -= 1
                    raise RuntimeError("synthetic persistence failure after partial apply")

        class _Assistant:
            def __init__(self, fail_updates: int = 0) -> None:
                self.fail_updates = fail_updates
                self.settings: Settings | None = None

            def update_settings(self, settings: Settings) -> None:
                if self.fail_updates:
                    self.fail_updates -= 1
                    raise RuntimeError("synthetic runtime reload failure")
                self.settings = settings.model_copy(deep=True)

            def llm_route_summary(self) -> dict[str, object]:
                return {"config_revision": 1, "routes": []}

        manager = SimpleNamespace(registry=_Registry())
        assert ProviderCredentialPolicy.is_provider_owned_endpoint(manager.registry, "provider-a", "https://a.example/v1") is True
        assert ProviderCredentialPolicy.is_provider_owned_endpoint(manager.registry, "provider-a", "https://evil.example/v1") is False
        manager.registry.get_resolved_api_base = lambda provider_id: "https://operator-override.example/v1"  # type: ignore[method-assign]
        assert ProviderCredentialPolicy.is_provider_owned_endpoint(
            manager.registry, "provider-a", "https://operator-override.example/v1"
        ) is False, "Registry endpoint overrides must not auto-inherit provider secrets"
        manager = SimpleNamespace(registry=_Registry())
        settings_manager = _SettingsManager()
        settings_manager.settings.llm.active_provider = "provider-a"
        settings_manager.settings.llm.api_base = "https://a.example/v1"
        settings_manager.settings.llm.api_key = "SECRET_A"
        handler = SettingsActionHandler(
            settings_manager, _Assistant(), SimpleNamespace(), SimpleNamespace(), manager
        )
        await handler.update_llm(provider="provider-b", model="model-b")
        llm = settings_manager.settings.llm
        assert llm.active_provider == "provider-b"
        assert llm.api_base == "https://b.example/v1"
        assert llm.api_key is None, "Provider transitions must not carry another provider's secret"

        llm.api_key = "SECRET_B"
        await handler.update_llm(api_base="http://127.0.0.1:9999/v1")
        assert settings_manager.settings.llm.api_key is None, "Custom endpoint changes must clear inherited secrets"

        # Normal provider activation must use the same route-owned secret semantics;
        # it must not retain a previous provider's settings-level credential.
        provider_registry = _Registry()
        provider_manager = SimpleNamespace(
            registry=provider_registry,
            keys=SimpleNamespace(),
        )
        provider_settings = _SettingsManager()
        provider_settings.settings.llm.active_provider = "provider-a"
        provider_settings.settings.llm.api_base = "https://a.example/v1"
        provider_settings.settings.llm.api_key = "SECRET_A"
        provider_handler = ProvidersActionHandler(provider_manager, provider_settings, _Assistant())  # type: ignore[arg-type]
        await provider_handler.activate("provider-b")
        assert provider_settings.settings.llm.active_provider == "provider-b"
        assert provider_settings.settings.llm.api_base == "https://b.example/v1"
        assert provider_settings.settings.llm.api_key is None
        assert provider_registry.active_provider == "provider-b"

        # First-run custom-endpoint credentials belong to that explicit route and
        # must not be copied into the canonical provider KeyStore.
        class _Keys:
            def __init__(self) -> None:
                self.added: list[tuple[object, ...]] = []

            def add_key(self, *args: object, **kwargs: object) -> object:
                self.added.append((*args, kwargs))
                return SimpleNamespace(id="key", label="setup", is_active=True)

        setup_keys = _Keys()
        setup_registry = _Registry()
        setup_manager = SimpleNamespace(registry=setup_registry, keys=setup_keys)
        setup_settings = _SettingsManager()
        setup_handler = SetupActionHandler(
            setup_settings, SimpleNamespace(), setup_manager, _Assistant()  # type: ignore[arg-type]
        )
        await setup_handler.setup_llm(
            provider="provider-a", model="model-a",
            api_base="https://custom.example/v1", api_key="CUSTOM_ENDPOINT_SECRET",
        )
        assert setup_settings.settings.llm.api_base == "https://custom.example/v1"
        assert setup_settings.settings.llm.api_key == "CUSTOM_ENDPOINT_SECRET"
        assert setup_keys.added == [], "Custom-endpoint credentials must not enter the provider KeyStore"

        # Applying the visible base route during setup must preserve live object
        # identity and non-route tuning while clearing stale per-task identity.
        identity_settings = _SettingsManager()
        identity_settings.settings.llm.active_provider = "provider-a"
        identity_settings.settings.llm.model = "old-base"
        identity_settings.settings.llm.intent_routing.model = "old-router"
        identity_settings.settings.llm.intent_routing.max_tokens = 700
        live_llm = identity_settings.settings.llm
        identity_handler = SetupActionHandler(
            identity_settings, SimpleNamespace(), setup_manager, _Assistant()  # type: ignore[arg-type]
        )
        await identity_handler.setup_llm(provider="provider-b", model="new-base")
        assert identity_settings.settings.llm is live_llm
        assert live_llm.get_model_for_task("intent_routing") == "new-base"
        assert live_llm.intent_routing.model is None
        assert live_llm.intent_routing.max_tokens == 700

        # Tier/provider overrides must not inherit the global provider secret.
        llm = settings_manager.settings.llm
        llm.active_provider = "provider-a"
        llm.api_base = "https://a.example/v1"
        llm.api_key = "SECRET_A"
        llm.heavy.provider = "provider-b"
        llm.heavy.api_base = "https://b.example/v1"
        llm.heavy.api_key = None
        assert llm.get_api_key_for_task("download") is None
        assert llm.get_api_base_for_task("download") == "https://b.example/v1"

        # Runtime-reload failure must restore both persisted/in-memory settings truth.
        settings_manager = _SettingsManager()
        settings_manager.settings.llm.active_provider = "provider-a"
        settings_manager.settings.llm.api_base = "https://a.example/v1"
        settings_manager.settings.llm.api_key = "SECRET_A"
        assistant = _Assistant(fail_updates=1)
        handler = SettingsActionHandler(
            settings_manager, assistant, SimpleNamespace(), SimpleNamespace(), manager
        )
        try:
            await handler.update_llm(provider="provider-b", model="model-b")
        except RuntimeError as exc:
            assert "synthetic runtime reload failure" in str(exc)
        else:
            raise AssertionError("Synthetic runtime reload failure must escape")
        restored = settings_manager.settings.llm
        assert restored.active_provider == "provider-a"
        assert restored.api_base == "https://a.example/v1"
        assert restored.api_key == "SECRET_A"
        assert assistant.settings is not None and assistant.settings.llm.active_provider == "provider-a"

        # Persistence can fail after partially replacing a settings file. The
        # mutation service must restore the previous snapshot before surfacing failure.
        settings_manager = _SettingsManager(fail_saves=1)
        settings_manager.settings.llm.active_provider = "provider-a"
        settings_manager.settings.llm.api_base = "https://a.example/v1"
        settings_manager.settings.llm.api_key = "SECRET_A"
        assistant = _Assistant()
        handler = SettingsActionHandler(
            settings_manager, assistant, SimpleNamespace(), SimpleNamespace(), manager
        )
        try:
            await handler.update_llm(provider="provider-b", model="model-b")
        except RuntimeError as exc:
            assert "synthetic persistence failure" in str(exc)
        else:
            raise AssertionError("Synthetic partial persistence failure must escape")
        restored = settings_manager.settings.llm
        assert restored.active_provider == "provider-a"
        assert restored.api_base == "https://a.example/v1"
        assert restored.api_key == "SECRET_A"
        assert assistant.settings is None, "Runtime must not reload a candidate that failed persistence"

    @staticmethod
    def _category_capability_contract() -> None:
        class _Registry:
            @staticmethod
            def list_all() -> list[object]:
                return [TvShowCategory(), MovieCategory()]

        tools = CategoryToolFactory(_Registry()).build_tools()
        by_name = {tool.name: tool for tool in tools}
        resolver = __import__("src.ai.tool_capabilities", fromlist=["AgentToolCapabilityResolver"]).AgentToolCapabilityResolver

        download_tools = {
            "tv.download_next_missing_episode",
            "tv.download_specific_episode",
            "tv.download_season_pack",
            "tv.download_missing_batch",
            "tv.scheduled_check",
            "movie.download_movie",
            "movie.scheduled_check",
        }
        for name in download_tools:
            metadata = resolver.for_tool(by_name[name])
            assert metadata.required == frozenset({InvocationCapability.DOWNLOADS_WRITE}), (name, metadata)
            assert metadata.mutating is True

        # Manifest-only actions without a concrete agent executor must not be
        # advertised as tools that will inevitably fail when selected.
        assert "tv.scan_library" not in by_name
        assert "movie.scan_library" not in by_name
        assert "tv.consolidate_library" not in by_name
        assert "movie.consolidate_library" not in by_name

        # Deletion needs library-write authority to be exposed; file deletion is
        # a conditional extra capability enforced from the concrete arguments.
        for name in {"tv.delete_item", "movie.delete_item"}:
            metadata = resolver.for_tool(by_name[name])
            assert metadata.required == frozenset({InvocationCapability.LIBRARY_WRITE}), (name, metadata)

        # Every currently registered concrete mutating category tool must own a
        # real application authorization domain rather than fall back to ADMIN.
        full_tools = CategoryToolFactory(CategoryRegistry.with_defaults()).build_tools()
        for tool in full_tools:
            tool_metadata = resolver.for_tool(tool)
            if tool_metadata.mutating:
                assert InvocationCapability.ADMIN not in tool_metadata.required, (tool.name, tool_metadata)

        # The generic dispatcher spans authorization domains and therefore must
        # remain admin-only for constrained external principals.
        metadata = resolver.for_name("execute_category_action")
        assert metadata.required == frozenset({InvocationCapability.ADMIN})

        # Risk alone never grants config.write. A future unannotated mutation
        # must fail closed until its owning category declares authorization.
        unknown_write = SimpleNamespace(
            name="future.category.write",
            risk_level="write",
            required_capabilities=None,
        )
        metadata = resolver.for_tool(unknown_write)
        assert metadata.required == frozenset({InvocationCapability.ADMIN})

    @staticmethod
    async def _tool_capability_contract() -> None:
        calls: list[str] = []

        async def read(**_: object) -> dict[str, object]:
            calls.append("read")
            return {"ok": True}

        async def write(**_: object) -> dict[str, object]:
            calls.append("write")
            return {"ok": True}

        registry = ToolRegistry()
        registry.register("web_search", "read", {}, read)
        registry.register("queue_download", "write", {}, write)
        registry.register("unknown_future_tool", "unknown", {}, write)
        principal = InvocationPrincipal(
            principal_id="read-only",
            client_id="client",
            source="mcp",
            capabilities={InvocationCapability.AGENT_READ, InvocationCapability.AGENT_DELEGATE},
        )
        context = ToolExecutionContextFactory.create(
            invocation=InvocationContext(principal=principal, allow_actions=False),
            session_id="external",
            user_prompt="test",
        )
        visible = registry.filter_names_for_context(
            {"web_search", "queue_download", "unknown_future_tool"},
            context,
        )
        assert visible == {"web_search"}
        denied = await registry.execute("queue_download", {}, context=context)
        assert denied.get("error_code") in {"CAPABILITY_DENIED", "ACTIONS_DISABLED"}
        assert calls == []

        write_principal = principal.model_copy(
            update={"capabilities": {InvocationCapability.AGENT_READ, InvocationCapability.DOWNLOADS_WRITE}}
        )
        write_context = ToolExecutionContextFactory.create(
            invocation=InvocationContext(principal=write_principal, allow_actions=True),
            session_id="external",
            user_prompt="test",
        )
        visible = registry.filter_names_for_context(
            {"web_search", "queue_download", "unknown_future_tool"},
            write_context,
        )
        assert visible == {"web_search", "queue_download"}
        allowed = await registry.execute("queue_download", {}, context=write_context)
        assert allowed.get("ok") is True and calls == ["write"]

        trusted = ToolExecutionContextFactory.create(
            invocation=None,
            session_id="web_local",
            user_prompt="test",
        )
        assert trusted.trusted is True
        assert registry.filter_names_for_context(
            {"web_search", "queue_download", "unknown_future_tool"}, trusted
        ) == {"web_search", "queue_download", "unknown_future_tool"}


    @staticmethod
    def _category_destructive_policy_contract() -> None:
        """Keep destructive category tools hidden from ordinary assistant turns.

        Explicit category workflow callers can use the token-bound confirmation
        protocol, but AIAssistant has no policy-level ``confirmed`` continuation
        state today. Advertising deletes in a normal CONFIG turn would therefore
        create an unreachable or double-confirmation contract.
        """
        from src.ai.tool_policy import AgentToolPolicy
        from src.core.models import Intent

        policy = AgentToolPolicy(Settings())
        for category in (MovieCategory(), TvShowCategory()):
            normal = policy.allowed_tool_names(Intent.CONFIG, category=category)
            assert f"{category.category_id}.delete_item" not in normal

    @staticmethod
    async def _category_conditional_delete_contract() -> None:
        """Require file-delete authority only when the requested delete touches files."""
        principal = InvocationPrincipal(
            principal_id="library-writer", client_id="client", source="mcp",
            capabilities={InvocationCapability.AGENT_READ, InvocationCapability.LIBRARY_WRITE},
        )
        tool_context = ToolExecutionContextFactory.create(
            invocation=InvocationContext(principal=principal, allow_actions=True),
            session_id="external-delete",
        )
        context = CategoryWorkflowContext(
            db=SimpleNamespace(media=SimpleNamespace()),
            pipeline=None,
            aggregator=None,
            settings=Settings(),
            tool_execution_context=tool_context,
        )  # type: ignore[arg-type]
        movie = MovieCategory()
        denied = await movie.execute_workflow(
            "delete_item", {"item_id": "Example", "delete_files": True}, context
        )
        assert denied.status == "failed"
        assert "library.files.delete" in str(denied.technical_message or "")

        confirmation = await movie.execute_workflow(
            "delete_item", {"item_id": "Example", "delete_files": False}, context
        )
        assert confirmation.status == "needs_confirmation"
        assert str(confirmation.data.get("confirmation_token") or "")

    @staticmethod
    async def _authentication_contract() -> None:
        settings = MCPIntegrationSettings(
            enabled=True,
            bearer_token="dedicated-secret",
            principal_id="mcp-test",
            user_id="local",
            client_id="mcp-client",
            capabilities=frozenset({InvocationCapability.AGENT_DELEGATE}),
        )
        resolver = MCPPrincipalResolver(settings=settings, auth_service=_Auth(), database=_Database())
        dedicated = await resolver.resolve({"authorization": "Bearer dedicated-secret"})
        assert dedicated.principal_id == "mcp-test"
        assert dedicated.user_id == "local"
        assert dedicated.client_id == "mcp-client"
        assert dedicated.trusted is False
        try:
            await resolver.resolve({"authorization": "Bearer web-jwt"})
        except MCPAuthenticationError:
            pass
        else:
            raise AssertionError("Generic web JWTs must not be widened into MCP authority")
        try:
            await resolver.resolve({"authorization": "Bearer bad"})
        except MCPAuthenticationError:
            pass
        else:
            raise AssertionError("Invalid bearer token must be rejected")

    @staticmethod
    async def _authentication_boundary_contract() -> None:
        calls: list[str] = []

        async def inner(scope: object, receive: object, send: object) -> None:
            principal = MCPRequestPrincipalContext.require()
            calls.append(f"inner:{principal.principal_id}")

        async def receive() -> dict[str, object]:
            return {"type": "http.request"}

        sent: list[dict[str, object]] = []

        async def send(message: dict[str, object]) -> None:
            sent.append(message)

        settings = MCPIntegrationSettings(
            enabled=True,
            bearer_token="x" * 32,
            principal_id="mcp-test",
            user_id="local",
            client_id="mcp-client",
            capabilities=frozenset({InvocationCapability.AGENT_DELEGATE}),
        )
        boundary = MCPAuthenticationBoundary(
            inner,
            MCPPrincipalResolver(settings=settings, auth_service=_Auth(), database=_Database()),
        )
        await boundary({"type": "http", "headers": []}, receive, send)
        assert calls == []
        assert sent and sent[0].get("status") == 401
        calls.clear()
        sent.clear()
        await boundary(
            {"type": "http", "headers": [(b"Authorization", b"Bearer " + b"x" * 32)]},
            receive,
            send,
        )
        assert calls == ["inner:mcp-test"]

    @staticmethod
    async def _conversation_handle_contract() -> None:
        database = _Database()
        service = ConversationHandleService(database)
        alice = InvocationPrincipal(
            principal_id="alice",
            client_id="client-a",
            source="mcp",
            capabilities={InvocationCapability.AGENT_DELEGATE},
        )
        bob = alice.model_copy(update={"principal_id": "bob", "client_id": "client-b"})
        handle = await service.mint(alice)
        assert handle.handle_id != handle.internal_session_id
        assert handle.user_id == "local"
        resolved = await service.resolve(handle.handle_id, alice)
        assert resolved.internal_session_id == handle.internal_session_id
        try:
            await service.resolve(handle.handle_id, bob)
        except ConversationHandleAccessError:
            pass
        else:
            raise AssertionError("Conversation handles must be principal-bound")

        different_user = alice.model_copy(update={"user_id": "another-user"})
        try:
            await service.resolve(handle.handle_id, different_user)
        except ConversationHandleAccessError:
            pass
        else:
            raise AssertionError("Conversation handles must be canonical-user-bound")

        limited = ConversationHandleService(_Database(), max_active_per_principal=1)
        first = await limited.mint(alice)
        try:
            await limited.mint(alice)
        except ConversationHandleLimitError:
            pass
        else:
            raise AssertionError("Conversation handle quota must reject unbounded durable sessions")
        first_session = first.internal_session_id
        assert await limited.revoke(first.handle_id, alice) is True
        assert first_session not in limited._database.users.sessions  # type: ignore[attr-defined]
        await limited.mint(alice)

        missing_user_db = _Database()
        missing_user_service = ConversationHandleService(missing_user_db)
        named_principal = alice.model_copy(update={"user_id": "does-not-exist"})
        try:
            await missing_user_service.mint(named_principal)
        except ConversationHandleAccessError as exc:
            assert "does not exist" in str(exc)
        else:
            raise AssertionError("A configured non-local MCP user must already exist")
        assert missing_user_db.users.sessions == {}

        class _FailingHandles(_Handles):
            async def create(self, **row: object) -> dict[str, object]:
                await super().create(**row)
                raise RuntimeError("synthetic handle persistence failure")

        rollback_db = SimpleNamespace(users=_Users(), conversation_handles=_FailingHandles())
        rollback_service = ConversationHandleService(rollback_db)
        try:
            await rollback_service.mint(alice)
        except RuntimeError as exc:
            assert "synthetic handle persistence failure" in str(exc)
        else:
            raise AssertionError("Synthetic handle mint failure must escape")
        assert rollback_db.users.sessions == {}, "Failed handle mint must clean its orphan internal session"
        assert all(row.get("revoked_at") is not None for row in rollback_db.conversation_handles.rows.values())

    @staticmethod
    async def _delegation_contract() -> None:
        principal = InvocationPrincipal(
            principal_id="mcp-test",
            client_id="mcp-test",
            source="mcp",
            capabilities={InvocationCapability.AGENT_DELEGATE, InvocationCapability.AGENT_READ},
        )
        runner = _Runner("agent-result")
        service = AgentDelegationService(
            runner,  # type: ignore[arg-type]
            ChatTurnRegistry(),
            ConversationHandleService(_Database()),
        )
        result = await service.send_message(principal=principal, message="hello")
        assert result.status == AgentDelegationStatus.COMPLETE
        assert result.message == "agent-result"
        request = runner.requests[0]
        assert getattr(request, "invocation_context").allow_actions is False
        assert getattr(request, "session_id") != result.conversation_id
        continued = await service.send_message(
            principal=principal,
            message="continue",
            conversation_id=result.conversation_id,
        )
        assert continued.conversation_id == result.conversation_id
        assert getattr(runner.requests[1], "session_id") == getattr(request, "session_id")

    @staticmethod
    async def _delegation_failure_contract() -> None:
        principal = InvocationPrincipal(
            principal_id="mcp-failure", client_id="client", source="mcp",
            capabilities={InvocationCapability.AGENT_DELEGATE, InvocationCapability.AGENT_READ},
        )
        service = AgentDelegationService(
            _FailingRunner(),  # type: ignore[arg-type]
            ChatTurnRegistry(),
            ConversationHandleService(_Database()),
        )
        result = await service.send_message(principal=principal, message="fail safely")
        assert result.status == AgentDelegationStatus.FAILED
        assert "synthetic private failure details" not in result.message

    @staticmethod
    async def _admission_contract() -> None:
        principal = InvocationPrincipal(
            principal_id="mcp-admission", client_id="client", source="mcp",
            capabilities={InvocationCapability.AGENT_DELEGATE, InvocationCapability.AGENT_READ},
        )
        runner = _BlockingRunner()
        handles = ConversationHandleService(_Database())
        registry = ChatTurnRegistry()
        admission = AgentDelegationAdmissionGate(max_active_per_principal=1)
        service = AgentDelegationService(
            runner, registry, handles,
            admission_gate=admission,
        )  # type: ignore[arg-type]
        first = asyncio.create_task(service.send_message(principal=principal, message="slow"))
        await asyncio.wait_for(runner.started.wait(), timeout=1.0)
        try:
            await service.send_message(principal=principal, message="second conversation")
        except AgentDelegationDenied as exc:
            assert "concurrent delegated-turn limit" in str(exc)
        else:
            raise AssertionError("Principal-level admission must reject unbounded concurrent turns")
        # Cancelling the owning request also cancels its registered LJS turn and releases admission.
        first.cancel()
        try:
            await first
        except asyncio.CancelledError:
            pass
        assert await admission.try_acquire(principal) is True, "Cancelled turns must release principal admission"
        await admission.release(principal)

    @staticmethod
    async def _cancellation_contract() -> None:
        principal = InvocationPrincipal(
            principal_id="mcp-cancel",
            client_id="mcp-cancel",
            source="mcp",
            capabilities={InvocationCapability.AGENT_DELEGATE, InvocationCapability.AGENT_READ},
        )
        runner = _BlockingRunner()
        handles = ConversationHandleService(_Database())
        resolved = await handles.mint(principal)
        registry = ChatTurnRegistry()
        service = AgentDelegationService(runner, registry, handles)  # type: ignore[arg-type]
        task = asyncio.create_task(
            service.send_message(
                principal=principal,
                message="slow",
                conversation_id=resolved.handle_id,
            )
        )
        await asyncio.wait_for(runner.started.wait(), timeout=1.0)
        active = await registry.active(resolved.internal_session_id)
        assert active is not None
        cancelled = await service.cancel_turn(
            principal=principal,
            conversation_id=resolved.handle_id,
            turn_id=active.turn_id,
        )
        assert cancelled.status == AgentDelegationStatus.CANCELLED
        await asyncio.wait_for(runner.cancelled.wait(), timeout=1.0)
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("Owning delegated request must observe turn cancellation")
        assert await registry.active(resolved.internal_session_id) is None

        no_active = await service.cancel_turn(
            principal=principal,
            conversation_id=resolved.handle_id,
            turn_id="does-not-exist",
        )
        assert no_active.status == AgentDelegationStatus.NOT_RUNNING
        assert no_active.matched is False
        assert no_active.cancellation_requested is False
        assert no_active.settled is True

        class _UnsettledRegistry:
            async def cancel_and_wait(self, session_id: str, turn_id: str | None = None) -> tuple[object, bool]:
                task = asyncio.create_task(asyncio.sleep(60))
                self.task = task
                return SimpleNamespace(turn_id=turn_id or "stubborn", task=task), False

            async def release(self, session_id: str, turn_id: str) -> None:
                raise AssertionError("An unsettled turn must not be released")

        unsettled_registry = _UnsettledRegistry()
        unsettled_service = AgentDelegationService(runner, unsettled_registry, handles)  # type: ignore[arg-type]
        unwinding = await unsettled_service.cancel_turn(
            principal=principal, conversation_id=resolved.handle_id, turn_id="stubborn"
        )
        assert unwinding.status == AgentDelegationStatus.CANCELLING
        assert unwinding.matched is True
        assert unwinding.cancellation_requested is True
        assert unwinding.settled is False
        unsettled_registry.task.cancel()
        try:
            await unsettled_registry.task
        except asyncio.CancelledError:
            pass

    @staticmethod
    async def _conversation_close_contract() -> None:
        principal = InvocationPrincipal(
            principal_id="mcp-close", client_id="client", source="mcp",
            capabilities={InvocationCapability.AGENT_DELEGATE},
        )
        close_db = _Database()
        handles = ConversationHandleService(close_db)
        resolved = await handles.mint(principal)
        service = AgentDelegationService(_Runner(), ChatTurnRegistry(), handles)  # type: ignore[arg-type]
        closed = await service.close_conversation(principal=principal, conversation_id=resolved.handle_id)
        assert closed.status == AgentDelegationStatus.CLOSED
        assert closed.settled is True
        try:
            await handles.resolve(resolved.handle_id, principal)
        except ConversationHandleAccessError:
            pass
        else:
            raise AssertionError("Closed conversation handles must be revoked")
        assert resolved.internal_session_id not in close_db.users.sessions, (
            "Closing an external conversation must clean its private session/history lifecycle"
        )

    @staticmethod
    async def _definition_backed_hidden_write_contract() -> None:
        class _MediaRepo:
            def __init__(self) -> None:
                self.writes = 0

            async def upsert_category_metadata(self, *args: object) -> None:
                self.writes += 1

        category = CategoryRegistry.with_defaults().get("music")
        assert category is not None
        media = _MediaRepo()
        read_principal = InvocationPrincipal(
            principal_id="read-only", client_id="client", source="mcp",
            capabilities={InvocationCapability.AGENT_READ},
        )
        read_context = ToolExecutionContextFactory.create(
            invocation=InvocationContext(principal=read_principal, allow_actions=False),
            session_id="external",
        )
        workflow_context = CategoryWorkflowContext(
            db=SimpleNamespace(media=media), pipeline=None, aggregator=None, settings=Settings(),
            tool_execution_context=read_context,
        )  # type: ignore[arg-type]
        persisted = await category._persist_resolved_metadata(  # type: ignore[attr-defined]
            {"item_id": "album-1"},
            workflow_context,
            {"best": {"provider": "musicbrainz", "stable_id": "mbid-1", "title": "Album"}},
        )
        assert persisted is None and media.writes == 0, "Read-only definition workflows must not hide DB writes"

    @staticmethod
    def _evidence_contract() -> None:
        evidence = InvocationEvidence()
        ToolResultEvidenceCollector.record(
            {
                "result_set_id": "rs-1",
                "candidates": [{"candidate_id": "cand-1"}],
                "command_receipt": {
                    "command_id": "cmd-1",
                    "receipt_persisted": True,
                },
                "status": "needs_confirmation",
                "ignored_receipt": {
                    "command_receipt": {
                        "command_id": "cmd-unpersisted",
                        "receipt_persisted": False,
                    }
                },
            },
            evidence,
        )
        assert evidence.result_set_ids == ["rs-1"]
        assert evidence.candidate_ids == ["cand-1"]
        assert evidence.action_receipt_ids == ["cmd-1"]
        assert evidence.needs_input is True

    @staticmethod
    def _redaction_contract() -> None:
        payload = {
            "item_id": "x",
            "path": "/private/library/file.mkv",
            "nested": {"local_path": "/private/other", "safe": "ok"},
            "items": [{"download_root": "/private/downloads", "name": "safe"}],
        }
        redacted = PublicLibraryRedactor.redact(payload)
        assert "path" not in redacted
        assert redacted["nested"] == {"safe": "ok"}
        assert redacted["items"] == [{"name": "safe"}]

    @staticmethod
    def _status_error_redaction_contract() -> None:
        class _BrokenStorage:
            def build_report(self) -> object:
                raise RuntimeError("/private/path/SECRET storage failure")

        principal = InvocationPrincipal(
            principal_id="status-reader", client_id="client", source="mcp",
            capabilities={InvocationCapability.STATUS_READ},
        )
        result = PublicStatusService(_BrokenStorage()).get(principal)
        assert result["status"] == "degraded"
        rendered = str(result)
        assert "/private/path/SECRET" not in rendered
        assert "diagnostics" in rendered.casefold()

    @staticmethod
    async def _network_boundary_contract() -> None:
        calls: list[str] = []

        async def inner(scope: object, receive: object, send: object) -> None:
            calls.append("inner")

        async def receive() -> dict[str, object]:
            return {"type": "http.request"}

        sent: list[dict[str, object]] = []

        async def send(message: dict[str, object]) -> None:
            sent.append(message)

        boundary = LocalMCPNetworkBoundary(inner)
        await boundary({"type": "http", "client": ("127.0.0.1", 1234)}, receive, send)
        assert calls == ["inner"]
        calls.clear()
        sent.clear()
        await boundary({"type": "http", "client": ("192.168.1.50", 1234)}, receive, send)
        assert calls == []
        assert sent and sent[0].get("status") == 403
        sent.clear()
        await boundary({"type": "http", "client": None}, receive, send)
        assert calls == []
        assert sent and sent[0].get("status") == 403

    @staticmethod
    def _adapter_surface_contract() -> None:
        class _Manager:
            def run(self) -> object:
                return SimpleNamespace()

        class _Server:
            last: "_Server | None" = None

            def __init__(self, name: str) -> None:
                self.name = name
                self.tools: list[str] = []
                self.resources: list[str] = []
                self.session_manager = _Manager()
                self.stream_path = None
                _Server.last = self

            def tool(self, *, name: str | None = None, **_: object):
                def decorator(fn: object) -> object:
                    self.tools.append(str(name or getattr(fn, "__name__", "")))
                    return fn
                return decorator

            def resource(self, uri: str, **_: object):
                def decorator(fn: object) -> object:
                    self.resources.append(uri)
                    return fn
                return decorator

            def streamable_http_app(self, *, streamable_http_path: str = "/mcp", **_: object) -> object:
                self.stream_path = streamable_http_path
                return SimpleNamespace()

        server_module = types.ModuleType("mcp.server")
        server_module.MCPServer = _Server
        mcpserver_module = types.ModuleType("mcp.server.mcpserver")
        mcpserver_module.Context = type("Context", (), {})
        mcp_module = types.ModuleType("mcp")
        previous = {name: sys.modules.get(name) for name in ("mcp", "mcp.server", "mcp.server.mcpserver")}
        try:
            sys.modules["mcp"] = mcp_module
            sys.modules["mcp.server"] = server_module
            sys.modules["mcp.server.mcpserver"] = mcpserver_module
            sys.modules.pop("src.integrations.mcp_server", None)
            module = importlib.import_module("src.integrations.mcp_server")
            adapter = module.MCPServerAdapter(
                control_plane=SimpleNamespace(),
                principal_resolver=SimpleNamespace(),
            )
            server = _Server.last
            assert adapter.asgi_app is not None
            assert server is not None and server.stream_path == "/"
            assert set(server.tools) == {
                "ljs.agent_message", "ljs.agent_cancel", "ljs.agent_close", "ljs.status", "ljs.capabilities",
                "ljs.library_list", "ljs.library_get", "ljs.downloads_list", "ljs.llm_get",
                "ljs.llm_test", "ljs.llm_set", "ljs.diagnostics_recent",
            }
            assert set(server.resources) == {
                "ljs://status", "ljs://capabilities", "ljs://library/summary",
                "ljs://downloads/active", "ljs://configuration/llm",
            }
            assert not any("search" in name for name in server.tools)
            assert "ljs.download" not in server.tools
        finally:
            sys.modules.pop("src.integrations.mcp_server", None)
            for name, value in previous.items():
                if value is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = value


if __name__ == "__main__":
    asyncio.run(_MCPControlPlaneAcceptance.run())
    print("MCP_CONTROL_PLANE_PASS")
