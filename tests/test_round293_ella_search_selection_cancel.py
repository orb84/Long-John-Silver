"""Round 293 regressions for Ella Enchanted search, selection, and cancellation."""
from __future__ import annotations

import asyncio
from pathlib import Path

import src
from types import SimpleNamespace

import pytest

from src.ai.chat_presenter import AgentChatPresenter
from src.ai.error_presenter import AgentErrorPresenter
from src.ai.tool_contracts import ToolContractValidator
from src.ai.tool_executor import ToolCallExecutor
from src.ai.tools.queue_download_support import QueueDownloadRequest, QueueDownloadService
from src.ai.tools.search_workspace import SelectionPolicyAnnotator
from src.core.categories.movie import MovieCategory
from src.core.categories.title_authority import CategoryTitleAuthority
from src.core.models import ToolExecutionContext
from src.web.chat_turn_registry import ChatTurnRegistry


PROJECT_ROOT = Path(src.__file__).resolve().parents[1]


class _Result(SimpleNamespace):
    pass


@pytest.mark.asyncio
async def test_ella_explicit_italian_hit_stops_movie_query_ladder_immediately() -> None:
    movie = MovieCategory()
    item = movie.create_item(
        "Ella Enchanted",
        year=2004,
        language="Italian",
        metadata={
            "title": "Ella Enchanted",
            "year": 2004,
            "alternative_titles": ["Ella - Den förtrollade"],
            "localized_titles": [
                {"language": "it", "title": "Ella Enchanted"},
                {"language": "sv", "title": "Ella - Den förtrollade"},
            ],
        },
    )
    calls: list[str] = []

    class Aggregator:
        async def search(self, query: str, **kwargs):
            calls.append(query)
            return [
                _Result(
                    title="Ella Enchanted 2004 BDMux ITA ENG 1080p x265 Paso77.mkv",
                    magnet="magnet:?xt=urn:btih:" + "a" * 40,
                    source="fixture",
                    seeders=1,
                    size_bytes=5_798_205_952,
                )
            ]

    results, query_summary = await movie.search_agent_candidates(
        item,
        language="Italian",
        context=SimpleNamespace(aggregator=Aggregator(), metadata_enricher=None),
    )

    assert len(results) == 1
    assert calls == ["Ella Enchanted 2004 ITA"]
    assert "Ella - Den förtrollade" not in query_summary


def test_explicit_italian_query_titles_exclude_unrelated_locales() -> None:
    movie = MovieCategory()
    item = movie.create_item(
        "Ella Enchanted",
        year=2004,
        metadata={
            "title": "Ella Enchanted",
            "alternative_titles": ["Ella - Den förtrollade"],
            "localized_titles": [
                {"language": "it", "title": "Ella Enchanted"},
                {"language": "sv", "title": "Ella - Den förtrollade"},
            ],
        },
    )
    titles = CategoryTitleAuthority.query_titles_for_item(
        item,
        preferred_language="Italian",
        strict_preferred_language=True,
    )
    assert "Ella Enchanted" in titles
    assert "Ella - Den förtrollade" not in titles


def test_optional_null_tool_argument_is_omitted_not_rejected() -> None:
    schema = {
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "target_size_gb": {"type": "number"},
            },
            "required": ["name"],
        }
    }
    result = ToolContractValidator().validate(
        tool_name="search_media_torrents",
        arguments={"name": "Ella Enchanted", "target_size_gb": None},
        schema=schema,
    )
    assert result.ok is True
    assert result.arguments == {"name": "Ella Enchanted"}

    missing = ToolContractValidator().validate(
        tool_name="search_media_torrents",
        arguments={"name": None},
        schema=schema,
    )
    assert missing.ok is False
    assert missing.error_code == "MISSING_REQUIRED_ARGUMENT"


class _Definition:
    parameters = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }


class _ToolRegistry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def get_tool_definition(self, name: str):
        return _Definition() if name == "search_media_torrents" else None

    async def execute(self, name: str, arguments: dict, context=None):
        self.calls.append((name, arguments))
        return {"ok": True, "torrent_candidate_count": 1}

    def get_definitions(self, allowed):
        return []


@pytest.mark.asyncio
async def test_provider_channel_suffix_does_not_block_real_search_tool() -> None:
    registry = _ToolRegistry()
    executor = ToolCallExecutor(registry)  # type: ignore[arg-type]
    message, _ = await executor.execute_tool_call(
        "search_media_torrents<|channel|>",
        {"name": "Ella Enchanted"},
        "call-1",
        {"search_media_torrents"},
        ToolExecutionContext(user_prompt="find Ella Enchanted in Italian"),
    )
    assert registry.calls == [("search_media_torrents", {"name": "Ella Enchanted"})]
    assert message["name"] == "search_media_torrents"


