"""Round 286 regressions for bounded LLM calls, chat cancellation, and TV identity."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.ai.llm_task_runtime import LLMTaskRuntime, LLMPayloadBudgetError
from src.ai.streaming_agent_loop import StreamingAgentLoopExecutor
from src.ai.token_budget import TokenBudgetManager
from src.core.categories.identity_resolution import CategoryIdentityResolver
from src.core.categories.tv import TvShowCategory
from src.core.models import Intent, LLMConfig, Settings
from src.llm_providers.activity import LLMActivityContext, LLMActivityMonitor
from src.integrations.metadata_providers.books import BookMetadataProviders
from src.utils.auth import AuthService
from src.web.app import create_app


class _Category:
    def __init__(self, category_id: str, *, metadata: list[dict] | None = None, web: list[dict] | None = None):
        self.category_id = category_id
        self.display_name = category_id.title()
        self.metadata = list(metadata or [])
        self.web = list(web or [])
        self.metadata_calls = 0
        self.web_calls = 0

    async def identify_agent_item(self, name, **kwargs):
        self.metadata_calls += 1
        return list(self.metadata)

    async def identify_agent_item_via_web(self, name, **kwargs):
        self.web_calls += 1
        return list(self.web)


class _Registry:
    def __init__(self, categories: list[_Category], router_ids: list[str] | None = None):
        self._categories = {category.category_id: category for category in categories}
        self._router_ids = list(router_ids or [])

    def get(self, category_id: str):
        return self._categories.get(category_id)

    def list_all(self):
        return list(self._categories.values())

    def list_ids(self):
        return list(self._categories)

    def routing_evidence(self, text: str):
        return [{"category_id": value, "score": 1.0, "authoritative": False} for value in self._router_ids]


def _resolver(registry: _Registry) -> CategoryIdentityResolver:
    return CategoryIdentityResolver(
        settings_manager=SimpleNamespace(settings=Settings()),
        database=None,
        category_registry=registry,
        metadata_clients={},
    )


@pytest.mark.asyncio
async def test_strong_hinted_tv_metadata_prevents_unrelated_book_audio_probes() -> None:
    tv = _Category("tv", metadata=[{
        "category_id": "tv", "title": "Silo", "source": "tmdb_tv", "base_score": 0.24,
    }])
    ebook = _Category("ebooks", metadata=[{
        "category_id": "ebooks", "title": "Silo", "source": "open_library", "base_score": 0.2,
    }])
    audiobook = _Category("audiobooks", metadata=[{
        "category_id": "audiobooks", "title": "Silo", "source": "librivox", "base_score": 0.2,
    }])
    result = await _resolver(_Registry([tv, ebook, audiobook], router_ids=["tv"])).resolve(
        "Silo",
        category_hint="tv",
        request_text="download the available episodes of the latest season of Silo",
    )
    assert result["resolved"] is True
    assert result["category_id"] == "tv"
    assert result["source"] == "hint_selected_category_metadata"
    assert tv.metadata_calls == 1
    assert tv.web_calls == 0
    assert ebook.metadata_calls == 0
    assert audiobook.metadata_calls == 0


@pytest.mark.asyncio
async def test_hinted_tv_uses_tv_owned_web_fallback_before_other_categories() -> None:
    tv = _Category("tv", web=[{
        "category_id": "tv", "title": "Silo", "source": "web_tv_identity_fallback", "base_score": 0.20,
    }])
    ebook = _Category("ebooks", metadata=[{
        "category_id": "ebooks", "title": "Silo", "source": "open_library", "base_score": 0.2,
    }])
    result = await _resolver(_Registry([tv, ebook], router_ids=["tv"])).resolve(
        "Silo", category_hint="tv", request_text="last season of Silo"
    )
    assert result["resolved"] is True
    assert result["category_id"] == "tv"
    assert result["source"] == "hint_selected_category_web"
    assert tv.metadata_calls == 1
    assert tv.web_calls == 1
    assert ebook.metadata_calls == 0


@pytest.mark.asyncio
async def test_tv_web_identity_requires_trusted_or_independent_corroboration() -> None:
    class Collector:
        async def collect(self, query, **kwargs):
            return {
                "ok": True,
                "fallback_used": False,
                "hits": [{
                    "title": "Silo TV series episodes",
                    "snippet": "Silo is a television series with seasons and episodes.",
                    "url": "https://www.imdb.com/title/tt14688458/",
                }],
                "pages": [],
            }

    tv = TvShowCategory()
    rows = await tv.identify_agent_item_via_web(
        "Silo",
        settings=Settings(),
        metadata_clients={"web_identity_search": Collector()},
    )
    assert len(rows) == 1
    assert rows[0]["category_id"] == "tv"
    assert rows[0]["source"] == "web_tv_identity_fallback"


@pytest.mark.asyncio
async def test_untrusted_single_web_hit_does_not_authorize_tv_identity() -> None:
    class Collector:
        async def collect(self, query, **kwargs):
            return {
                "ok": True,
                "fallback_used": False,
                "hits": [{
                    "title": "Silo series episodes",
                    "snippet": "A series with seasons.",
                    "url": "https://random.invalid/silo",
                }],
                "pages": [],
            }

    rows = await TvShowCategory().identify_agent_item_via_web(
        "Silo", settings=Settings(), metadata_clients={"web_identity_search": Collector()},
    )
    assert rows == []


def test_terminal_category_resolution_question_stops_model_loop() -> None:
    payload = {
        "error_code": "category_resolution_required",
        "clarification_required": True,
        "clarification_question": "What kind of content is 'Silo'?",
    }
    message = {"role": "tool", "content": json.dumps(payload)}
    assert StreamingAgentLoopExecutor._terminal_clarification(message) == payload["clarification_question"]


def test_activity_monitor_links_turn_and_reports_exact_payload_usage() -> None:
    monitor = LLMActivityMonitor(max_history=5)
    messages = [{"role": "user", "content": "download Silo"}]
    tools = [{"type": "function", "function": {"name": "search_media_torrents", "parameters": {"type": "object"}}}]
    with LLMActivityContext.bind(session_id="web_test", turn_id="turn_1"):
        call_id = monitor.start_call(
            task="download", provider="local", model="gemma", messages=messages,
            tools=tools, stream=False, generation={"max_tokens": 512},
        )
    monitor.record_attempt(call_id, attempt=1, max_attempts=2, status="started")
    monitor.record_attempt(call_id, attempt=1, max_attempts=2, status="completed")
    monitor.finish_call(call_id, response={"usage": {"prompt_tokens": 321, "completion_tokens": 17, "total_tokens": 338}})
    detail = monitor.detail(call_id)
    assert detail is not None
    assert detail["session_id"] == "web_test"
    assert detail["turn_id"] == "turn_1"
    assert detail["prompt_tokens"] == 321
    assert detail["tool_schema_chars"] > 0
    assert detail["context"]["messages"] == messages
    assert detail["context"]["tools"] == tools
    assert not any(key.startswith("_") for key in detail)
    assert not any(key.startswith("_") for key in detail["attempts"][0])


class _RuntimeLLMClient:
    def resolve_task(self, task: str):
        return SimpleNamespace(
            context_limit=128_000,
            context_limit_source="endpoint_metadata",
            context_limit_reported=True,
        )


class _EmptyTools:
    def get_definitions_for_intent(self, intent):
        return []


def test_download_auto_context_is_soft_target_under_endpoint_hard_limit() -> None:
    runtime = LLMTaskRuntime(
        Settings(llm=LLMConfig(max_context_tokens=None, context_budget_percent=100)),
        _RuntimeLLMClient(),
        _EmptyTools(),
    )
    budget = runtime.context_budget_for_task("download")
    assert budget["model_context_tokens"] == 128_000
    assert budget["context_cap_source"] == "endpoint"
    assert budget["provider_call_context_tokens"] == 128_000
    assert budget["target_context_tokens"] == 32_768
    assert budget["context_target_source"] == "task_auto_target"
    assert budget["available_prompt_tokens"] < 32_768


def test_tool_schemas_are_reserved_inside_prompt_budget() -> None:
    manager = TokenBudgetManager(default_context_limit=4096)
    messages = [
        {"role": "system", "content": "system"},
        {"role": "assistant", "content": "old context " * 2000},
        {"role": "user", "content": "download Silo"},
    ]
    tools = [{
        "type": "function",
        "function": {
            "name": f"tool_{i}",
            "description": "schema detail " * 60,
            "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "x" * 200}}},
        },
    } for i in range(8)]
    trimmed = manager.trim_messages(messages, context_limit=4096, reserved_output_tokens=512, tools=tools)
    assert trimmed[-1]["content"] == "download Silo"
    assert manager.estimate_tools(tools) + 512 > 4096

    runtime = LLMTaskRuntime(Settings(), _RuntimeLLMClient(), _EmptyTools())
    with pytest.raises(LLMPayloadBudgetError, match="stopped before provider I/O"):
        runtime._audit_payload_budget(
            "download",
            trimmed,
            tools,
            {
                "reserved_output_tokens": 512,
                "provider_call_context_tokens": 4096,
                "context_cap_source": "test",
            },
        )


class _BlockingAssistant:
    def __init__(self):
        self.cancelled = False

    async def preflight_intent_for_chat_status(self, prompt, **kwargs):
        return Intent.DOWNLOAD

    async def run_stream(self, prompt, **kwargs):
        try:
            yield "working"
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    def format_chat_error(self, operation, exc):
        return f"error: {exc}"


def _web_app(assistant, *, monitor=None):
    settings = Settings(
        llm=LLMConfig(model="test", api_key="test"),
        tracked_items=[],
        download_dir="/tmp/test",
        web_password_hash=None,
        setup_complete=True,
        trakt_client_id="",
    )
    mgr = MagicMock()
    mgr.settings = settings
    downloader = MagicMock()
    downloader.set_stats_callback.return_value = None
    return create_app(
        settings_manager=mgr,
        db=AsyncMock(),
        assistant=assistant,
        downloader=downloader,
        notifications=MagicMock(),
        auth_service=AuthService(secret_key="test-secret"),
        llm_manager=MagicMock(),
        scanner=AsyncMock(),
        conversation_manager=MagicMock(),
        behavior_tracker=MagicMock(),
        suggestion_compiler=AsyncMock(),
        recommender=MagicMock(),
        release_group_tracker=MagicMock(),
        comms_registry=MagicMock(),
        torrent_racer=MagicMock(),
        browser_runtime=MagicMock(),
        jackett_manager=MagicMock(),
        scheduler=AsyncMock(),
        supervisor=MagicMock(),
        llm_activity_monitor=monitor,
    )


def test_websocket_rejects_second_turn_and_cancels_active_turn() -> None:
    assistant = _BlockingAssistant()
    with TestClient(_web_app(assistant)) as client:
        with client.websocket_connect("/ws/chat") as ws:
            ws.send_json({"type": "message", "message": "download Silo", "session_id": "s", "turn_id": "one"})
            assert ws.receive_json()["type"] == "started"
            token = ws.receive_json()
            assert token == {"type": "token", "content": "working", "turn_id": "one"}
            ws.send_json({"type": "message", "message": "another", "session_id": "s", "turn_id": "two"})
            busy = ws.receive_json()
            assert busy["type"] == "busy"
            assert busy["turn_id"] == "one"
            ws.send_json({"type": "cancel", "session_id": "s", "turn_id": "one"})
            cancelled = ws.receive_json()
            assert cancelled["type"] == "cancelled"
            assert cancelled["turn_id"] == "one"
    assert assistant.cancelled is True


def test_llm_activity_endpoint_and_frontend_contracts() -> None:
    monitor = LLMActivityMonitor(max_history=5)
    call_id = monitor.start_call(
        task="chat", provider="local", model="test", messages=[{"role": "user", "content": "hi"}],
        tools=None, stream=False,
    )
    monitor.finish_call(call_id, response={"usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6}})
    with TestClient(_web_app(_BlockingAssistant(), monitor=monitor)) as client:
        summary = client.get("/api/system/llm-activity").json()
        detail = client.get(f"/api/system/llm-activity/{call_id}").json()
    assert summary["last_call"]["call_id"] == call_id
    assert "context" not in summary["last_call"]
    assert detail["call"]["context"]["messages"][0]["content"] == "hi"

    chat_js = Path("src/web/static/js/components/chatController.js").read_text()
    panel_js = Path("src/web/static/js/components/llmActivityPanel.js").read_text()
    html = Path("src/web/templates/base.html").read_text()
    css = Path("src/web/static/css/style.css").read_text()
    assert "type: 'cancel'" in chat_js
    assert "this.input.disabled = this.isBusy" in chat_js
    assert "Stop current request" in chat_js
    assert "/api/system/llm-activity" in panel_js
    assert "Messages sent" in panel_js and "Tool schemas sent" in panel_js
    assert "brand-status-row" in html
    assert ".brand-status-row" in css and "white-space: nowrap" in css


@pytest.mark.asyncio
async def test_book_metadata_fallback_keeps_ia_and_librivox_probes_well_formed() -> None:
    """Legacy config drift must not turn optional book probes into exceptions."""
    calls: list[tuple[str, str, dict]] = []

    class Context:
        category = SimpleNamespace(category_id="audiobooks", media_kind="audiobook")

        async def json(self, provider, url, *, params=None):
            calls.append((provider, url, dict(params or {})))
            return {"response": {"docs": []}} if provider == "internet_archive" else {"books": []}

        def secret(self, provider, key):
            return None

    providers = BookMetadataProviders(Context())
    assert await providers.internet_archive("Silo", 3) == []
    assert await providers.librivox("Silo", 3) == []
    assert 'mediatype:(audio)' in calls[0][2]["q"]
    assert calls[1][1].endswith('/api/feed/audiobooks/')
