"""Executable Round 287 context-budget and responsiveness regression checks."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ai.llm_task_runtime import LLMTaskRuntime
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
from src.llm_providers.activity import LLMActivityMonitor
from src.llm_providers.task_client import TaskLLMClient


class FakeClient:
    def __init__(self) -> None:
        self.activity_monitor = LLMActivityMonitor()
        self.called = False

    def resolve_task(self, task: str):
        return SimpleNamespace(
            context_limit=128_000,
            context_limit_source="built-in provider/model metadata",
            context_limit_reported=True,
            provider_id="test",
            model="test-model",
        )

    async def ensure_model_metadata_for_task(self, task: str, force_refresh: bool = False) -> None:
        return None

    async def completion(self, *, task, messages, tools=None, stream=False, **kwargs):
        self.called = True
        call_id = self.activity_monitor.start_call(
            task=task,
            provider="test",
            model="test-model",
            messages=messages,
            tools=tools,
            stream=stream,
            generation=kwargs,
        )
        response = {"choices": [{"message": {"content": "ok"}}]}
        self.activity_monitor.finish_call(call_id, response=response)
        return response


class EmptyRegistry:
    def get_definitions_for_intent(self, intent):
        return []


def download_definitions() -> list[dict]:
    registry = AgentToolCatalog([
        DownloadToolProvider(),
        LibraryToolProvider(),
        ResearchToolProvider(),
        SchedulingToolProvider(),
        StorageToolProvider(),
    ]).build_registry()
    return AgentToolPolicy().definitions_for_intent(registry, Intent.DOWNLOAD) or []


async def main_async() -> None:
    client = FakeClient()
    runtime = LLMTaskRuntime(
        Settings(llm=LLMConfig(
            max_context_tokens=None,
            context_budget_percent=85,
            reserved_output_tokens=1024,
        )),
        client,
        EmptyRegistry(),
    )
    budget = runtime.context_budget_for_task("download")
    assert budget["model_context_tokens"] == 128_000
    assert budget["provider_call_context_tokens"] == 108_800
    assert budget["target_context_tokens"] == 32_768
    assert budget["available_prompt_tokens"] == 31_744
    assert budget["context_cap_source"] == "endpoint"
    assert budget["context_target_source"] == "task_auto_target"

    tools = download_definitions()
    names = {row["function"]["name"] for row in tools}
    assert len(tools) == 10
    assert {"enquire_about_media", "metadata_lookup", "search_media_torrents", "queue_download"} <= names
    assert {"pause_downloads", "resume_downloads", "cancel_downloads", "track_category_item", "create_web_information_watch"}.isdisjoint(names)
    assert TokenBudgetManager().estimate_tools(tools) < 14_000

    incident_messages = [
        {"role": "system", "content": "S" * 31_330},
        {"role": "user", "content": "download the last season of Silo in italian"},
    ]
    incident = runtime._audit_payload_budget("download", incident_messages, tools, budget)
    assert incident["over_target"] is False
    assert incident["over_hard_limit"] is False

    spill_messages = [
        {"role": "system", "content": "important instruction " * 5000},
        {"role": "user", "content": "download Silo"},
    ]
    await runtime.make_completion_fn()(task="download", messages=spill_messages, tools=tools)
    assert client.called is True
    call = client.activity_monitor.snapshot(limit=1)["last_call"]
    assert call["budget"]["payload"]["over_target"] is True
    assert call["budget"]["payload"]["over_hard_limit"] is False

    prompt = PromptBuilder().build_system_prompt(Intent.DOWNLOAD, active_category_id="tv")
    assert "PUBLIC WEB RESEARCH GUIDANCE" not in prompt
    assert "track_category_item" not in prompt
    assert TaskLLMClient._retry_policy("intent_routing") == (2, 90.0)
    assert TaskLLMClient._should_retry(RuntimeError("HTTP 400 bad request")) is False
    assert TaskLLMClient._should_retry(RuntimeError("API error (502 Bad Gateway)")) is True

    ui = Path("src/web/static/js/components/llmActivityPanel.js").read_text()
    assert "Interactive target" in ui
    assert "Hard context ceiling" in ui
    assert "soft target exceeded · allowed" in ui


def main() -> None:
    asyncio.run(main_async())
    print("Round 287 context-budget resilience checks passed")


if __name__ == "__main__":
    main()