def _ella_candidate(*, candidate_id: str = "fcb06ea0d210996d", hard: bool = False) -> dict:
    return {
        "candidate_id": candidate_id,
        "title": "Ella Enchanted 2004 BDMux ITA ENG 1080p x265 Paso77.mkv",
        "magnet": "magnet:?xt=urn:btih:" + "b" * 40,
        "seeders": 1,
        "languages": ["Italian", "English"],
        "auto_queue_allowed": False,
        "manual_confirmation_reasons": ["very low seeders (1)"],
        "hard_queue_blockers": ["wrong requested language"] if hard else [],
        "unit_descriptor": {
            "granularity": "item",
            "label": "Ella Enchanted",
            "stable_key": "movie:ella enchanted:2004",
            "coordinates": {"title": "Ella Enchanted", "year": 2004},
        },
        "category_id": "movie",
    }


def _queue_request(*, prompt: str, confirmed: bool = False) -> QueueDownloadRequest:
    raw = {"candidate_id": "fcb06ea0d210996d", "result_set_id": "r1"}
    if confirmed:
        raw["confirmed"] = True
    return QueueDownloadRequest(
        session_id="session",
        magnet=None,
        name=None,
        season=None,
        episode=None,
        option_index=None,
        candidate_ids=["fcb06ea0d210996d"],
        result_set_id="r1",
        category_id="movie",
        estimated_size_bytes=None,
        selected_torrent_title="",
        selected_source_seeders=None,
        requested_priority="high",
        raw_arguments=raw,
        user_prompt=prompt,
    )


class _Scheduler:
    _categories = None

    async def queue_download(self, **kwargs):
        return {"status": "queued", "download_id": "dl-1"}


def _entry(candidate: dict | None = None) -> dict:
    return {
        "candidate_id": "fcb06ea0d210996d",
        "candidate": candidate or _ella_candidate(),
        "cache_data": {
            "name": "Ella Enchanted",
            "category_id": "movie",
            "awaiting_user_choice": True,
            "origin_user_prompt": "Please find me a movie called Ella Enchanted in italian",
        },
    }


def test_later_stable_candidate_selection_confirms_only_soft_warning() -> None:
    service = QueueDownloadService(_Scheduler())  # type: ignore[arg-type]
    selected = service._candidate_queue_payload(
        _queue_request(prompt="1"), _entry(), 0, 1,
    )
    assert "error" not in selected
    assert selected["scheduler_kwargs"]["torrent_title"].startswith("Ella Enchanted 2004")

    same_turn = service._candidate_queue_payload(
        _queue_request(prompt="Please find me a movie called Ella Enchanted in italian"),
        _entry(), 0, 1,
    )
    assert same_turn.get("confirmation_required") is True


def test_hard_constraint_is_never_overridden_by_selection() -> None:
    service = QueueDownloadService(_Scheduler())  # type: ignore[arg-type]
    payload = service._candidate_queue_payload(
        _queue_request(prompt="1"), _entry(_ella_candidate(hard=True)), 0, 1,
    )
    assert payload.get("policy_blocked") is True
    assert payload.get("fallback_eligible") is False


def test_confirmation_does_not_transfer_to_operational_fallback_candidate() -> None:
    service = QueueDownloadService(_Scheduler())  # type: ignore[arg-type]
    alternate = _entry(_ella_candidate(candidate_id="alternate"))
    alternate["candidate_id"] = "alternate"
    payload = service._candidate_queue_payload(
        _queue_request(prompt="1"), alternate, 0, 1, allow_manual_override=False,
    )
    assert payload.get("confirmation_required") is True
    assert payload.get("fallback_eligible") is False


@pytest.mark.asyncio
async def test_confirmation_policy_failure_does_not_spray_fallback_candidates() -> None:
    service = QueueDownloadService(_Scheduler())  # type: ignore[arg-type]
    fallback_called = False

    async def forbidden_fallback(*args, **kwargs):
        nonlocal fallback_called
        fallback_called = True
        return None

    service._queue_fallback_for_failed_entry = forbidden_fallback  # type: ignore[method-assign]
    response = await service._queue_resolved_entries(
        _queue_request(prompt="Please find me a movie called Ella Enchanted in italian"),
        [_entry()],
    )
    assert response.get("confirmation_required") is True
    assert fallback_called is False


def test_explicit_language_workspace_suppresses_unknown_language_alternative() -> None:
    italian = {
        "title": "Ella Enchanted 2004 BDMux ITA ENG 1080p x265 Paso77.mkv",
        "seeders": 1,
        "languages": ["Italian", "English"],
    }
    unknown = {
        "title": "Ella Enchanted 2004 1080p BluRay x264-OFT",
        "seeders": 47,
        "languages": [],
    }
    candidates = [italian, unknown]
    SelectionPolicyAnnotator.annotate(
        candidates, preferred_language="Italian", language_is_explicit=True,
    )
    visible = SelectionPolicyAnnotator.narrow_to_explicit_language_evidence(
        candidates, language_is_explicit=True,
    )
    assert visible == [italian]
    assert italian["manual_confirmation_reasons"] == ["very low seeders (1)"]
    assert italian["hard_queue_blockers"] == []
    assert unknown["hard_queue_blockers"]


