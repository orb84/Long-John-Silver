"""Dependency-light executable checks for the Round 288 incident fixes."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ai.intent_router import IntentRouter
from src.core.models import Intent
from src.llm_providers.activity import LLMActivityMonitor
from src.llm_providers.call_policy import LLMCallPolicy


class Round288Checks:
    """Run the release-blocking router and diagnostics contracts."""

    @staticmethod
    async def run() -> None:
        captured = {}

        class Client:
            async def completion(self, **kwargs):
                captured.update(kwargs)
                return {"choices": [{"message": {"content": "DOWNLOAD"}}]}

        decision = await IntentRouter(llm_client=Client()).route_with_details(
            "download the available episodes of the latest season of Silo in Italian"
        )
        assert decision.intent is Intent.DOWNLOAD
        assert decision.status == "success"
        assert "max_tokens" not in captured
        assert LLMCallPolicy.resolve("intent_routing").timeout_seconds == 90.0

        events = []
        monitor = LLMActivityMonitor()
        monitor.set_event_sink(events.append)
        call_id = monitor.start_call(
            task="intent_routing", provider="nvidia_nim", model="openai/gpt-oss-120b",
            messages=[{"role": "user", "content": "route"}], tools=None, stream=False,
        )
        for attempt in (1, 2):
            monitor.record_attempt(call_id, attempt=attempt, max_attempts=2, status="started")
            monitor.record_attempt(
                call_id, attempt=attempt, max_attempts=2, status="failed",
                error="ReadTimeout: timed out",
            )
        assert len([event for event in events if event["event_type"] == "attempt_timeout"]) == 2
        assert len([event for event in events if event["event_type"] == "retry_started"]) == 1

        cards = Path("src/web/static/js/components/llmProblemCards.js").read_text(encoding="utf-8")
        panel = Path("src/web/static/js/components/llmActivityPanel.js").read_text(encoding="utf-8")
        assert "subscribe('llm_activity'" in cards
        assert "openCall(event.call_id)" in cards
        assert "LLM Diagnostics" in panel
        assert "/api/system/llm-logs" in panel


if __name__ == "__main__":
    asyncio.run(Round288Checks.run())
    print("Round 288 reasoning-router and live-diagnostics checks passed")
