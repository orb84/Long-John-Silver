#!/usr/bin/env python3
"""Round 90 regression traces for endpoint context and planning context.

These are executable scenario checks for the issues seen in the 2026-05-24
logs: placeholder crashes, 8k fallback context, overlarge single-item prompts,
and user context caps below the usable minimum.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ai.llm_task_runtime import LLMTaskRuntime
from src.ai.plan_executor import PlanExecutor
from src.ai.reasoning import ReasoningPlanner
from src.core.categories.registry import CategoryRegistry
from src.core.models import AgentPlan, Intent, ItemList, LLMConfig, PlanExecutionStep, PlanStep, Settings, TvShowItem
from src.llm_providers.context_limits import (
    FALLBACK_CONTEXT_LIMIT,
    MIN_USER_CONTEXT_LIMIT,
    extract_context_limit,
    extract_loaded_context_limit,
    iter_model_records,
)


class DummyToolRegistry:
    def get_definitions_for_intent(self, _intent):
        return []


class DummyLLMClient:
    def __init__(self, limit):
        self.limit = limit
    def resolve_task(self, _task):
        return SimpleNamespace(context_limit=self.limit)


class DummyExecutor:
    async def execute_direct_call(self, name, arguments):
        return {}


def test_context_limit_parsers() -> None:
    lmstudio_payload = {
        "models": [{
            "key": "gemma-4-e4b-it",
            "max_context_length": 131072,
            "loaded_instances": [{"id": "gemma-4-e4b-it", "config": {"context_length": 65536}}],
        }]
    }
    model = next(iter_model_records(lmstudio_payload))
    assert extract_loaded_context_limit(model) == 65536
    assert extract_context_limit(model) == 131072

    nvidia_like = {"id": "openai/gpt-oss-120b", "max_model_len": 131072}
    assert extract_context_limit(nvidia_like) == 131072

    nested = {"id": "x", "limits": {"context_window": "32768"}}
    assert extract_context_limit(nested) == 32768


def test_runtime_context_floor_and_fallback() -> None:
    settings = Settings(llm=LLMConfig(max_context_tokens=1000, context_budget_percent=100, reserved_output_tokens=1000))
    runtime = LLMTaskRuntime(settings, DummyLLMClient(131072), DummyToolRegistry())
    budget = runtime.context_budget_for_task("chat")
    assert budget["endpoint_context_tokens"] == 131072
    assert budget["model_context_tokens"] == MIN_USER_CONTEXT_LIMIT
    assert budget["available_prompt_tokens"] == MIN_USER_CONTEXT_LIMIT - 1000

    settings2 = Settings(llm=LLMConfig(max_context_tokens=None, context_budget_percent=100, reserved_output_tokens=1000))
    runtime2 = LLMTaskRuntime(settings2, DummyLLMClient(None), DummyToolRegistry())
    budget2 = runtime2.context_budget_for_task("chat")
    assert budget2["endpoint_context_tokens"] == FALLBACK_CONTEXT_LIMIT
    assert budget2["model_context_tokens"] == FALLBACK_CONTEXT_LIMIT


def test_placeholder_seasons_length_resolves_to_latest_season() -> None:
    executor = PlanExecutor(tool_executor=DummyExecutor(), allowed_tool_names={"search_media_torrents"})
    dep_step = PlanStep(id="lookup_metadata", tool_name="metadata_lookup", arguments={}, depends_on=[])
    executed = {
        "lookup_metadata": PlanExecutionStep(
            step=dep_step,
            success=True,
            result={"content": {"ok": True, "best": {"number_of_seasons": 5}}},
        )
    }
    search_step = PlanStep(
        id="search_missing",
        tool_name="search_media_torrents",
        arguments={"season": "${lookup_metadata.seasons.length}"},
        depends_on=["lookup_metadata"],
    )
    args = dict(search_step.arguments)
    err = executor._resolve_dynamic_arguments(args, search_step, executed)  # noqa: SLF001 - regression seam
    assert err is None
    assert args["season"] == 5


def test_matched_category_context_is_item_scoped() -> None:
    registry = CategoryRegistry()
    registry.register_defaults()
    tv = registry.get("tv")
    settings = Settings(tracked_items=ItemList(items=[
        TvShowItem(key="For All Mankind", language="Italian"),
        TvShowItem(key="Babylon 5", language="English"),
        TvShowItem(key="Chernobyl", language="English"),
    ]))

    async def run() -> dict:
        return await tv.build_llm_context_packet(
            user_message="grab missing episodes from For All Mankind",
            intent=Intent.DOWNLOAD,
            settings=settings,
            db=None,
        )

    packet = asyncio.run(run())
    assert packet["context_scope"] == "matched_item"
    assert [item["key"] for item in packet["tracked_items"]] == ["For All Mankind"]
    assert "Babylon 5" in packet["other_tracked_item_keys_sample"]


def test_planner_tool_schema_is_compact() -> None:
    planner = ReasoningPlanner()
    verbose_tool = {
        "type": "function",
        "function": {
            "name": "search_media_torrents",
            "description": "Search torrents for media.",
            "parameters": {
                "type": "object",
                "properties": {
                    f"field_{i}": {"type": "string", "description": "x" * 300}
                    for i in range(20)
                },
                "required": ["field_0"],
            },
        },
    }
    prompt = planner._build_plan_prompt(  # noqa: SLF001 - regression seam
        "Grab missing episodes from For All Mankind",
        Intent.DOWNLOAD,
        context="ACTIVE CATEGORY LIBRARY CONTEXT PACKET: small",
        tool_schemas=[verbose_tool],
    )
    assert "Parameter JSON Schema" not in prompt
    assert len(prompt) < 7000


def main() -> None:
    test_context_limit_parsers()
    test_runtime_context_floor_and_fallback()
    test_placeholder_seasons_length_resolves_to_latest_season()
    test_matched_category_context_is_item_scoped()
    test_planner_tool_schema_is_compact()
    print("Round 90 context/planning regression traces passed")


if __name__ == "__main__":
    main()