@pytest.mark.asyncio
async def test_chat_turn_cancel_waits_for_server_task_to_settle() -> None:
    registry = ChatTurnRegistry()
    finally_ran = asyncio.Event()

    async def run() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            finally_ran.set()

    started, active = await registry.start("session", "turn-1", run)
    assert started is True
    await asyncio.sleep(0)
    matched, settled = await registry.cancel_and_wait("session", "turn-1", timeout_seconds=1.0)
    assert matched is active
    assert settled is True
    assert finally_ran.is_set()
    assert active.task.done()
    assert await registry.active("session") is None


def test_rest_stop_requires_server_cancellation_before_local_abort() -> None:
    js = (PROJECT_ROOT / "src/web/static/js/components/chatController.js").read_text(encoding="utf-8")
    assert "if (data?.cancellation_requested && data?.settled)" in js
    assert "this.httpAbortController.abort();" in js
    assert "Do not abort the local request when the cancellation endpoint" in js
    app = (PROJECT_ROOT / "src/web/app.py").read_text(encoding="utf-8")
    assert '@app.post("/api/chat/cancel")' in app
    assert "cancel_and_wait" in app
    assert "cancel_still_unwinding" in app


def test_simple_media_search_skips_advisory_planner_and_llm_progress_call() -> None:
    assistant = (PROJECT_ROOT / "src/ai/assistant.py").read_text(encoding="utf-8")
    assert "def _uses_live_media_acquisition_loop" in assistant
    assert 'ctx.intent == Intent.SEARCH' in assistant
    assert '"search_media_torrents" in (ctx.allowed_tool_names or set())' in assistant
    runner = (PROJECT_ROOT / "src/ai/chat_session_runner.py").read_text(encoding="utf-8")
    progress = runner[runner.index("async def _progress_message"):runner.index("async def _status_intent")]
    assert "generate_progress_message" not in progress
    assert "Searching the configured sources" in progress


def test_deterministic_queue_errors_and_receipts_use_plain_language() -> None:
    error = AgentErrorPresenter().queue_failure("No candidates were queued.")
    assert "cargo" not in error.casefold()
    assert "parrot" not in error.casefold()
    assert "captain" not in error.casefold()
    assert "download was not queued" in error.casefold()

    receipt = AgentChatPresenter().batch_queue_result(
        item_name="Ella Enchanted",
        queued=[{"title": "Ella Enchanted 2004 BDMux ITA ENG 1080p"}],
        failed=[],
    )
    assert "cargo" not in receipt.casefold()
    assert "captain" not in receipt.casefold()
    assert "queued 1 download" in receipt.casefold()


def test_fresh_acquisition_starts_new_goal_but_short_result_selection_continues() -> None:
    from src.ai.download_context_policy import DownloadContextPolicy
    from src.core.models import Intent

    assert DownloadContextPolicy.should_start_fresh_goal(
        "Please download the movie Ella Enchanted in italian", Intent.DOWNLOAD,
    ) is True
    assert DownloadContextPolicy.should_start_fresh_goal(
        "Please find me a movie called Ella Enchanted in italian", Intent.SEARCH,
    ) is True
    assert DownloadContextPolicy.should_start_fresh_goal("search harder", Intent.SEARCH) is False
    assert DownloadContextPolicy.should_start_fresh_goal("1", Intent.DOWNLOAD) is False


@pytest.mark.asyncio
async def test_selected_candidate_operational_failure_does_not_substitute_an_alternate() -> None:
    class _FailingScheduler:
        _categories = None

        async def queue_download(self, **kwargs):
            return {"error": "download backend rejected the exact candidate"}

    service = QueueDownloadService(_FailingScheduler())  # type: ignore[arg-type]
    fallback_called = False

    async def forbidden_fallback(*args, **kwargs):
        nonlocal fallback_called
        fallback_called = True
        return None

    service._queue_fallback_for_failed_entry = forbidden_fallback  # type: ignore[method-assign]
    response = await service._queue_resolved_entries(_queue_request(prompt="1"), [_entry()])
    assert response.get("error") == "No candidates were queued."
    assert fallback_called is False


