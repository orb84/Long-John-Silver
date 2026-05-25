"""Round 88 regression traces for endpoint context windows and UI caps.

These traces intentionally avoid network calls.  They verify the local contract:
- endpoint/model context metadata is the default runtime limit;
- user caps are allowed from the configured minimum..endpoint limit and are clamped;
- 0/too-small caps are raised to the minimum because tools need working context;
- UI code exposes and saves the context cap rather than hiding it in YAML.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ai.llm_task_runtime import LLMTaskRuntime
from src.llm_providers.context_limits import MIN_USER_CONTEXT_LIMIT
from src.core.models import LLMConfig, Settings


class FakeLLMClient:
    def __init__(self, context_limit: int, *, context_limit_reported: bool = True):
        self.context_limit = context_limit
        self.context_limit_reported = context_limit_reported
        self.warmed = False

    def resolve_task(self, task: str):
        return SimpleNamespace(
            context_limit=self.context_limit,
            context_limit_source="provider_model_endpoint" if self.context_limit_reported else "configured fallback",
            context_limit_reported=self.context_limit_reported,
        )

    async def ensure_model_metadata_for_task(self, task: str, force_refresh: bool = False) -> None:
        self.warmed = True


class FakeToolRegistry:
    def get_definitions_for_intent(self, intent):
        return []


def budget_for(config: LLMConfig, endpoint_limit: int, *, context_limit_reported: bool = True) -> dict:
    settings = Settings(llm=config)
    runtime = LLMTaskRuntime(settings, FakeLLMClient(endpoint_limit, context_limit_reported=context_limit_reported), FakeToolRegistry())
    return runtime.context_budget_for_task("chat")


def test_endpoint_max_is_default() -> None:
    budget = budget_for(LLMConfig(max_context_tokens=None, context_budget_percent=100), 131_072)
    assert budget["endpoint_context_tokens"] == 131_072
    assert budget["model_context_tokens"] == 131_072
    assert budget["context_cap_source"] == "endpoint"
    assert budget["endpoint_context_reported"] is True


def test_user_cap_clamps_to_endpoint() -> None:
    budget = budget_for(LLMConfig(max_context_tokens=999_999, context_budget_percent=100), 64_000)
    assert budget["model_context_tokens"] == 64_000
    assert budget["context_cap_source"] == "user_cap_clamped_to_endpoint"




def test_user_cap_above_unreported_fallback_is_honored() -> None:
    budget = budget_for(
        LLMConfig(max_context_tokens=131_072, context_budget_percent=100),
        16_384,
        context_limit_reported=False,
    )
    assert budget["endpoint_context_tokens"] == 16_384
    assert budget["endpoint_context_reported"] is False
    assert budget["model_context_tokens"] == 131_072
    assert budget["context_cap_source"] == "user_cap_unverified_endpoint"

def test_too_small_cap_is_raised_to_minimum() -> None:
    cfg = LLMConfig(max_context_tokens=0, context_budget_percent=100)
    assert cfg.get_context_tokens_for_task("chat") == 0
    budget = budget_for(cfg, 64_000)
    assert budget["model_context_tokens"] == MIN_USER_CONTEXT_LIMIT
    assert budget["conversation_tokens"] > 0
    assert budget["max_recent_turns"] > 0


def test_settings_ui_exposes_context_cap() -> None:
    js = Path("src/web/static/js/components/settingsPanel.js").read_text()
    assert "pref-llm-max-context" in js
    assert "_llmContextCapPayload" in js
    assert "max_context_tokens" in js
    assert "/api/settings/llm/context" in js
    assert "endpoint_context_reported" in js
    assert "endpointContextReported" in js


def main() -> None:
    test_endpoint_max_is_default()
    test_user_cap_clamps_to_endpoint()
    test_user_cap_above_unreported_fallback_is_honored()
    test_too_small_cap_is_raised_to_minimum()
    test_settings_ui_exposes_context_cap()
    print("Round 88 context-window setting traces passed")


if __name__ == "__main__":
    main()
