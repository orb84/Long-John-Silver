#!/usr/bin/env python3
"""Round 87 regression traces: LLM-owned intent, pending actions, context budget."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ai.intent_router import IntentRouter, route_intent_fast
from src.ai.pending_actions import PendingActionContextBuilder
from src.ai.plan_coordinator import PlanCoordinator
from src.ai.token_budget import TokenBudgetManager
from src.core.models import AgentPlan, Intent, PlanStep, Settings
from src.core.domain_models.media import TvShowItem


class _FakeChoice:
    def __init__(self, text: str) -> None:
        self.message = type("Msg", (), {"content": text})()


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.choices = [_FakeChoice(text)]


class _FakeLLM:
    def __init__(self, text: str = "DOWNLOAD") -> None:
        self.text = text
        self.calls: list[dict[str, Any]] = []

    async def completion(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        return _FakeResponse(self.text)


class _Prefs:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get_preference(self, key: str, default: str = "") -> str:
        return self.values.get(key, default)

    async def set_preference(self, key: str, value: str) -> None:
        self.values[key] = value


class _FakeDB:
    def __init__(self) -> None:
        self.system = _Prefs()


class _FakeExecutor:
    def get_definitions(self, allowed_tool_names: set[str]) -> list[dict]:
        return []


async def test_intent_is_llm_owned_with_pending_context() -> None:
    fast, confidence = route_intent_fast("please proceed")
    assert fast is None and confidence == 0.0, "keyword route must not classify natural language"

    db = _FakeDB()
    cache = {
        "result_set_id": "rs123",
        "name": "For All Mankind",
        "category_id": "tv",
        "query": "For All Mankind S05",
        "batch_recommendation": {
            "candidate_ids": ["c1", "c2"],
            "queue_download_arguments": {"name": "For All Mankind", "result_set_id": "rs123", "candidate_ids": ["c1", "c2"]},
        },
        "candidates": [
            {"candidate_id": "c1", "title": "candidate one", "seeders": 10},
            {"candidate_id": "c2", "title": "candidate two", "seeders": 20},
        ],
    }
    await db.system.set_preference("last_options_web_main", json.dumps(cache))
    await db.system.set_preference("torrent_result_sets_web_main", json.dumps(["rs123"]))
    await db.system.set_preference("torrent_result_set_web_main_rs123", json.dumps(cache))
    pending = await PendingActionContextBuilder(db).build_for_session("web_main")
    assert "rs123" in pending and "queue_download_arguments" in pending

    llm = _FakeLLM("DOWNLOAD")
    router = IntentRouter(llm_client=llm)
    routed = await router.route("faites-le", context=pending)
    assert routed == Intent.DOWNLOAD
    prompt = llm.calls[0]["messages"][0]["content"]
    assert "rs123" in prompt and "candidate_id" in prompt, "pending action context must be passed to LLM router"


async def test_plan_language_binding_does_not_parse_user_language_words() -> None:
    settings = Settings()
    settings.tracked_items.items = [TvShowItem(key="For All Mankind", language="Italian")]
    coord = PlanCoordinator(tool_executor=_FakeExecutor(), llm_client=None, settings=settings)

    plan = AgentPlan(
        intent=Intent.DOWNLOAD,
        user_goal="download requested unit",
        constraints={},
        steps=[PlanStep(id="s", tool_name="search_media_torrents", arguments={"name": "For All Mankind", "language": "Spanish"})],
    )
    normalized = coord._normalize_download_plan(plan, "arbitrary multilingual message", {"search_media_torrents", "queue_download"})
    assert normalized.steps[0].arguments["language"] == "Spanish", "explicit planner language must not be overwritten by prompt word heuristics"

    plan2 = AgentPlan(
        intent=Intent.DOWNLOAD,
        user_goal="download requested unit",
        constraints={},
        steps=[PlanStep(id="s", tool_name="search_media_torrents", arguments={"name": "For All Mankind"})],
    )
    # prepare_plan contains the full binding pass; _normalize_download_plan should keep search plans intact.
    normalized2 = coord._normalize_download_plan(plan2, "arbitrary multilingual message", {"search_media_torrents", "queue_download"})
    assert normalized2.steps[0].arguments["name"] == "For All Mankind"

    unsafe = AgentPlan(
        intent=Intent.DOWNLOAD,
        user_goal="latest season request",
        constraints={},
        steps=[PlanStep(id="bad", tool_name="tv.download_missing_batch", arguments={"name": "For All Mankind"})],
    )
    safe = coord._normalize_download_plan(unsafe, "latest season request", {"search_media_torrents", "queue_download"})
    assert safe.steps[0].tool_name == "search_media_torrents"
    assert "season" not in safe.steps[0].arguments, "latest/current season must be interpreted by LLM/category context, not regex"


def test_token_budget_compacts_without_losing_core_messages() -> None:
    manager = TokenBudgetManager(default_context_limit=1200)
    messages = [
        {"role": "system", "content": "primary system"},
        {"role": "system", "content": "PENDING ACTION CONTEXT\n" + "x" * 1000},
    ]
    for i in range(30):
        messages.append({"role": "assistant" if i % 2 else "user", "content": "old turn " + str(i) + " " + ("y" * 800)})
    messages.append({"role": "user", "content": "current request"})

    trimmed = manager.trim_messages(messages, context_limit=1200, reserved_output_tokens=200)
    assert trimmed[0]["content"] == "primary system"
    assert trimmed[-1]["content"] == "current request"
    assert any(str(m.get("content") or "").startswith("PENDING ACTION CONTEXT") for m in trimmed)
    assert manager.estimate_messages(trimmed) <= 1000 or len(trimmed) < len(messages)


async def main() -> None:
    await test_intent_is_llm_owned_with_pending_context()
    await test_plan_language_binding_does_not_parse_user_language_words()
    test_token_budget_compacts_without_losing_core_messages()
    print("Round 87 LLM/context/intent regression traces passed")


if __name__ == "__main__":
    asyncio.run(main())
