"""Round 88 regression traces for endpoint context windows and UI caps.

These traces intentionally avoid network calls.  They verify the local contract:
- endpoint/model context metadata is the default runtime limit when provider-reported;
- user caps are clamped to provider limits and the supported minimum;
- a zero cap is raised to the supported minimum rather than disabling context;
- UI code exposes and saves the context cap rather than hiding it in YAML.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ai.llm_task_runtime import LLMTaskRuntime
from src.ai.token_budget import TokenBudgetManager
from src.core.models import LLMConfig, Settings


class FakeLLMClient:
    def __init__(self, context_limit: int):
        self.context_limit = context_limit
        self.warmed = False

    def resolve_task(self, task: str):
        return SimpleNamespace(
            context_limit=self.context_limit,
            context_limit_source="endpoint_metadata",
            context_limit_reported=True,
        )

    async def ensure_model_metadata_for_task(self, task: str, force_refresh: bool = False) -> None:
        self.warmed = True


class FakeToolRegistry:
    def get_definitions_for_intent(self, intent):
        return []


def budget_for(config: LLMConfig, endpoint_limit: int) -> dict:
    settings = Settings(llm=config)
    runtime = LLMTaskRuntime(settings, FakeLLMClient(endpoint_limit), FakeToolRegistry())
    return runtime.context_budget_for_task("chat")


def test_endpoint_window_is_hard_limit_and_interactive_target_is_soft() -> None:
    budget = budget_for(LLMConfig(max_context_tokens=None, context_budget_percent=100), 131_072)
    assert budget["endpoint_context_tokens"] == 131_072
    assert budget["model_context_tokens"] == 131_072
    assert budget["context_cap_source"] == "endpoint"
    assert budget["provider_call_context_tokens"] == 131_072
    assert budget["target_context_tokens"] == 32_768
    assert budget["context_target_source"] == "task_auto_target"


def test_user_cap_clamps_to_endpoint() -> None:
    budget = budget_for(LLMConfig(max_context_tokens=999_999, context_budget_percent=100), 64_000)
    assert budget["model_context_tokens"] == 64_000
    assert budget["context_cap_source"] == "user_cap_clamped_to_endpoint"


def test_zero_cap_is_clamped_to_supported_minimum() -> None:
    cfg = LLMConfig(max_context_tokens=0, context_budget_percent=100)
    assert cfg.get_context_tokens_for_task("chat") == 0
    budget = budget_for(cfg, 64_000)
    assert 0 < budget["model_context_tokens"] <= 64_000
    assert budget["context_cap_source"] == "user_cap_clamped_to_endpoint"
    assert budget["conversation_tokens"] > 0
    assert budget["max_recent_turns"] > 0


def test_token_budget_zero_does_not_expand_to_default() -> None:
    manager = TokenBudgetManager(default_context_limit=8192)
    messages = [
        {"role": "system", "content": "system"},
        {"role": "assistant", "content": "old " * 5000},
        {"role": "user", "content": "now"},
    ]
    trimmed = manager.trim_messages(messages, context_limit=0, reserved_output_tokens=0)
    assert any(m["role"] == "system" for m in trimmed)
    assert any(m["role"] == "user" and m["content"] == "now" for m in trimmed)
    assert not any(m["role"] == "assistant" and m["content"].startswith("old") for m in trimmed)



def test_settings_ui_exposes_context_cap() -> None:
    js = Path("src/web/static/js/components/settingsPanel.js").read_text()
    assert "pref-llm-max-context" in js
    assert "_llmContextCapPayload" in js
    assert "max_context_tokens" in js
    assert "/api/settings/llm/context" in js


def main() -> None:
    test_endpoint_window_is_hard_limit_and_interactive_target_is_soft()
    test_user_cap_clamps_to_endpoint()
    test_zero_cap_is_clamped_to_supported_minimum()
    test_token_budget_zero_does_not_expand_to_default()
    test_settings_ui_exposes_context_cap()
    print("Round 88 context-window setting traces passed")


if __name__ == "__main__":
    main()
