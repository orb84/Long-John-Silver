"""Round 287 regression coverage for context-window authority and soft targets.

The Round 286 implementation accidentally replaced a provider/user 128k context
window with a hard 32k task cap, then applied the 85% safety margin again.  A
basic DOWNLOAD payload estimated at 28.7k was therefore rejected before provider
I/O.  These tests preserve the corrected contract:

- endpoint/user configuration is the hard authority;
- task limits are soft assembly targets only;
- optional history is compacted toward the target;
- irreducible payload may exceed the target while remaining below the hard cap;
- only the hard cap rejects provider I/O;
- ordinary DOWNLOAD exposes a compact, category-owned tool surface;
- telemetry distinguishes selected, hard, target, measured, and rejected values.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.ai.llm_task_runtime import LLMPayloadBudgetError, LLMTaskRuntime
from src.ai.prompt_builder import PromptBuilder
from src.ai.token_budget import TokenBudgetManager
from src.ai.tool_catalog import AgentToolCatalog
from src.ai.tool_policy import AgentToolPolicy
from src.ai.tools.downloads import DownloadToolProvider
from src.ai.tools.library import LibraryToolProvider
from src.ai.tools.research import ResearchToolProvider
from src.ai.tools.scheduling import SchedulingToolProvider
from src.ai.tools.storage import StorageToolProvider
from src.core.models import Intent, LLMConfig, Settings
from src.llm_providers.activity import LLMActivityContext, LLMActivityMonitor


class _RuntimeClient:
    """Dependency-light provider boundary that records the bound call budget."""

    def __init__(self, *, context_limit: int = 128_000) -> None:
        self.context_limit = context_limit
        self.activity_monitor = LLMActivityMonitor()
        self.called = False
        self.messages = None
        self.tools = None

    def resolve_task(self, task: str):
        return SimpleNamespace(
            context_limit=self.context_limit,
            context_limit_source="built-in provider/model metadata",
            context_limit_reported=True,
            provider_id="test-provider",
            model="test-model",
        )

    async def ensure_model_metadata_for_task(self, task: str, force_refresh: bool = False) -> None:
        return None

    async def completion(self, *, task, messages, tools=None, stream=False, **kwargs):
        self.called = True
        self.messages = messages
        self.tools = tools
        call_id = self.activity_monitor.start_call(
            task=task,
            provider="test-provider",
            model="test-model",
            messages=messages,
            tools=tools,
            stream=stream,
            generation=kwargs,
        )
        response = {"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 1234, "completion_tokens": 12}}
        self.activity_monitor.finish_call(call_id, response=response)
        return response


class _EmptyRegistry:
    def get_definitions_for_intent(self, intent):
        return []


def _runtime(*, percent: int = 85, max_context_tokens: int | None = None) -> tuple[LLMTaskRuntime, _RuntimeClient]:
    client = _RuntimeClient()
    runtime = LLMTaskRuntime(
        Settings(llm=LLMConfig(
            max_context_tokens=max_context_tokens,
            context_budget_percent=percent,
            reserved_output_tokens=1024,
        )),
        client,
        _EmptyRegistry(),
    )
    return runtime, client


def _download_registry():
    return AgentToolCatalog([
        DownloadToolProvider(),
        LibraryToolProvider(),
        ResearchToolProvider(),
        SchedulingToolProvider(),
        StorageToolProvider(),
    ]).build_registry()


def _download_definitions() -> list[dict]:
    return AgentToolPolicy().definitions_for_intent(_download_registry(), Intent.DOWNLOAD) or []


def test_128k_endpoint_remains_hard_authority_and_32k_is_only_soft_target() -> None:
    runtime, _ = _runtime(percent=85)
    budget = runtime.context_budget_for_task("download")

    assert budget["endpoint_context_tokens"] == 128_000
    assert budget["model_context_tokens"] == 128_000
    assert budget["context_cap_source"] == "endpoint"
    assert budget["provider_call_context_tokens"] == 108_800
    assert budget["hard_available_prompt_tokens"] == 107_776
    assert budget["target_context_tokens"] == 32_768
    assert budget["context_target_source"] == "task_auto_target"
    # The 85% safety margin applies once to the hard window.  It must not also
    # shrink the already-conservative 32k soft target to 27,852.
    assert budget["available_prompt_tokens"] == 31_744


def test_incident_sized_basic_download_fits_soft_target_after_surface_cleanup() -> None:
    runtime, _ = _runtime(percent=85)
    budget = runtime.context_budget_for_task("download")
    tools = _download_definitions()
    messages = [
        {"role": "system", "content": "S" * 31_330},
        {"role": "user", "content": "download the last season of Silo in italian"},
    ]

    audit = runtime._audit_payload_budget("download", messages, tools, budget)

    assert len(tools) == 10
    assert audit["total_tokens_estimated"] < budget["target_context_tokens"]
    assert audit["over_target"] is False
    assert audit["over_hard_limit"] is False


@pytest.mark.asyncio
async def test_irreducible_payload_may_spill_over_soft_target_but_calls_provider() -> None:
    runtime, client = _runtime(percent=85)
    tools = _download_definitions()
    # The primary system/current user pair is intentionally irreducible and
    # larger than the 32k target once schemas/output are included, but still
    # comfortably below the 108.8k hard ceiling.
    messages = [
        {"role": "system", "content": "important instruction " * 5000},
        {"role": "user", "content": "download Silo"},
    ]

    result = await runtime.make_completion_fn()(task="download", messages=messages, tools=tools)
    assert result["choices"][0]["message"]["content"] == "ok"
    assert client.called is True
    detail = client.activity_monitor.snapshot(limit=1)["last_call"]
    assert detail["budget"]["payload"]["over_target"] is True
    assert detail["budget"]["payload"]["over_hard_limit"] is False
    assert detail["budget"]["provider_call_context_tokens"] == 108_800


def test_only_hard_ceiling_rejects_and_rejection_is_visible_before_provider_io() -> None:
    runtime, client = _runtime(percent=85)
    budget = runtime.context_budget_for_task("download")
    messages = [
        {"role": "system", "content": "irreducible " * 50_000},
        {"role": "user", "content": "download Silo"},
    ]

    with LLMActivityContext.bind(session_id="web_test", turn_id="turn_budget"):
        with pytest.raises(LLMPayloadBudgetError, match="stopped before provider I/O"):
            runtime._audit_payload_budget("download", messages, [], budget)

    assert client.called is False
    detail = client.activity_monitor.snapshot(limit=1)["last_call"]
    assert detail["status"] == "failed"
    assert detail["session_id"] == "web_test"
    assert detail["turn_id"] == "turn_budget"
    assert detail["generation"]["pre_provider_rejection"] is True
    assert detail["budget"]["payload"]["over_hard_limit"] is True
    assert detail["budget"]["payload"]["over_target"] is True


def test_download_tool_surface_is_compact_and_contains_no_alias_or_config_bloat() -> None:
    tools = _download_definitions()
    names = {row["function"]["name"] for row in tools}
    assert names == {
        "list_downloads",
        "manage_downloads",
        "set_download_priority",
        "get_storage_status",
        "inspect_torrent_candidate",
        "enquire_about_media",
        "metadata_lookup",
        "search_media_torrents",
        "queue_download",
        "enqueue_soulseek_download",
    }
    assert {
        "cancel_downloads",
        "pause_downloads",
        "resume_downloads",
        "download_set_priority",
        "download_upload",
        "check_storage_capacity",
        "get_library_status",
        "get_soulseek_share_plan",
        "suggestions_list",
        "track_category_item",
        "create_web_information_watch",
    }.isdisjoint(names)
    assert TokenBudgetManager().estimate_tools(tools) < 14_000


def test_download_prompt_does_not_advertise_unavailable_web_or_tracking_tools() -> None:
    prompt = PromptBuilder().build_system_prompt(Intent.DOWNLOAD, active_category_id="tv")
    assert "PUBLIC WEB RESEARCH GUIDANCE" not in prompt
    assert "create_web_information_watch" not in prompt
    assert "track_category_item" not in prompt
    assert "search_media_torrents" in prompt
    assert "enquire_about_media" in prompt


def test_activity_ui_labels_soft_target_hard_ceiling_and_measured_payload() -> None:
    js = Path("src/web/static/js/components/llmActivityPanel.js").read_text()
    assert "Interactive target" in js
    assert "Hard context ceiling" in js
    assert "Selected/model window" in js
    assert "soft target exceeded · allowed" in js
    assert "hard limit exceeded · rejected" in js
    assert "Context budget and measured payload" in js


def test_routing_retry_envelope_allows_reasoning_provider_latency() -> None:
    from src.llm_providers.task_client import TaskLLMClient

    assert TaskLLMClient._retry_policy("intent_routing") == (2, 90.0)
    assert TaskLLMClient._retry_policy("routing_fast") == (2, 90.0)


def test_generic_provider_retry_classifier_rejects_non_transient_api_errors() -> None:
    from src.llm_providers.task_client import TaskLLMClient

    assert TaskLLMClient._should_retry(RuntimeError("API request rejected: invalid schema")) is False
    assert TaskLLMClient._should_retry(RuntimeError("HTTP 400 bad request")) is False
    assert TaskLLMClient._should_retry(RuntimeError("API error (502 Bad Gateway)")) is True
    assert TaskLLMClient._should_retry(TimeoutError("request timed out")) is True


@pytest.mark.asyncio
async def test_nvidia_non_transient_http_failure_is_not_retried(monkeypatch) -> None:
    import httpx
    from src.llm_providers.task_client import TaskLLMClient

    calls = {"count": 0}

    class _Response:
        status_code = 400
        text = "invalid request"

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            calls["count"] += 1
            return _Response()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    client = object.__new__(TaskLLMClient)
    client._activity_monitor = LLMActivityMonitor()
    resolved = SimpleNamespace(
        task="intent_routing",
        api_base="http://localhost/v1",
        api_key="key",
        model="openai/gpt-oss-120b",
        temperature=None,
        max_tokens=None,
    )
    activity_id = client._activity_monitor.start_call(
        task="intent_routing",
        provider="nvidia_nim",
        model=resolved.model,
        messages=[{"role": "user", "content": "route"}],
        tools=None,
        stream=False,
    )

    with pytest.raises(RuntimeError, match="status code 400"):
        await client._completion_nvidia(
            resolved,
            [{"role": "user", "content": "route"}],
            None,
            False,
            activity_id=activity_id,
        )

    assert calls["count"] == 1
    detail = client._activity_monitor.detail(activity_id)
    assert detail is not None
    assert len(detail["attempts"]) == 1
    assert detail["attempts"][0]["status"] == "failed"


@pytest.mark.asyncio
async def test_production_taste_ingestion_is_dispatched_to_supervisor_without_blocking() -> None:
    from src.ai.assistant import AIAssistant

    queued = {}

    class _Supervisor:
        def spawn_one_shot(self, name, factory):
            queued["name"] = name
            queued["factory"] = factory

    assistant = object.__new__(AIAssistant)
    assistant._taste_signal_ingestor = object()
    assistant._task_supervisor = _Supervisor()
    ran = {"value": False}

    async def _fake_ingest(**kwargs):
        ran["value"] = True

    assistant._ingest_taste_from_turn = _fake_ingest
    await assistant._dispatch_taste_ingestion(
        user_prompt="download Silo",
        assistant_response="queued",
        user_id="u",
        session_id="web_test",
        ctx=SimpleNamespace(category_id="tv", intent=Intent.DOWNLOAD),
    )

    assert queued["name"] == "assistant_taste_ingestion"
    assert ran["value"] is False
    await queued["factory"]()
    assert ran["value"] is True


def test_bare_download_does_not_launch_redundant_taste_llm_call() -> None:
    from src.ai.taste_signal_ingestion import TasteSignalIngestionService

    service = TasteSignalIngestionService(
        llm_client=object(),
        settings=None,
        taste_profiler=object(),
    )

    assert service._could_contain_taste_evidence(
        "download the last season of Silo in Italian",
        Intent.DOWNLOAD,
    ) is False
    assert service._could_contain_taste_evidence(
        "I love Silo; download the last season in Italian",
        Intent.DOWNLOAD,
    ) is True


def test_settings_persistence_uses_same_context_headroom_bounds_as_runtime() -> None:
    source = Path("src/web/action_handlers/settings.py").read_text()
    assert 'context_budget_percent = max(20, min(100' in source


def test_real_download_prompt_and_schema_surface_leave_substantial_soft_target_headroom() -> None:
    from src.ai.assistant import AIAssistant

    manager = TokenBudgetManager()
    prompt = AIAssistant._download_tool_loop_contract(
        PromptBuilder().build_system_prompt(Intent.DOWNLOAD, active_category_id="tv")
    )
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": "download the available episodes of the latest season of Silo in Italian"},
    ]
    estimated_total = (
        manager.estimate_messages(messages)
        + manager.estimate_tools(_download_definitions())
        + 1024
    )

    assert estimated_total < 24_000
    assert estimated_total < 32_768


def test_compact_download_tool_descriptions_reference_exposed_tools_only() -> None:
    definitions = _download_definitions()
    serialized = str(definitions)
    assert "list_media" not in serialized
    assert "list_downloads" in serialized
