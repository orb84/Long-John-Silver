#!/usr/bin/env python3
"""Round 251 regressions for download chat stability and state transparency.

The observed session failure had three coupled symptoms:
- streamed candidate prose was shown, then the same turn later showed a different
  candidate set after another tool call;
- fresh-request heuristics hid the immediately previous download/result state,
  so complaints/corrections could cancel or replace work without an audit trail;
- final download replies were allowed to omit state-changing tool actions.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import sys
import types
from typing import Any, AsyncIterator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# The conversation module imports the real database facade for type hints; the
# tests below use only an injected fake conversation manager and do not need the
# optional sqlite package present in this sandbox.
sys.modules.setdefault("aiosqlite", types.SimpleNamespace(Connection=object, Row=dict, Cursor=object))

from src.ai.conversation_binding import ConversationBinding
from src.ai.goal_state import AgentGoalState, AgentGoalStateManager
from src.ai.pending_actions import PendingActionContextBuilder
from src.ai.streaming_agent_loop import StreamingAgentLoopExecutor
from src.core.models import Intent
from src.utils.candidate_ids import store_result_set


class Check:
    """Small assertion collector for script-style regression tests."""

    def __init__(self) -> None:
        self.failures: list[str] = []

    def ok(self, condition: bool, message: str) -> None:
        if not condition:
            self.failures.append(message)

    def finish(self) -> None:
        if self.failures:
            print("Round 251 chat/session failures:")
            for failure in self.failures:
                print(f" - {failure}")
            raise SystemExit(1)
        print("round251_chat_stream_context_action_tests: OK")


class FakeSystemStore:
    """In-memory preference store matching the database.system facade."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get_preference(self, key: str) -> str | None:
        return self.values.get(key)

    async def set_preference(self, key: str, value: str) -> None:
        self.values[key] = value


class FakeDatabase:
    """Minimal database facade for pending-action and goal-state tests."""

    def __init__(self) -> None:
        self.system = FakeSystemStore()


class FakeConversationManager:
    """Conversation manager returning a fixed context packet."""

    MAX_FULL_TURNS = 20

    def __init__(self, messages: list[dict[str, str]]) -> None:
        self.messages = messages

    async def get_context(self, *_args: Any, **_kwargs: Any) -> list[dict[str, str]]:
        return list(self.messages)

    def has_vector_store(self) -> bool:
        return False


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


class FakeToolExecutor:
    """Tool executor that records calls and returns a successful tool result."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute_tool_call(self, **kwargs: Any) -> tuple[dict[str, Any], None]:
        self.calls.append(dict(kwargs))
        result = {"ok": True, "tool": kwargs.get("name"), "status": "searched"}
        return {
            "role": "tool",
            "tool_call_id": kwargs.get("tool_call_id"),
            "name": kwargs.get("name"),
            "content": json.dumps(result),
        }, None


class FakeStreamCompletion:
    """First iteration emits premature prose plus a tool call; second emits final text."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, **_kwargs: Any) -> AsyncIterator[FakeChunk]:
        self.calls += 1
        call_number = self.calls

        async def stream() -> AsyncIterator[FakeChunk]:
            if call_number == 1:
                yield FakeChunk(FakeDelta(content="Premature candidate table that should never be displayed."))
                yield FakeChunk(FakeDelta(tool_calls=[{
                    "index": 0,
                    "id": "call_search_1",
                    "function": {
                        "name": "search_media_torrents",
                        "arguments": '{"query":"For All Mankind S01"}',
                    },
                }]))
                return
            yield FakeChunk(FakeDelta(content="Final tool-grounded answer only."))

        return stream()


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _result_set() -> dict[str, Any]:
    return {
        "result_set_id": "rs_for_all_mankind_s01",
        "name": "For All Mankind S01",
        "query": "For All Mankind S01E01-10",
        "category_id": "tv",
        "candidates": [
            {"candidate_id": "cand_a", "title": "For All Mankind S01 pack", "seeders": 300, "languages": ["English"], "resolution": "1080p"},
        ],
    }


def test_pending_action_context_is_guarded_not_erased(check: Check) -> None:
    async def scenario() -> str:
        db = FakeDatabase()
        await store_result_set(db, session_id="s1", cache_data=_result_set())
        builder = PendingActionContextBuilder(db)
        return await builder.build_for_session(
            "s1",
            current_user_prompt="Find me the first season in English",
            intent=Intent.DOWNLOAD,
        )

    context = _run(scenario())
    check.ok("rs_for_all_mankind_s01" in context, "guarded fresh request should retain structured result-set handles")
    check.ok('"fresh_request_guard": true' in context, "guarded packet should mark the prior workspace as guarded")
    check.ok("complaints/corrections/refinements" in context, "guard rule should explain why the context is still useful")


