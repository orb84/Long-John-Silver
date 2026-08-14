"""Dependency-light executable regression harness for the Round 293 incident."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import sys

from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ai.chat_presenter import AgentChatPresenter
from src.ai.download_context_policy import DownloadContextPolicy
from src.ai.error_presenter import AgentErrorPresenter
from src.ai.tool_contracts import ToolContractValidator
from src.ai.tool_executor import ToolCallExecutor
from src.ai.tools.queue_download_support import QueueDownloadRequest, QueueDownloadService
from src.ai.tools.search_workspace import SelectionPolicyAnnotator
from src.core.categories.movie import MovieCategory
from src.core.categories.title_authority import CategoryTitleAuthority
from src.core.models import Intent, ToolExecutionContext
from src.core.operation_trace import OperationTraceContext, OperationTraceLogEnricher
from src.search.jackett import JackettSearch
from src.utils.detailed_logger import DetailedLoggingSubsystem
from src.web.chat_turn_registry import ChatTurnRegistry


class Result(SimpleNamespace):
    pass


async def check_movie_search() -> None:
    movie = MovieCategory()
    item = movie.create_item(
        "Ella Enchanted", year=2004, language="Italian",
        metadata={
            "title": "Ella Enchanted", "year": 2004,
            "alternative_titles": ["Ella - Den förtrollade"],
            "localized_titles": [
                {"language": "it", "title": "Ella Enchanted"},
                {"language": "sv", "title": "Ella - Den förtrollade"},
            ],
        },
    )
    calls = []
    class Aggregator:
        async def search(self, query, **kwargs):
            calls.append(query)
            return [Result(
                title="Ella Enchanted 2004 BDMux ITA ENG 1080p x265 Paso77.mkv",
                magnet="magnet:?xt=urn:btih:" + "a" * 40,
                source="fixture", seeders=1, size_bytes=5_798_205_952,
            )]
    results, summary = await movie.search_agent_candidates(
        item, language="Italian",
        context=SimpleNamespace(aggregator=Aggregator(), metadata_enricher=None),
    )
    assert len(results) == 1
    assert calls == ["Ella Enchanted 2004 ITA"], calls
    assert "Den förtrollade" not in summary
    titles = CategoryTitleAuthority.query_titles_for_item(
        item, preferred_language="Italian", strict_preferred_language=True,
    )
    assert "Ella - Den förtrollade" not in titles


class Definition:
    parameters = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }
class Registry:
    def __init__(self): self.calls = []
    def get_tool_definition(self, name): return Definition() if name == "search_media_torrents" else None
    async def execute(self, name, arguments, context=None):
        self.calls.append((name, arguments)); return {"ok": True, "torrent_candidate_count": 1}
    def get_definitions(self, allowed): return []


async def check_tool_contracts() -> None:
    schema = {"parameters": {"type": "object", "properties": {
        "name": {"type": "string"}, "target_size_gb": {"type": "number"},
    }, "required": ["name"]}}
    result = ToolContractValidator().validate(
        tool_name="search_media_torrents",
        arguments={"name": "Ella Enchanted", "target_size_gb": None}, schema=schema,
    )
    assert result.ok and result.arguments == {"name": "Ella Enchanted"}
    registry = Registry()
    executor = ToolCallExecutor(registry)
    message, _ = await executor.execute_tool_call(
        "search_media_torrents<|channel|>", {"name": "Ella Enchanted"}, "call-1",
        {"search_media_torrents"}, ToolExecutionContext(user_prompt="find Ella in Italian"),
    )
    assert registry.calls == [("search_media_torrents", {"name": "Ella Enchanted"})]
    assert message["name"] == "search_media_torrents"


def request(prompt: str) -> QueueDownloadRequest:
    return QueueDownloadRequest(
        session_id="session", magnet=None, name=None, season=None, episode=None,
        option_index=None, candidate_ids=["fcb06ea0d210996d"], result_set_id="r1",
        category_id="movie", estimated_size_bytes=None, selected_torrent_title="",
        selected_source_seeders=None, requested_priority="high",
        raw_arguments={"candidate_id": "fcb06ea0d210996d", "result_set_id": "r1"},
        user_prompt=prompt,
    )


def candidate(candidate_id="fcb06ea0d210996d", hard=False):
    return {
        "candidate_id": candidate_id,
        "title": "Ella Enchanted 2004 BDMux ITA ENG 1080p x265 Paso77.mkv",
        "magnet": "magnet:?xt=urn:btih:" + "b" * 40,
        "seeders": 1, "languages": ["Italian", "English"],
        "auto_queue_allowed": False,
        "manual_confirmation_reasons": ["very low seeders (1)"],
        "hard_queue_blockers": ["wrong requested language"] if hard else [],
        "unit_descriptor": {"granularity": "item", "label": "Ella Enchanted", "stable_key": "movie:ella enchanted:2004", "coordinates": {"title": "Ella Enchanted", "year": 2004}},
        "category_id": "movie",
    }


def entry(c=None, candidate_id="fcb06ea0d210996d"):
    return {
        "candidate_id": candidate_id, "candidate": c or candidate(candidate_id),
        "cache_data": {
            "name": "Ella Enchanted", "category_id": "movie", "awaiting_user_choice": True,
            "origin_user_prompt": "Please find me a movie called Ella Enchanted in italian",
        },
    }


class Scheduler:
    _categories = None
    async def queue_download(self, **kwargs): return {"status": "queued", "download_id": "dl-1"}


async def check_selection() -> None:
    service = QueueDownloadService(Scheduler())
    chosen = service._candidate_queue_payload(request("1"), entry(), 0, 1)
    assert "error" not in chosen
    same_turn = service._candidate_queue_payload(
        request("Please find me a movie called Ella Enchanted in italian"), entry(), 0, 1,
    )
    assert same_turn.get("confirmation_required") is True
    hard = service._candidate_queue_payload(request("1"), entry(candidate(hard=True)), 0, 1)
    assert hard.get("policy_blocked") is True
    alt = entry(candidate("alternate"), candidate_id="alternate")
    alternate = service._candidate_queue_payload(
        request("1"), alt, 0, 1, allow_manual_override=False,
    )
    assert alternate.get("confirmation_required") is True

    fallback_called = False
    async def forbidden(*args, **kwargs):
        nonlocal fallback_called
        fallback_called = True
    service._queue_fallback_for_failed_entry = forbidden
    response = await service._queue_resolved_entries(
        request("Please find me a movie called Ella Enchanted in italian"), [entry()],
    )
    assert response.get("confirmation_required") is True
    assert not fallback_called

    class FailingScheduler:
        _categories = None
        async def queue_download(self, **kwargs):
            return {"error": "download backend rejected the exact candidate"}
    selected_service = QueueDownloadService(FailingScheduler())
    selected_fallback_called = False
    async def selected_forbidden(*args, **kwargs):
        nonlocal selected_fallback_called
        selected_fallback_called = True
        return None
    selected_service._queue_fallback_for_failed_entry = selected_forbidden
    selected_response = await selected_service._queue_resolved_entries(request("1"), [entry()])
    assert selected_response.get("error") == "No candidates were queued."
    assert not selected_fallback_called, "an explicitly selected candidate must not spray unrelated fallbacks"

    italian = {"title": "Ella Enchanted 2004 BDMux ITA ENG 1080p", "seeders": 1, "languages": ["Italian", "English"]}
    unknown = {"title": "Ella Enchanted 2004 1080p BluRay x264-OFT", "seeders": 47, "languages": []}
    rows = [italian, unknown]
    SelectionPolicyAnnotator.annotate(rows, preferred_language="Italian", language_is_explicit=True)
    assert SelectionPolicyAnnotator.narrow_to_explicit_language_evidence(rows, language_is_explicit=True) == [italian]


async def check_cancel() -> None:
    registry = ChatTurnRegistry()
    settled_flag = asyncio.Event()
    async def run():
        try: await asyncio.Event().wait()
        finally: settled_flag.set()
    started, active = await registry.start("session", "turn", run)
    assert started
    await asyncio.sleep(0)
    matched, settled = await registry.cancel_and_wait("session", "turn", timeout_seconds=1.0)
    assert matched is active and settled and settled_flag.is_set() and active.task.done()


async def check_provider_cancellation() -> None:
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

    provider._search_aggregate = aggregate
    provider._search_direct_configured_indexers = direct
    task = asyncio.create_task(provider.search("Ella Enchanted 2004 ITA", category="movie"))
    await asyncio.sleep(0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("Jackett parent search did not propagate cancellation")
    assert aggregate_settled.is_set() and direct_settled.is_set()
    await asyncio.sleep(0)
    leaking = [
        child for child in asyncio.all_tasks()
        if child is not asyncio.current_task() and not child.done()
        and child.get_name().startswith("jackett-")
    ]
    assert not leaking, leaking


async def check_direct_empty_does_not_leave_aggregate_running() -> None:
    """A verified-empty manual-parity probe must terminate the slow aggregate child."""
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

    provider._search_aggregate = aggregate
    provider._search_direct_configured_indexers = direct
    started = asyncio.get_running_loop().time()
    result = await asyncio.wait_for(
        provider.search("Ella - Den förtrollade", category="movie"), timeout=1.0,
    )
    elapsed = asyncio.get_running_loop().time() - started
    assert result == []
    assert aggregate_settled.is_set()
    assert elapsed < 1.0
    await asyncio.sleep(0)
    leaking = [
        child for child in asyncio.all_tasks()
        if child is not asyncio.current_task() and not child.done()
        and child.get_name().startswith("jackett-")
    ]
    assert not leaking, leaking


async def check_turn_and_search_logging() -> None:
    with TemporaryDirectory(prefix="ljs-round293-log-") as tmp:
        logs = DetailedLoggingSubsystem(tmp)
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
                transport="rest", detail="Assistant task received cancellation and exited.",
                state="cancelled",
            )
        turn_rows = [json.loads(row) for row in (Path(tmp) / "chat_turns.jsonl").read_text().splitlines()]
        search_rows = [json.loads(row) for row in (Path(tmp) / "searches.jsonl").read_text().splitlines()]
        assert [row["event"] for row in turn_rows] == ["turn_received", "cancel_requested", "turn_cancelled"]
        assert all(row["turn_id"] == "turn-first-ella" for row in turn_rows)
        assert all(row["turn_elapsed_ms"] is not None for row in turn_rows)
        assert [row["event"] for row in search_rows] == ["torrent_search_started", "torrent_search_cancelled"]
        assert all(row["turn_id"] == "turn-first-ella" for row in search_rows)
        assert search_rows[0]["search_id"] == search_rows[1]["search_id"]
        assert search_rows[1]["search_elapsed_ms"] == 17

        app_log = Path(tmp) / "trace.log"
        sink = logger.add(
            app_log,
            filter=OperationTraceLogEnricher(),
            format="session={extra[session_id]} turn={extra[turn_id]} elapsed_ms={extra[turn_elapsed_ms]} - {message}",
        )
        try:
            with OperationTraceContext.bind(session_id="session-293", turn_id="turn-loguru"):
                logger.info("provider detail")
        finally:
            logger.remove(sink)
        row = app_log.read_text()
        assert "session=session-293 turn=turn-loguru" in row and "provider detail" in row


def check_goal_freshness() -> None:
    # The same existing acquisition freshness guard owns SEARCH and DOWNLOAD:
    # a concrete new search starts clean, while terse refinements/selections
    # continue the current structured result set.
    assert DownloadContextPolicy.should_start_fresh_goal(
        "Please find me a movie called Ella Enchanted in italian", Intent.SEARCH
    ) is True
    assert DownloadContextPolicy.should_start_fresh_goal("search harder", Intent.SEARCH) is False
    assert DownloadContextPolicy.should_start_fresh_goal("1", Intent.DOWNLOAD) is False


def check_source_contracts() -> None:
    assistant = Path("src/ai/assistant.py").read_text()
    assert "def _uses_live_media_acquisition_loop" in assistant
    assert 'ctx.intent == Intent.SEARCH' in assistant
    runner = Path("src/ai/chat_session_runner.py").read_text()
    progress = runner[runner.index("async def _progress_message"):runner.index("async def _status_intent")]
    assert "generate_progress_message" not in progress
    app = Path("src/web/app.py").read_text()
    js = Path("src/web/static/js/components/chatController.js").read_text()
    assert '@app.post("/api/chat/cancel")' in app and "cancel_and_wait" in app
    assert "if (data?.cancellation_requested && data?.settled)" in js
    assert "Do not abort the local request when the cancellation endpoint" in js
    assert "if (data?.cancelled)" in js
    diagnostics = Path("src/web/static/js/components/llmActivityPanel.js").read_text()
    assert "Turn lifecycle" in diagnostics and "Searches" in diagnostics
    aggregator = Path("src/search/aggregator.py").read_text()
    assert "torrent_search_cancelled" in aggregator and "begin_search" in aggregator
    detailed_logger = Path("src/utils/detailed_logger.py").read_text()
    assert '"terminal_state": "completed"' in detailed_logger
    err = AgentErrorPresenter().queue_failure("No candidates were queued.")
    assert all(word not in err.casefold() for word in ("cargo", "parrot", "captain"))
    receipt = AgentChatPresenter().batch_queue_result(
        item_name="Ella Enchanted", queued=[{"title": "Ella Enchanted 2004 BDMux ITA ENG"}], failed=[]
    )
    assert "queued 1 download" in receipt.casefold() and "cargo" not in receipt.casefold()


async def main() -> None:
    await check_movie_search()
    await check_tool_contracts()
    await check_selection()
    await check_cancel()
    await check_provider_cancellation()
    await check_direct_empty_does_not_leave_aggregate_running()
    await check_turn_and_search_logging()
    check_goal_freshness()
    check_source_contracts()
    print("ROUND293_ELLA_SEARCH_SELECTION_CANCEL_PASS")


if __name__ == "__main__":
    asyncio.run(main())
