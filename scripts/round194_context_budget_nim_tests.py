#!/usr/bin/env python3
"""Round 194 regression tests for context-window hot reload and NVIDIA NIM metadata."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ai.llm_task_runtime import LLMTaskRuntime
from src.ai.tool_registry import ToolRegistry
from src.core.models import LLMConfig, Settings
from src.llm_providers.catalog import ModelCatalog
from src.llm_providers.context_limits import probe_endpoint_context_limit
from src.llm_providers.key_store import KeyStore


class FakeResolved:
    context_limit = 128_000
    context_limit_source = "fake endpoint"
    context_limit_reported = True


class FakeTaskClient:
    def __init__(self) -> None:
        self.ensure_calls = []
        self._warmed = False

    async def ensure_model_metadata_for_task(self, task: str, force_refresh: bool = False) -> None:
        self.ensure_calls.append((task, force_refresh))
        self._warmed = True

    def resolve_task(self, task: str) -> FakeResolved:
        if self._warmed:
            return FakeResolved()
        class ColdResolved:
            context_limit = 16_384
            context_limit_source = "configured fallback"
            context_limit_reported = False
        return ColdResolved()


async def test_runtime_prewarms_metadata_before_budget() -> None:
    settings = Settings()
    settings.llm = LLMConfig(model="openai/gpt-oss-120b", active_provider="nvidia_nim")
    client = FakeTaskClient()
    runtime = LLMTaskRuntime(settings, client, ToolRegistry())

    cold = runtime.context_budget_for_task("download")
    assert cold["endpoint_context_tokens"] == 16_384
    assert cold["endpoint_context_reported"] is False

    await runtime.ensure_context_metadata_for_task("download")
    warm = runtime.context_budget_for_task("download")
    assert client.ensure_calls == [("download", False)]
    assert warm["endpoint_context_tokens"] == 128_000
    assert warm["endpoint_context_reported"] is True
    assert warm["model_context_tokens"] == 128_000


async def test_nvidia_known_context_metadata_prevents_16k_fallback() -> None:
    result = await probe_endpoint_context_limit(
        base_url=None,
        model_id="openai/gpt-oss-120b",
        provider_id="nvidia_nim",
    )
    assert result.usable_context_tokens == 128_000
    assert result.max_context_tokens == 128_000
    assert result.endpoint_reported is True
    assert result.source == "built-in provider/model metadata"


def test_catalog_attaches_nvidia_known_context_when_models_endpoint_omits_it() -> None:
    catalog = ModelCatalog(KeyStore(Path("/tmp/nonexistent-ljs-keys.json")))
    models = catalog._parse_models_response("nvidia_nim", {"data": [{"id": "openai/gpt-oss-120b"}]})
    match = next(m for m in models if m.id == "openai/gpt-oss-120b")
    assert match.context.max_context_tokens == 128_000


def test_settings_preview_does_not_reuse_saved_api_base_for_other_provider() -> None:
    text = (ROOT / "src/web/routers/settings.py").read_text()
    assert "selected_is_active = provider_id == llm.active_provider" in text
    assert "do not reuse the saved api_base/api_key" in text


def test_assistant_prewarms_before_context_budget() -> None:
    text = (ROOT / "src/ai/assistant.py").read_text()
    marker = "await self._llm_runtime.ensure_context_metadata_for_task(task)\n        context_budget = self._llm_runtime.context_budget_for_task(task)"
    assert marker in text


async def main() -> None:
    await test_runtime_prewarms_metadata_before_budget()
    await test_nvidia_known_context_metadata_prevents_16k_fallback()
    test_catalog_attaches_nvidia_known_context_when_models_endpoint_omits_it()
    test_settings_preview_does_not_reuse_saved_api_base_for_other_provider()
    test_assistant_prewarms_before_context_budget()
    print("round194 context budget / NVIDIA NIM metadata tests passed")


if __name__ == "__main__":
    asyncio.run(main())