@pytest.mark.asyncio
async def test_jackett_cancellation_awaits_aggregate_and_direct_children() -> None:
    from src.search.jackett import JackettSearch

    provider = JackettSearch(
        "http://127.0.0.1:9117", "fixture", timeout=10.0,
        configured_indexers=1, enable_direct_recovery=True,
    )
    aggregate_settled = asyncio.Event()
    direct_settled = asyncio.Event()

    async def aggregate(query):
        try:
            await asyncio.Event().wait()
        finally:
            aggregate_settled.set()

    async def direct(query, *, category=None):
        try:
            await asyncio.Event().wait()
        finally:
            direct_settled.set()

    provider._search_aggregate = aggregate  # type: ignore[method-assign]
    provider._search_direct_configured_indexers = direct  # type: ignore[method-assign]
    task = asyncio.create_task(provider.search("Ella Enchanted 2004 ITA", category="movie"))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert aggregate_settled.is_set()
    assert direct_settled.is_set()
    leaking = [
        child for child in asyncio.all_tasks()
        if child is not asyncio.current_task() and not child.done()
        and child.get_name().startswith("jackett-")
    ]
    assert not leaking


@pytest.mark.asyncio
async def test_jackett_direct_empty_cancels_and_awaits_slow_aggregate() -> None:
    from src.search.jackett import JackettSearch

    provider = JackettSearch(
        "http://127.0.0.1:9117", "fixture", timeout=75.0,
        configured_indexers=1, enable_direct_recovery=True,
    )
    aggregate_settled = asyncio.Event()

    async def aggregate(query):
        try:
            await asyncio.Event().wait()
        finally:
            aggregate_settled.set()

    async def direct(query, *, category=None):
        await asyncio.sleep(0)
        return []

    provider._search_aggregate = aggregate  # type: ignore[method-assign]
    provider._search_direct_configured_indexers = direct  # type: ignore[method-assign]
    result = await asyncio.wait_for(
        provider.search("Ella - Den förtrollade", category="movie"), timeout=1.0,
    )
    assert result == []
    assert aggregate_settled.is_set()
    leaking = [
        child for child in asyncio.all_tasks()
        if child is not asyncio.current_task() and not child.done()
        and child.get_name().startswith("jackett-")
    ]
    assert not leaking


@pytest.mark.asyncio
async def test_turn_and_search_lifecycle_logs_make_cancellation_explicit(tmp_path) -> None:
    import json

    from src.core.operation_trace import OperationTraceContext
    from src.utils.detailed_logger import DetailedLoggingSubsystem

    logs = DetailedLoggingSubsystem(tmp_path)
    with OperationTraceContext.bind(session_id="session-293", turn_id="turn-first-ella"):
        await logs.turn_logger.log_event(
            "turn_received", session_id="session-293", turn_id="turn-first-ella",
            transport="rest", message="Please find me a movie called Ella Enchanted in italian",
            state="received",
        )
        await logs.turn_logger.log_event(
            "cancel_requested", session_id="session-293", turn_id="turn-first-ella",
            transport="rest_cancel", detail="User requested Stop/Cancel.", state="cancelling",
        )
        search_id = await logs.search_logger.begin_search(
            query="Ella Enchanted 2004 ITA", category="movie", active_providers=["JackettSearch"],
        )
        await logs.search_logger.log_search_event(
            event="torrent_search_cancelled", search_id=search_id,
            query="Ella Enchanted 2004 ITA", category="movie",
            active_providers=["JackettSearch"], elapsed_ms=17,
            detail="Owning user operation was cancelled.",
        )
        await logs.turn_logger.log_event(
            "turn_cancelled", session_id="session-293", turn_id="turn-first-ella",
            transport="rest", detail="Assistant task received cancellation and exited.", state="cancelled",
        )

    turn_rows = [json.loads(row) for row in (tmp_path / "chat_turns.jsonl").read_text().splitlines()]
    search_rows = [json.loads(row) for row in (tmp_path / "searches.jsonl").read_text().splitlines()]
    assert [row["event"] for row in turn_rows] == ["turn_received", "cancel_requested", "turn_cancelled"]
    assert all(row["turn_id"] == "turn-first-ella" for row in turn_rows)
    assert all(row["turn_elapsed_ms"] is not None for row in turn_rows)
    assert [row["event"] for row in search_rows] == ["torrent_search_started", "torrent_search_cancelled"]
    assert search_rows[0]["search_id"] == search_rows[1]["search_id"]
    assert search_rows[1]["search_elapsed_ms"] == 17


def test_application_log_format_includes_operation_lineage() -> None:
    main_source = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")
    assert "OperationTraceLogEnricher" in main_source
    assert "session={extra[session_id]} turn={extra[turn_id]}" in main_source
    diagnostics = (PROJECT_ROOT / "src/web/static/js/components/llmActivityPanel.js").read_text(encoding="utf-8")
    assert "Turn lifecycle" in diagnostics
    assert "Searches" in diagnostics
