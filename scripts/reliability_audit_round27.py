#!/usr/bin/env python3
"""Round 27 audit for persona-aware prompts and chat error formatting.

The audit is intentionally lightweight and avoids importing the full app graph,
so it can run in minimal sandboxes that do not have optional database drivers.
It checks the regression this round targeted: user-facing LLM prompts must carry
persona/error guidance, and deterministic chat errors must not fall back to raw
``Error: ...`` strings.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class Round27PersonaErrorAudit:
    """Validate the Round 27 persona/error integration contract."""

    def run(self) -> int:
        """Run all checks and return a shell-friendly exit code."""
        failures: list[str] = []
        failures.extend(self._check_prompt_builder_contract())
        failures.extend(self._check_error_presenter_contract())
        failures.extend(self._check_no_raw_chat_error_strings())
        failures.extend(self._check_websocket_uses_assistant_formatter())
        if failures:
            print("Round 27 persona/error audit failed:")
            print("\n".join(f"- {failure}" for failure in failures))
            return 1
        print("Round 27 persona/error audit passed.")
        return 0

    def _check_prompt_builder_contract(self) -> list[str]:
        """Ensure every final system prompt includes persona and error guidance."""
        from src.ai.prompt_builder import PromptBuilder
        from src.core.models import Intent

        failures: list[str] = []
        for intent in Intent:
            prompt = PromptBuilder("default").build_system_prompt(intent)
            if "Long John Silver" not in prompt or "Captain" not in prompt:
                failures.append(f"{intent.value} prompt missing persona text")
            if "USER-FACING VOICE CONTRACT" not in prompt:
                failures.append(f"{intent.value} prompt missing voice contract")
            if "⚠️ **Error" not in prompt or "technical detail" not in prompt:
                failures.append(f"{intent.value} prompt missing error style/detail guidance")
        return failures

    def _check_error_presenter_contract(self) -> list[str]:
        """Ensure deterministic errors are marked and useful."""
        from src.ai.error_presenter import AgentErrorPresenter

        presenter = AgentErrorPresenter("default")
        samples = [
            presenter.plan_failure("search_media_torrents", "bad integer"),
            presenter.queue_failure("queue_download did not return download_id"),
            presenter.circuit_open("AI completion"),
            presenter.exception("websocket chat", RuntimeError("boom")),
        ]
        failures: list[str] = []
        for sample in samples:
            if not sample.startswith("⚠️ **Error"):
                failures.append(f"deterministic error lacks marker: {sample}")
            if "Captain" not in sample:
                failures.append(f"deterministic error lacks persona address: {sample}")
            if "**Details:**" not in sample:
                failures.append(f"deterministic error lacks details: {sample}")
        return failures

    def _check_no_raw_chat_error_strings(self) -> list[str]:
        """Reject sterile raw errors in chat loop surfaces."""
        failures: list[str] = []
        for rel in ("src/ai/agent_loop.py", "src/ai/streaming_agent_loop.py", "src/web/app.py"):
            text = (PROJECT_ROOT / rel).read_text(encoding="utf-8")
            forbidden = ["f\"Error: {e}\"", "Assistant Error:"]
            for needle in forbidden:
                if needle in text:
                    failures.append(f"{rel} still contains raw user-facing error token {needle!r}")
        return failures

    def _check_websocket_uses_assistant_formatter(self) -> list[str]:
        """Ensure websocket exceptions use the assistant's active persona formatter."""
        text = (PROJECT_ROOT / "src/web/app.py").read_text(encoding="utf-8")
        if "format_chat_error" not in text:
            return ["websocket chat error path does not call assistant.format_chat_error"]
        return []


if __name__ == "__main__":
    sys.exit(Round27PersonaErrorAudit().run())
