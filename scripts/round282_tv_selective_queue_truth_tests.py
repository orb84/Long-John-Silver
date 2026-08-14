#!/usr/bin/env python3
"""Round 282 regressions for TV selective queueing and action truth.

Covers the production failure where a requested TV episode remained absent from
canonical library state, an old completed transfer row blocked every retry, a
season bundle was incorrectly treated as requiring manual pre-inspection even
though metadata-time file priorities were supported, language preference was
reported as release fact, and the chat response contradicted a failed queue
receipt.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
import types
from types import SimpleNamespace
from typing import Any, AsyncIterator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Optional runtime dependency is not needed by these isolated regressions.
sys.modules.setdefault("aiosqlite", types.SimpleNamespace(Connection=object, Row=dict, Cursor=object))

from src.ai.streaming_agent_loop import StreamingAgentLoopExecutor
from src.ai.tool_outcome_guard import ToolOutcomeLedger
from src.ai.tools.search_workspace import SelectionPolicyAnnotator
from src.core.categories.tv import TvShowCategory
from src.core.download_completion_authority import CompletedDownloadAuthority, CompletedDownloadDecision
from src.core.download_import_identity import _find_duplicate_import_context
from src.core.downloader import DownloadManager
from src.core.models import (
    DownloadImportContext,
    DownloadItem,
    DownloadPriority,
    DownloadStatus,
    SearchResult,
)
from src.core.scheduler import MediaScheduler

TV_CATEGORY_ID = "t" + "v"


class Check:
    """Collect script-style assertion failures into one readable report."""

    def __init__(self) -> None:
        self.failures: list[str] = []

    def ok(self, condition: bool, message: str) -> None:
        if not condition:
            self.failures.append(message)

    def finish(self) -> None:
        if self.failures:
            print("Round 282 TV selective queue truth failures:")
            for failure in self.failures:
                print(f" - {failure}")
            raise SystemExit(1)
        print("round282_tv_selective_queue_truth_tests: PASS")


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def episode_descriptor(category: TvShowCategory, episode: int = 10) -> dict[str, Any]:
    return category.unit_descriptor_from_agent_args(season=1, episode=episode)


def import_context(category: TvShowCategory, *, episode: int = 10, bundle: dict[str, Any] | None = None) -> DownloadImportContext:
    descriptor = episode_descriptor(category, episode)
    return DownloadImportContext.from_selection(
        category_id="tv",
        item_id="Example Series",
        item_name="Example Series",
        season=1,
        episode=episode,
        unit_descriptor=descriptor,
        language="Italian",
        release_title="Example.Series.S01.ITA.1080p",
        metadata={"provider": "tmdb", "provider_id": "1001", "provider_media_type": "tv"},
        candidate={"title": "Example.Series.S01.ITA.1080p", "bundle_context": bundle or {}},
    )


def test_tv_bundle_publishes_supported_metadata_selection(check: Check) -> None:
    category = TvShowCategory()
    result = SearchResult(
        title="Example Series S01E01-E10 ITA 1080p WEB-DL",
        size="8 GB",
        seeders=42,
        magnet="magnet:?xt=urn:btih:bundle",
        source="fixture",
    )
    context = category.torrent_bundle_candidate_context(
        result,
        item=category.create_item("Example Series"),
        unit_label="S01E10",
    )
    check.ok(bool(context and context.get("contains_requested_unit")), f"bundle should prove S01E10 coverage: {context!r}")
    policy = (context or {}).get("selective_queue_policy") or {}
    check.ok(policy.get("status") == "supported", f"TV should publish metadata-time selective capability: {policy!r}")

    candidate = {
        "candidate_id": "bundle",
        "title": result.title,
        "seeders": 42,
        "languages": ["Italian"],
        "bundle_context": context,
    }
    SelectionPolicyAnnotator.annotate([candidate], preferred_language="Italian", language_is_explicit=True)
    check.ok(candidate.get("auto_queue_allowed") is True, f"supported selective bundle should remain queueable: {candidate!r}")
    check.ok((candidate.get("selective_queue") or {}).get("status") == "supported", "candidate should expose category selective capability")
    check.ok(not candidate.get("selection_blockers"), f"supported selective bundle must not retain an inspection blocker: {candidate!r}")


def test_tv_bundle_that_does_not_prove_target_stays_blocked(check: Check) -> None:
    category = TvShowCategory()
    result = SearchResult(
        title="Example Series S01E01-E08 ITA 1080p WEB-DL",
        size="6 GB",
        seeders=40,
        magnet="magnet:?xt=urn:btih:shortbundle",
        source="fixture",
    )
    context = category.torrent_bundle_candidate_context(
        result,
        item=category.create_item("Example Series"),
        unit_label="S01E10",
    )
    policy = (context or {}).get("selective_queue_policy") or {}
    check.ok(policy.get("status") == "requires_inspection", f"range ending at E08 must not claim E10 coverage: {context!r}")
    candidate = {
        "candidate_id": "shortbundle",
        "title": result.title,
        "seeders": 40,
        "languages": ["Italian"],
        "bundle_context": context,
    }
    SelectionPolicyAnnotator.annotate([candidate], preferred_language="Italian", language_is_explicit=True)
    check.ok(candidate.get("auto_queue_allowed") is False, f"unproven target coverage should be blocked: {candidate!r}")


def test_tv_file_matcher_selects_only_requested_episode_or_season(check: Check) -> None:
    category = TvShowCategory()
    e10 = episode_descriptor(category, 10)
    e09_file = episode_descriptor(category, 9)
    e10_file = episode_descriptor(category, 10)
    common = {"parsed": None, "target_descriptors": [e10]}
    check.ok(
        category.torrent_file_matches_target(file_path="Example.Series.S01E10.mkv", file_descriptor=e10_file, **common),
        "requested E10 payload should be selected",
    )
    check.ok(
        not category.torrent_file_matches_target(file_path="Example.Series.S01E09.mkv", file_descriptor=e09_file, **common),
        "neighboring E09 payload should remain priority zero",
    )
    season = category.unit_descriptor_from_agent_args(season=1, episode=None)
    check.ok(
        category.torrent_file_matches_target(
            file_path="Example.Series.S01E09.mkv",
            parsed=None,
            file_descriptor=e09_file,
            target_descriptors=[season],
        ),
        "season-scoped selection should include files from the requested season",
    )


def test_language_preference_is_not_release_evidence(check: Check) -> None:
    preferred_only = {"candidate_id": "unknown", "title": "Example Series S01E10 1080p WEB-DL", "seeders": 30}
    SelectionPolicyAnnotator.annotate([preferred_only], preferred_language="Italian", language_is_explicit=False)
    evidence = preferred_only.get("language_evidence") or {}
    check.ok(preferred_only.get("language_preference_status") == "unknown", f"untagged title should remain unknown: {preferred_only!r}")
    check.ok(evidence.get("preference_is_release_evidence") is False, f"preference must be explicitly marked non-evidence: {evidence!r}")
    check.ok(evidence.get("source") == "none", f"untagged title should have no release-language source: {evidence!r}")
    check.ok(any("not evidence" in warning for warning in preferred_only.get("selection_warnings") or []), "warning should forbid promoting preference to fact")

    explicit = {"candidate_id": "unknown-explicit", "title": "Example Series S01E10 1080p WEB-DL", "seeders": 30}
    SelectionPolicyAnnotator.annotate([explicit], preferred_language="Italian", language_is_explicit=True)
    check.ok(explicit.get("auto_queue_allowed") is False, "an explicit non-English request should block an unadvertised-language release")
    check.ok(explicit.get("language_preference_status") == "unknown", "explicit preference still must not become a factual language tag")

    multi = {"candidate_id": "multi", "title": "Example Series S01E10 MULTI 1080p WEB-DL", "seeders": 30}
    SelectionPolicyAnnotator.annotate([multi], preferred_language="Italian", language_is_explicit=True)
    check.ok(
        multi.get("language_preference_status") == "multi_language_unverified",
        f"MULTI must remain unverified for a specific Italian request: {multi!r}",
    )
    check.ok(multi.get("auto_queue_allowed") is False, "MULTI alone must not satisfy an explicit Italian request")

    provider_verified = {
        "candidate_id": "provider-verified",
        "title": "Example Series S01E10 1080p WEB-DL",
        "seeders": 30,
        "languages": ["Italian"],
    }
    SelectionPolicyAnnotator.annotate([provider_verified], preferred_language="Italian", language_is_explicit=True)
    check.ok(
        provider_verified.get("language_preference_status") == "preferred_only",
        f"provider-advertised Italian should be recognized without requiring a title token: {provider_verified!r}",
    )
    check.ok(provider_verified.get("auto_queue_allowed") is True, "verified provider language should remain queueable")


def canonical_payload(*episodes: int, provider_episodes: tuple[int, ...] = ()) -> dict[str, Any]:
    return {
        "category_id": "tv",
        "item_id": "Example Series",
        "seasons": [{
            "season_number": 1,
            "episodes": [
                {"season": 1, "episode": episode, "status": "downloaded"}
                for episode in episodes
            ],
        }],
        "computed": {
            "local_episode_keys": [f"S01E{episode:02d}" for episode in episodes],
            "provider_aired_episode_keys": [f"S01E{episode:02d}" for episode in provider_episodes],
        },
    }


class FakeCanonicalBuilder:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[tuple[str, str]] = []

    async def build(self, category_id: str, item_id: str, *, settings_item: Any | None = None) -> dict[str, Any]:
        self.calls.append((category_id, item_id))
        return self.payload


class FakeRegistry:
    def __init__(self, category: Any) -> None:
        self.category = category

    def get(self, category_id: str) -> Any:
        return self.category if category_id == TV_CATEGORY_ID else None


async def completion_decision(payload: dict[str, Any]) -> CompletedDownloadDecision:
    category = TvShowCategory()
    builder = FakeCanonicalBuilder(payload)
    authority = CompletedDownloadAuthority(
        settings_manager=SimpleNamespace(settings=SimpleNamespace(tracked_items=[])),
        category_registry=FakeRegistry(category),
        category_context_factory=lambda: SimpleNamespace(library_objects=builder),
    )
    return await authority.evaluate(
        import_context=import_context(category),
        category_id="tv",
        item_name="Example Series",
    )


def test_canonical_library_overrules_stale_complete_history(check: Check) -> None:
    absent = run(completion_decision(canonical_payload(*range(1, 10))))
    check.ok(absent.verified is True and absent.satisfied is False, f"E10 absence should be verified: {absent!r}")
    check.ok(absent.retry_completed_row is True, "verified canonical absence should admit retry past complete history")

    present = run(completion_decision(canonical_payload(*range(1, 11))))
    check.ok(present.verified is True and present.satisfied is True, f"present E10 should remain satisfied: {present!r}")
    check.ok(present.retry_completed_row is False, "present canonical unit must not revive a completed row")


def test_season_satisfaction_requires_the_full_provider_frontier(check: Check) -> None:
    category = TvShowCategory()
    descriptor = category.unit_descriptor_from_agent_args(season=1, episode=None)
    partial = category.canonical_download_satisfaction(
        canonical_payload(*range(1, 10), provider_episodes=tuple(range(1, 11))),
        descriptor,
    )
    complete = category.canonical_download_satisfaction(
        canonical_payload(*range(1, 11), provider_episodes=tuple(range(1, 11))),
        descriptor,
    )
    unknown = category.canonical_download_satisfaction(canonical_payload(*range(1, 10)), descriptor)
    check.ok(partial is False, "a season with only E01-E09 must not satisfy an E01-E10 provider frontier")
    check.ok(complete is True, "a season should be satisfied only when every provider-known aired episode is local")
    check.ok(unknown is None, "season satisfaction must stay unknown when the provider frontier is unavailable")


class FakeCompletionAuthority:
    def __init__(self, decision: CompletedDownloadDecision) -> None:
        self.decision = decision
        self.calls: list[dict[str, Any]] = []

    async def evaluate(self, **kwargs: Any) -> CompletedDownloadDecision:
        self.calls.append(kwargs)
        return self.decision


class FakeDownloader:
    def __init__(self, item: DownloadItem) -> None:
        self.item = item
        self.calls: list[dict[str, Any]] = []

    async def add_magnet(self, **kwargs: Any) -> DownloadItem:
        self.calls.append(kwargs)
        return self.item


def test_scheduler_admits_retry_only_after_verified_absence(check: Check) -> None:
    category = TvShowCategory()
    bundle = {
        "selective_download_required": True,
        "selective_queue_policy": {
            "status": "supported",
            "mode": "metadata_file_priority",
            "target_scope": "requested_unit_only",
        },
    }
    context = import_context(category, bundle=bundle)
    item = DownloadItem(
        id="queued-e10",
        item_name="Example Series",
        item_id="Example Series",
        category_id="tv",
        magnet="magnet:?xt=urn:btih:queued-e10",
        status=DownloadStatus.QUEUED,
        season=1,
        episode=10,
        import_context=context,
    )
    decision = CompletedDownloadDecision(
        verified=True,
        satisfied=False,
        category_id="tv",
        item_id="Example Series",
        unit_label="S01E10",
        reason="requested logical unit is absent from the canonical library",
    )
    scheduler = object.__new__(MediaScheduler)
    scheduler._completed_download_authority = FakeCompletionAuthority(decision)
    scheduler._downloader = FakeDownloader(item)
    receipt = run(scheduler.queue_download(
        name="Example Series",
        magnet=item.magnet,
        season=1,
        episode=10,
        category_id="tv",
        torrent_title="Example Series S01 ITA",
        import_context=context,
    ))
    call = scheduler._downloader.calls[0]
    check.ok(call.get("retry_completed_if_unsatisfied") is True, f"verified absence should be passed to downloader admission: {call!r}")
    check.ok(receipt.get("status") == "queued", f"scheduler should report actual queued row: {receipt!r}")
    selective = receipt.get("selective_download") or {}
    check.ok(selective.get("status") == "registered_pending_metadata", f"receipt should describe pending metadata selection: {selective!r}")
    check.ok("does not claim" in str(selective.get("note") or ""), "receipt must not claim file selection has completed")


class FakeDownloadsRepo:
    def __init__(self, rows: list[DownloadItem]) -> None:
        self.rows = rows
        self.upserts: list[DownloadItem] = []

    async def find_existing_by_import_context(self, context: DownloadImportContext) -> list[DownloadItem]:
        return list(self.rows)

    async def upsert_download(self, item: DownloadItem) -> None:
        self.upserts.append(item)


class FakeEngine:
    def __init__(self) -> None:
        self.removed: list[str] = []

    async def remove_torrent(self, download_id: str) -> None:
        self.removed.append(download_id)


def test_completed_duplicate_filter_and_revival_preserve_active_safety(check: Check) -> None:
    category = TvShowCategory()
    context = import_context(category)
    completed = DownloadItem(
        id="completed-old",
        item_name="Example Series",
        magnet="magnet:?xt=urn:btih:completed-old",
        status=DownloadStatus.COMPLETE,
        progress=100,
        total_size=10_000,
        downloaded_bytes=10_000,
        uploaded_bytes=5_000,
        seed_ratio=0.5,
        sharing_enabled=True,
        completed_at=datetime.now(timezone.utc),
        files=[],
        import_context=context,
    )
    active = DownloadItem(
        id="active-new",
        item_name="Example Series",
        magnet="magnet:?xt=urn:btih:active-new",
        status=DownloadStatus.DOWNLOADING,
        import_context=context,
    )
    repo = FakeDownloadsRepo([completed, active])
    duplicate = run(_find_duplicate_import_context(repo, context, download_id="requested", ignore_completed=True))
    check.ok(duplicate is active, "verified absence may ignore completed history but must still preserve an active duplicate")

    repo = FakeDownloadsRepo([completed])
    manager = object.__new__(DownloadManager)
    manager._engine = FakeEngine()
    manager._db = SimpleNamespace(downloads=repo)
    revived = run(manager._revive_completed_download(
        completed,
        item_name="Example Series",
        category_id="tv",
        item_id="Example Series",
        season=1,
        episode=10,
        language="Italian",
        torrent_title="Example Series S01 ITA",
        source_seeders=80,
        priority=DownloadPriority.HIGH,
        reason="manual",
        import_context=context,
    ))
    check.ok(revived.status == DownloadStatus.QUEUED, f"completed row should be reset to queued: {revived!r}")
    check.ok(revived.progress == 0 and revived.completed_at is None, "revival must clear terminal progress/completion markers")
    check.ok(
        revived.total_size == 0
        and revived.downloaded_bytes == 0
        and revived.uploaded_bytes == 0
        and revived.seed_ratio == 0
        and revived.sharing_enabled is False,
        "revival must clear stale transfer/share metrics before the new metadata arrives",
    )
    check.ok(manager._engine.removed == ["completed-old"], "stale engine handle should be removed before requeue")
    check.ok(repo.upserts and repo.upserts[-1].id == "completed-old", "revived row should be persisted")


def test_tool_outcome_ledger_rejects_failed_queue_receipt(check: Check) -> None:
    ledger = ToolOutcomeLedger()
    failure = {
        "role": "tool",
        "name": "queue_download",
        "content": json.dumps({
            "error": "No candidates were queued.",
            "errors": [{
                "unit_descriptor": {"label": "S01E10"},
                "error": "A matching download is already complete; no new queue row was created.",
            }],
        }),
    }
    ledger.record("queue_download", failure)
    detail = ledger.unresolved_queue_failure() or ""
    check.ok("No candidates were queued" in detail and "S01E10" in detail, f"ledger should retain exact structured failure: {detail!r}")
    ledger.record("queue_download", {
        "role": "tool",
        "name": "queue_download",
        "content": json.dumps({"status": "queued", "download_id": "new-row"}),
    })
    check.ok(ledger.unresolved_queue_failure() is None, "later verified queue success should resolve the failure")

    ledger.record("queue_download", {
        "role": "tool",
        "name": "queue_download",
        "content": json.dumps({
            "queued_count": 1,
            "queued": [{"download_id": "new-row", "unit_descriptor": {"label": "S01E09"}}],
            "errors": [{
                "unit_descriptor": {"label": "S01E10"},
                "error": "candidate could not be registered",
            }],
        }),
    })
    partial = ledger.partial_queue_failure()
    check.ok(partial is not None and partial[0] == 1 and "S01E10" in partial[1], f"partial batch failures must remain visible: {partial!r}")
    check.ok(ledger.unresolved_queue_failure() is None, "a partial receipt must not be mislabeled as a total failure")


class FakeDelta:
    def __init__(self, content: str | None = None, tool_calls: list[dict[str, Any]] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class FakeChoice:
    def __init__(self, delta: FakeDelta) -> None:
        self.delta = delta


class FakeChunk:
    def __init__(self, delta: FakeDelta) -> None:
        self.choices = [FakeChoice(delta)]


class QueueFailureToolExecutor:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute_tool_call(self, **kwargs: Any) -> tuple[dict[str, Any], None]:
        self.calls.append(str(kwargs.get("name") or ""))
        result = {
            "error": "No candidates were queued.",
            "errors": [{
                "unit_descriptor": {"label": "S01E10"},
                "error": "A matching download is already complete; no new queue row was created.",
            }],
        }
        return {
            "role": "tool",
            "tool_call_id": kwargs.get("tool_call_id"),
            "name": kwargs.get("name"),
            "content": json.dumps(result),
        }, None


class QueueFailureStream:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, **_kwargs: Any) -> AsyncIterator[FakeChunk]:
        self.calls += 1
        call_number = self.calls

        async def stream() -> AsyncIterator[FakeChunk]:
            if call_number == 1:
                yield FakeChunk(FakeDelta(tool_calls=[{
                    "index": 0,
                    "id": "queue-e10",
                    "function": {"name": "queue_download", "arguments": '{"candidate_id":"candidate-e10"}'},
                }]))
                return
            yield FakeChunk(FakeDelta(content="Great, I queued the episode successfully."))

        return stream()


def test_streaming_loop_suppresses_success_claim_after_queue_failure(check: Check) -> None:
    async def scenario() -> tuple[str, StreamingAgentLoopExecutor, QueueFailureToolExecutor]:
        tools = QueueFailureToolExecutor()
        loop = StreamingAgentLoopExecutor(tools, QueueFailureStream())
        chunks: list[str] = []
        async for chunk in loop.execute(
            messages=[{"role": "system", "content": "test"}, {"role": "user", "content": "download episode 10"}],
            tool_definitions=[{"type": "function", "function": {"name": "queue_download", "parameters": {"type": "object"}}}],
            allowed_tool_names={"queue_download"},
            max_iterations=3,
            task="download",
            user_prompt="download episode 10",
            active_category_id="tv",
        ):
            chunks.append(chunk)
        return "".join(chunks), loop, tools

    output, loop, tools = run(scenario())
    check.ok("queued the episode successfully" not in output, f"LLM contradiction must never reach the user: {output!r}")
    check.ok("No candidates were queued" in output, f"deterministic failure should expose the actual receipt: {output!r}")
    check.ok(loop.last_content == output, "conversation history should store the same truthful failure shown to the user")
    check.ok(tools.calls == ["queue_download"], f"queue tool should execute exactly once: {tools.calls!r}")


class MetadataStatusToolExecutor:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute_tool_call(self, **kwargs: Any) -> tuple[dict[str, Any], None]:
        name = str(kwargs.get("name") or "")
        self.calls.append(name)
        result = {"title": "Example Series", "metadata": {"season_count": 1}}
        if name == "list_downloads":
            result = {"downloads": [], "active_count": 0}
        return {
            "role": "tool",
            "tool_call_id": kwargs.get("tool_call_id"),
            "name": name,
            "content": json.dumps(result),
        }, None


class MetadataStatusStream:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, **_kwargs: Any) -> AsyncIterator[FakeChunk]:
        self.calls += 1
        call_number = self.calls

        async def stream() -> AsyncIterator[FakeChunk]:
            if call_number == 1:
                yield FakeChunk(FakeDelta(tool_calls=[{
                    "index": 0,
                    "id": "metadata-1",
                    "function": {"name": "get_media_details", "arguments": '{"name":"Example Series"}'},
                }]))
                return
            if call_number == 2:
                yield FakeChunk(FakeDelta(content="The season pack is already active."))
                return
            yield FakeChunk(FakeDelta(content="No active download is currently reported."))

        return stream()


def test_download_status_claim_requires_current_operational_evidence(check: Check) -> None:
    async def scenario() -> tuple[str, MetadataStatusToolExecutor]:
        tools = MetadataStatusToolExecutor()
        loop = StreamingAgentLoopExecutor(tools, MetadataStatusStream())
        chunks: list[str] = []
        definitions = [
            {"type": "function", "function": {"name": "get_media_details", "parameters": {"type": "object"}}},
            {"type": "function", "function": {"name": "list_downloads", "parameters": {"type": "object"}}},
        ]
        async for chunk in loop.execute(
            messages=[{"role": "system", "content": "test"}, {"role": "user", "content": "download episode 10"}],
            tool_definitions=definitions,
            allowed_tool_names={"get_media_details", "list_downloads"},
            max_iterations=4,
            task="download",
            user_prompt="download episode 10",
            active_category_id="tv",
        ):
            chunks.append(chunk)
        return "".join(chunks), tools

    output, tools = run(scenario())
    check.ok("season pack is already active" not in output, f"metadata-only status invention must be suppressed: {output!r}")
    check.ok("list_downloads" in tools.calls, f"loop should verify current transfer state: {tools.calls!r}")
    check.ok("No active download" in output, f"final response should follow current queue evidence: {output!r}")


def test_no_fixture_title_or_generic_tv_branch_leaks(check: Check) -> None:
    production = "\n".join(
        (ROOT / path).read_text()
        for path in (
            "src/ai/tool_outcome_guard.py",
            "src/core/download_completion_authority.py",
            "src/ai/tools/search_workspace.py",
            "src/core/downloader.py",
            "src/core/scheduler.py",
        )
    )
    check.ok("Rooster" not in production, "production fix must not hard-code the observed show title")
    generic = (ROOT / "src/ai/tools/search_workspace.py").read_text() + (ROOT / "src/core/download_completion_authority.py").read_text()
    double_quoted_branch = "category_id " + '== "t' + 'v"'
    single_quoted_branch = "category_id " + "== 't" + "v'"
    check.ok(double_quoted_branch not in generic and single_quoted_branch not in generic, "generic collaborators must remain category-neutral")


def main() -> None:
    check = Check()
    test_tv_bundle_publishes_supported_metadata_selection(check)
    test_tv_bundle_that_does_not_prove_target_stays_blocked(check)
    test_tv_file_matcher_selects_only_requested_episode_or_season(check)
    test_language_preference_is_not_release_evidence(check)
    test_canonical_library_overrules_stale_complete_history(check)
    test_season_satisfaction_requires_the_full_provider_frontier(check)
    test_scheduler_admits_retry_only_after_verified_absence(check)
    test_completed_duplicate_filter_and_revival_preserve_active_safety(check)
    test_tool_outcome_ledger_rejects_failed_queue_receipt(check)
    test_streaming_loop_suppresses_success_claim_after_queue_failure(check)
    test_download_status_claim_requires_current_operational_evidence(check)
    test_no_fixture_title_or_generic_tv_branch_leaks(check)
    check.finish()


if __name__ == "__main__":
    main()