def test_active_goal_keeps_recent_results_when_fresh_heuristic_fires(check: Check) -> None:
    async def scenario() -> str:
        db = FakeDatabase()
        await store_result_set(db, session_id="s1", cache_data=_result_set())
        previous = AgentGoalState(
            goal_id="goal-old",
            session_id="s1",
            intent=Intent.DOWNLOAD.value,
            user_goal="grab For All Mankind season 1",
            category_id="tv",
            status="active",
            result_sets=[{"result_set_id": "rs_for_all_mankind_s01"}],
        )
        await db.system.set_preference(AgentGoalStateManager._key("s1"), json.dumps(asdict(previous)))
        manager = AgentGoalStateManager(db)
        return await manager.build_context_and_update(
            session_id="s1",
            user_prompt="Find me the first season in English",
            intent=Intent.DOWNLOAD,
            category_id="tv",
        )

    context = _run(scenario())
    check.ok("goal-old" in context, "fresh heuristic should not discard an active goal with result sets")
    check.ok("rs_for_all_mankind_s01" in context, "active goal should keep recent result sets for correction turns")


def test_fresh_download_context_keeps_immediate_history_tail(check: Check) -> None:
    messages = [
        {"role": "system", "content": "COMPRESSED RELEVANT PAST CONTEXT:\nold unrelated stuff"},
        {"role": "user", "content": "Grab For All Mankind season 1"},
        {"role": "assistant", "content": "Queued download id=164e4fe3e301"},
        {"role": "user", "content": "I do not need Italian"},
    ]
    binding = ConversationBinding(FakeConversationManager(messages))
    context = _run(binding.build_context_messages(
        "s1", "u1", user_prompt="Find it in English", fresh_download_request=True,
    ))
    text = json.dumps(context)
    check.ok("Queued download id=164e4fe3e301" in text, "guarded fresh requests must keep immediate state-changing history")
    check.ok("old unrelated stuff" not in text, "guarded fresh requests should still drop older compressed recalls")


def test_streaming_tool_iteration_suppresses_premature_prose(check: Check) -> None:
    async def scenario() -> tuple[list[str], StreamingAgentLoopExecutor, FakeToolExecutor, FakeStreamCompletion]:
        tools = FakeToolExecutor()
        streamer = FakeStreamCompletion()
        executor = StreamingAgentLoopExecutor(tools, streamer)
        chunks: list[str] = []
        async for chunk in executor.execute(
            messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "download x"}],
            tool_definitions=[{"type": "function", "function": {"name": "search_media_torrents", "parameters": {"type": "object"}}}],
            allowed_tool_names={"search_media_torrents"},
            max_iterations=3,
            task="download",
            user_prompt="download x",
        ):
            chunks.append(chunk)
        return chunks, executor, tools, streamer

    chunks, executor, tools, streamer = _run(scenario())
    visible = "".join(chunks)
    check.ok(visible == "Final tool-grounded answer only.", "stream should only show final no-tool response")
    check.ok("Premature candidate" not in visible, "prose emitted with tool calls must be suppressed")
    check.ok(executor.last_content == "Final tool-grounded answer only.", "conversation log should store final emitted text only")
    check.ok(len(tools.calls) == 1, "tool call should still execute once")
    check.ok(streamer.calls == 2, "loop should continue after tool execution to get final answer")


def test_download_contract_requires_state_change_reporting(check: Check) -> None:
    source = (ROOT / "src" / "ai" / "assistant.py").read_text()
    check.ok("state-changing download tool" in source, "download contract should require action-result reporting")
    check.ok("Do not bury or omit a cancellation" in source, "download contract should forbid hidden cancellations")
    check.ok("Never say a download was queued" in source, "download contract should forbid ungrounded queue claims")
    check.ok("Treat that as a search/refinement first" in source, "download contract should not silently cancel when refining a result")


def main() -> None:
    check = Check()
    test_pending_action_context_is_guarded_not_erased(check)
    test_active_goal_keeps_recent_results_when_fresh_heuristic_fires(check)
    test_fresh_download_context_keeps_immediate_history_tail(check)
    test_streaming_tool_iteration_suppresses_premature_prose(check)
    test_download_contract_requires_state_change_reporting(check)
    check.finish()


if __name__ == "__main__":
    main()
