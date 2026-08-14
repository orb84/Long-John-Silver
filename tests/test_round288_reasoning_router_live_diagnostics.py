"""Round 288 regressions for reasoning-safe routing and visible LLM failures."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.ai.chat_session_runner import ChatSessionRunner, ChatTurnRequest
from src.ai.intent_router import IntentRouter
from src.core.models import Intent
from src.llm_providers.activity import LLMActivityMonitor
from src.llm_providers.call_policy import LLMCallEnvelope, LLMCallPolicy, LLMTaskPromptPolicy
from src.llm_providers.task_client import ResolvedLLMTask, TaskLLMClient
from src.web.llm_diagnostics import LLMActivityBroadcaster, LLMDiagnosticLogReader


class _RoutingClient:
    """Capture the task-aware router call without touching a provider."""

    def __init__(self, response: str = "DOWNLOAD") -> None:
        self.response = response
        self.kwargs = None

    async def completion(self, **kwargs):
        self.kwargs = kwargs
        return {"choices": [{"message": {"content": self.response}}]}


@pytest.mark.asyncio
async def test_intent_router_never_imposes_a_tiny_output_token_cap() -> None:
    client = _RoutingClient()
    router = IntentRouter(llm_client=client)

    decision = await router.route_with_details(
        "download the available episodes of the latest season of Silo in Italian"
    )

    assert decision.intent is Intent.DOWNLOAD
    assert decision.status == "success"
    assert client.kwargs is not None
    assert client.kwargs["task"] == "intent_routing"
    assert "temperature" not in client.kwargs
    assert "max_tokens" not in client.kwargs


def test_gpt_oss_router_uses_low_reasoning_instruction_not_token_starvation() -> None:
    messages = [{"role": "system", "content": "Return one label."}, {"role": "user", "content": "download Silo"}]

    routed = LLMTaskPromptPolicy.apply(
        task="intent_routing",
        model="openai/gpt-oss-120b",
        messages=messages,
    )

    assert routed[0]["role"] == "system"
    assert str(routed[0]["content"]).startswith("Reasoning: low")
    assert len(routed) == len(messages)
    assert all("max_tokens" not in row for row in routed)
    assert messages == [
        {"role": "system", "content": "Return one label."},
        {"role": "user", "content": "download Silo"},
    ]


def test_routing_timeout_is_observable_and_not_an_aggressive_15_second_cutoff() -> None:
    envelope = LLMCallPolicy.resolve("intent_routing")
    assert envelope == LLMCallEnvelope(max_attempts=2, timeout_seconds=90.0)
    assert LLMCallPolicy.resolve(
        "intent_routing", {"request_timeout_seconds": 140, "max_attempts": 1}
    ) == LLMCallEnvelope(max_attempts=1, timeout_seconds=140.0)


@pytest.mark.asyncio
async def test_nvidia_receives_generation_overrides_without_inventing_max_tokens(monkeypatch) -> None:
    import httpx

    captured = {}

    class _Response:
        status_code = 200
        text = "ok"

        @staticmethod
        def json():
            return {
                "id": "route-1",
                "model": "openai/gpt-oss-120b",
                "choices": [{"message": {"role": "assistant", "content": "DOWNLOAD"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 4, "total_tokens": 24},
            }

    class _Client:
        def __init__(self, *args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json, headers):
            captured["url"] = url
            captured["payload"] = json
            return _Response()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    client = object.__new__(TaskLLMClient)
    client._activity_monitor = LLMActivityMonitor()
    client._llm_logger = None
    resolved = ResolvedLLMTask(
        task="intent_routing",
        model="openai/gpt-oss-120b",
        provider_id="nvidia_nim",
        api_base="http://nim.test/v1",
        api_key="secret",
        max_tokens=None,
        temperature=None,
    )
    call_id = client._activity_monitor.start_call(
        task="intent_routing",
        provider="nvidia_nim",
        model=resolved.model,
        messages=[{"role": "user", "content": "route"}],
        tools=None,
        stream=False,
    )

    response = await client._completion_nvidia(
        resolved,
        [{"role": "user", "content": "route"}],
        None,
        False,
        activity_id=call_id,
        generation_options={"temperature": 0.0, "top_p": 0.2},
        call_envelope=LLMCallEnvelope(max_attempts=2, timeout_seconds=90.0),
    )

    assert response.choices[0].message.content == "DOWNLOAD"
    assert captured["timeout"] == 90.0
    assert captured["payload"]["temperature"] == 0.0
    assert captured["payload"]["top_p"] == 0.2
    assert "max_tokens" not in captured["payload"]


def test_each_timeout_and_retry_emits_an_individual_user_event() -> None:
    events = []
    monitor = LLMActivityMonitor()
    monitor.set_event_sink(events.append)
    call_id = monitor.start_call(
        task="intent_routing",
        provider="nvidia_nim",
        model="openai/gpt-oss-120b",
        messages=[{"role": "user", "content": "route"}],
        tools=None,
        stream=False,
    )

    monitor.record_attempt(call_id, attempt=1, max_attempts=2, status="started")
    monitor.record_attempt(call_id, attempt=1, max_attempts=2, status="failed", error="ReadTimeout: timed out")
    monitor.record_attempt(call_id, attempt=2, max_attempts=2, status="started")
    monitor.record_attempt(call_id, attempt=2, max_attempts=2, status="failed", error="ReadTimeout: timed out")
    monitor.finish_call(call_id, status="failed", error="ReadTimeout: timed out")

    timeout_events = [event for event in events if event["event_type"] == "attempt_timeout"]
    retry_events = [event for event in events if event["event_type"] == "retry_started"]
    assert [(event["attempt"], event["max_attempts"]) for event in timeout_events] == [(1, 2), (2, 2)]
    assert [(event["attempt"], event["max_attempts"]) for event in retry_events] == [(2, 2)]
    assert all(event["call_id"] == call_id for event in timeout_events + retry_events)


def test_activity_snapshot_redacts_provider_secrets_from_card_reconciliation() -> None:
    monitor = LLMActivityMonitor()
    call_id = monitor.start_call(
        task="intent_routing",
        provider="nvidia_nim",
        model="openai/gpt-oss-120b",
        messages=[{"role": "user", "content": "route"}],
        tools=None,
        stream=False,
    )
    monitor.record_attempt(
        call_id,
        attempt=1,
        max_attempts=1,
        status="failed",
        error="Authorization: Bearer top-secret-token",
    )
    monitor.finish_call(call_id, status="failed", error="api_key=top-secret-token")

    call = monitor.snapshot()["calls"][0]
    assert "top-secret-token" not in str(call)


def test_activity_broadcaster_uses_existing_websocket_event_contract() -> None:
    emitted = []

    class _EventBus:
        def emit(self, event_type, data):
            emitted.append((event_type, data))

    broadcaster = LLMActivityBroadcaster(_EventBus())
    broadcaster({"event_type": "attempt_timeout", "call_id": "abc"})

    assert emitted == [("llm_activity", {"event": {"event_type": "attempt_timeout", "call_id": "abc"}})]


@pytest.mark.asyncio
async def test_chat_reports_slow_routing_before_waiting_for_the_provider() -> None:
    class _Assistant:
        async def preflight_intent_for_chat_status(self, prompt, **kwargs):
            await asyncio.sleep(0.14)
            return Intent.DOWNLOAD

        async def run_stream(self, prompt, **kwargs):
            yield "answer"

    runner = ChatSessionRunner(_Assistant())
    events = [
        event async for event in runner.run_events(ChatTurnRequest(
            prompt="download Silo",
            session_id="web_test",
            turn_id="turn_1",
            first_progress_seconds=0.05,
            later_progress_seconds=0.05,
        ))
    ]

    assert events[0].type == "status"
    assert "still classifying" in events[0].content
    assert any(event.type == "token" and event.content == "answer" for event in events)


def test_diagnostic_log_reader_returns_bounded_redacted_context_and_llm_app_rows(tmp_path, monkeypatch) -> None:
    context_log = tmp_path / "llm_context.log"
    response_log = tmp_path / "llm_raw_response.log"
    routing_log = tmp_path / "structured_replies.log"
    app_log = tmp_path / "ljs.log"
    context_log.write_text("prompt api_key=secret-value\nsecond row\n", encoding="utf-8")
    response_log.write_text("raw assistant response\n", encoding="utf-8")
    routing_log.write_text("Routed Intent: DOWNLOAD\nConfidence: 0.95\n", encoding="utf-8")
    app_log.write_text(
        "ordinary subsystem row\n"
        "2026 | WARNING | Intent routing LLM timeout\n"
        "    traceback continuation\n"
        "unrelated final row\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(LLMDiagnosticLogReader, "_SOURCES", {
        "context": context_log,
        "responses": response_log,
        "routing": routing_log,
        "application": app_log,
    })
    reader = LLMDiagnosticLogReader()

    context = reader.read("context", 1)
    responses = reader.read("responses", 100)
    routing = reader.read("routing", 100)
    application = reader.read("application", 100)

    assert context["logs"] == ["second row"]
    assert responses["logs"] == ["raw assistant response"]
    assert routing["logs"] == ["Routed Intent: DOWNLOAD", "Confidence: 0.95"]
    assert any("Intent routing LLM timeout" in row for row in application["logs"])
    assert any("traceback continuation" in row for row in application["logs"])
    assert all("ordinary subsystem" not in row for row in application["logs"])


def test_frontend_has_clickable_problem_cards_and_dedicated_log_views() -> None:
    cards = Path("src/web/static/js/components/llmProblemCards.js").read_text(encoding="utf-8")
    panel = Path("src/web/static/js/components/llmActivityPanel.js").read_text(encoding="utf-8")
    app = Path("src/web/app.py").read_text(encoding="utf-8")

    assert "subscribe('llm_activity'" in cards
    assert "llm_activity_snapshot" in cards
    assert "_reconcileSnapshot" in cards
    assert "_canonicalKey" in cards
    assert "attempt_timeout" in cards
    assert "openCall(event.call_id)" in cards
    assert "LLM Diagnostics" in panel
    assert "Context log" in panel
    assert "Raw responses" in panel
    assert "Routing log" in panel
    assert "LLM app log" in panel
    assert "/api/system/llm-logs" in panel
    assert "LLMActivityBroadcaster(event_bus)" in app
    assert "new LLMActivityPanel(this._eventBus)" in Path(
        "src/web/static/js/app.js"
    ).read_text(encoding="utf-8")
