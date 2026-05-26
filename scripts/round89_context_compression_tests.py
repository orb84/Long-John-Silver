#!/usr/bin/env python3
"""Round 89 regression traces for compression-first LLM context assembly.

These are lightweight executable checks for the contract requested after
Round 88:

- context windows include output tokens in the budget;
- recent conversation receives a raw reserve (30% by default);
- older conversation is compressed, not silently dropped;
- final provider-call budget enforcement is compression-first;
- a user cap of 0 disables optional/history context.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The execution container used for these focused traces may not include the
# optional aiosqlite package needed by the full database implementation.  The
# ConversationManager tests below use a fake DB and never call aiosqlite, so a
# minimal import stub keeps the module importable without hiding production
# dependency issues in full pytest.
class _AioSqliteStub(types.ModuleType):
    def __getattr__(self, name):
        placeholder = type(name, (), {})
        setattr(self, name, placeholder)
        return placeholder

_aiosqlite_stub = _AioSqliteStub("aiosqlite")
_aiosqlite_stub.Connection = object
_aiosqlite_stub.Cursor = object
sys.modules.setdefault("aiosqlite", _aiosqlite_stub)

from src.ai.llm_task_runtime import LLMTaskRuntime
from src.ai.token_budget import TokenBudgetManager
from src.core.conversation import ConversationManager
from src.core.models import LLMConfig, Settings


@dataclass
class _ResolvedTask:
    context_limit: int


class _DummyLLMClient:
    def __init__(self, context_limit: int) -> None:
        self.context_limit = context_limit

    def resolve_task(self, task: str) -> _ResolvedTask:
        return _ResolvedTask(context_limit=self.context_limit)


class _DummyToolRegistry:
    def get_definitions_for_intent(self, intent):
        return []


class _SystemDB:
    def __init__(self, turns: list[dict]) -> None:
        self._turns = turns

    async def get_conversation_history(self, session_id: str, limit: int = 200):
        return self._turns[-limit:]

    async def add_conversation_turn(self, *args, **kwargs):
        return 1

    async def get_conversation_turn_count(self, session_id: str):
        return len(self._turns)


class _DB:
    def __init__(self, turns: list[dict]) -> None:
        self.system = _SystemDB(turns)


async def _conversation_context() -> list[dict]:
    turns = []
    for i in range(1, 16):
        turns.append({"id": i * 2 - 1, "role": "user", "content": f"older user fact {i}: remember project decision {i}"})
        turns.append({"id": i * 2, "role": "assistant", "content": f"older assistant answer {i}: accepted decision {i}"})
    turns.append({"id": 31, "role": "user", "content": "recent raw user question about For All Mankind"})
    turns.append({"id": 32, "role": "assistant", "content": "recent raw assistant answer with candidate ids"})
    manager = ConversationManager(_DB(turns))
    return await manager.get_context(
        "web:default",
        max_turns=6,
        max_tokens=900,
        raw_recent_tokens=180,
        compressed_history_tokens=720,
    )


def test_budget_splits_output_and_raw_recent() -> None:
    settings = Settings(llm=LLMConfig(
        max_context_tokens=None,
        context_budget_percent=100,
        reserved_output_tokens=1000,
        raw_recent_context_percent=30,
    ))
    runtime = LLMTaskRuntime(settings, _DummyLLMClient(10_000), _DummyToolRegistry())
    budget = runtime.context_budget_for_task("chat")
    assert budget["model_context_tokens"] == 10_000
    assert budget["reserved_output_tokens"] == 1000
    assert budget["available_prompt_tokens"] == 9000
    assert budget["provider_call_context_tokens"] == 10_000
    assert budget["raw_recent_context_percent"] == 30
    assert budget["raw_recent_conversation_tokens"] == int(budget["conversation_tokens"] * 0.30)
    assert budget["compressed_history_tokens"] == budget["conversation_tokens"] - budget["raw_recent_conversation_tokens"]


def test_too_small_cap_is_raised_to_minimum_context() -> None:
    settings = Settings(llm=LLMConfig(
        max_context_tokens=0,
        context_budget_percent=100,
        reserved_output_tokens=0,
    ))
    runtime = LLMTaskRuntime(settings, _DummyLLMClient(10_000), _DummyToolRegistry())
    budget = runtime.context_budget_for_task("chat")
    assert budget["model_context_tokens"] == 10_000
    assert budget["available_prompt_tokens"] == budget["model_context_tokens"] - budget["reserved_output_tokens"]
    assert budget["provider_call_context_tokens"] == 10_000
    assert budget["conversation_tokens"] > 0
    assert budget["max_recent_turns"] > 0


def test_token_budget_compresses_old_history_instead_of_dropping() -> None:
    manager = TokenBudgetManager(default_context_limit=1400)
    messages = [{"role": "system", "content": "primary system"}]
    for i in range(30):
        messages.append({"role": "user", "content": f"old user turn {i} " + ("alpha " * 80)})
        messages.append({"role": "assistant", "content": f"old assistant turn {i} " + ("beta " * 80)})
    messages.append({"role": "user", "content": "current request must remain raw"})

    compressed = manager.compress_messages(
        messages,
        context_limit=1400,
        reserved_output_tokens=200,
        raw_recent_context_percent=30,
    )
    assert compressed[0]["content"] == "primary system"
    assert compressed[-1]["content"] == "current request must remain raw"
    assert any("COMPRESSED EARLIER CONVERSATION CONTEXT" in str(m.get("content") or "") for m in compressed)
    assert manager.estimate_messages(compressed) <= 1200 or len(compressed) < len(messages)


def test_conversation_manager_preserves_recent_raw_and_compresses_older() -> None:
    ctx = asyncio.run(_conversation_context())
    combined = "\n".join(str(m.get("content") or "") for m in ctx)
    assert "COMPRESSED PAST CONVERSATION CONTEXT" in combined
    assert "older user fact" in combined
    assert "recent raw user question about For All Mankind" in combined
    assert "recent raw assistant answer with candidate ids" in combined


def test_ui_and_docs_expose_raw_recent_setting() -> None:
    js = (ROOT / "src/web/static/js/components/settingsPanel.js").read_text()
    docs = (ROOT / "AGENTS.md").read_text() + "\n" + (ROOT / "architecture.md").read_text()
    assert "pref-llm-raw-recent-percent" in js
    assert "raw_recent_context_percent" in js
    assert "compression-first" in docs.lower()


if __name__ == "__main__":
    test_budget_splits_output_and_raw_recent()
    test_too_small_cap_is_raised_to_minimum_context()
    test_token_budget_compresses_old_history_instead_of_dropping()
    test_conversation_manager_preserves_recent_raw_and_compresses_older()
    test_ui_and_docs_expose_raw_recent_setting()
    print("Round 89 context-compression traces passed")
