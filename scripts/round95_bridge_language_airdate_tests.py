#!/usr/bin/env python3
"""Round 95 bridge/language/air-date regression checks.

Covers the Discord polish issues found after Round 94: trivial CHAT turns must
not receive heavy progress acknowledgements, tool/action turns should use the
shared runner's generated progress message, media download language must not
become the conversational reply language, broad future-episode questions must
not inherit a previous episode number, and upcoming air-date answers should get
an official-source cross-check when metadata may be regional/time-zone skewed.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# The full app imports Database transitively through assistant wiring.  The
# lightweight runner test does not touch the database, and this container does
# not include aiosqlite, so provide an import-only stub.
if "aiosqlite" not in sys.modules:
    sys.modules["aiosqlite"] = SimpleNamespace(Connection=object, Cursor=object)

from src.ai.chat_session_runner import ChatSessionRunner, ChatTurnRequest
from src.ai.language import detect_user_language_label
from src.ai.plan_coordinator import PlanCoordinator
from src.ai.prompt_builder import PromptBuilder
from src.core.models import Intent


class FakeToolExecutor:
    def get_definitions(self, allowed_tool_names: set[str]):
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": name,
                    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
                },
            }
            for name in sorted(allowed_tool_names)
        ]


class BroadFutureEpisodePlanLLM:
    async def completion(self, **kwargs):
        plan = {
            "intent": "SEARCH",
            "user_goal": "Determine how many episodes of For All Mankind have not yet aired.",
            "constraints": {},
            "steps": [
                {
                    "id": "lookup_show",
                    "tool_name": "metadata_lookup",
                    "arguments": {
                        "query": "For All Mankind",
                        "media_type": "tv",
                        "season": 5,
                        "include_episodes": True,
                    },
                    "depends_on": [],
                    "success_condition": "season 5 episode list with air dates is returned",
                }
            ],
        }
        return {"choices": [{"message": {"content": json.dumps(plan)}}]}


class ChatFakeAssistant:
    def __init__(self, intent: Intent, body: str = "final", stream_delay: float = 0.0) -> None:
        self.intent = intent
        self.body = body
        self.stream_delay = stream_delay
        self.generated = []

    async def preflight_intent_for_chat_status(self, prompt, session_id=None, user_id=None):
        return self.intent

    async def generate_progress_message(self, prompt, tick=0, intent=None):
        self.generated.append((prompt, tick, intent))
        return "generated progress acknowledgement"

    async def run_stream(self, prompt, session_id=None, user_id=None):
        if self.stream_delay:
            await asyncio.sleep(self.stream_delay)
        yield self.body

    def format_chat_error(self, operation, exc):
        return f"error: {operation}: {exc}"


async def test_chat_turn_does_not_emit_status_for_trivial_chat() -> None:
    runner = ChatSessionRunner(ChatFakeAssistant(Intent.CHAT, body="You’re welcome."))
    events = [event async for event in runner.run_events(ChatTurnRequest(prompt="Thanks !", session_id="discord_test"))]
    assert [event.type for event in events] == ["token", "done"]
    assert events[0].content == "You’re welcome."


async def test_action_turn_emits_generated_progress_status() -> None:
    assistant = ChatFakeAssistant(Intent.DOWNLOAD, body="Queued.", stream_delay=0.03)
    runner = ChatSessionRunner(assistant)
    request = ChatTurnRequest(prompt="grab the missing episodes", session_id="discord_test", first_progress_seconds=0.01)
    events = [event async for event in runner.run_events(request)]
    assert events[0].type == "status"
    assert events[0].content == "generated progress acknowledgement"
    assert events[1].type == "token"
    assert assistant.generated and assistant.generated[0][2] == Intent.DOWNLOAD


async def test_broad_future_episode_question_does_not_inherit_recent_episode_or_download_language() -> None:
    settings = SimpleNamespace(tracked_items=[SimpleNamespace(key="For All Mankind", language="Italian")])
    coordinator = PlanCoordinator(FakeToolExecutor(), BroadFutureEpisodePlanLLM(), settings=settings)
    context = """
ACTIVE CATEGORY LIBRARY CONTEXT PACKET:
{"category_id":"tv","matched_tracked_items":[{"key":"For All Mankind","language":"Italian"}]}
RECENT CONVERSATION HISTORY:
USER: Please grab the missing episodes from the latest season of For All Mankind.
ASSISTANT: Queued S05E05 and S05E07 for For All Mankind.
"""
    plan, _executor, prompt = await coordinator.prepare_plan(
        user_prompt="How many episodes still need to be aired ?",
        intent=Intent.SEARCH,
        system_prompt_content="system",
        allowed_tool_names={"metadata_lookup", "web_search"},
        context=context,
    )
    assert plan is not None
    metadata = next(step for step in plan.steps if step.tool_name == "metadata_lookup")
    assert metadata.arguments["query"] == "For All Mankind"
    assert metadata.arguments["season"] == 5
    assert "episode" not in metadata.arguments, "broad count question must not inherit S05E07 from history"
    assert "language" not in plan.constraints, "download language must not become a SEARCH/reply-language constraint"
    assert "language=Italian" not in prompt
    web = next(step for step in plan.steps if step.tool_name == "web_search")
    assert "For All Mankind" in web.arguments["query"]
    assert "official" in web.arguments["query"].lower()
    assert "Apple TV" in web.arguments["query"]


def test_prompt_receives_current_user_language_hint_not_item_language() -> None:
    assert detect_user_language_label("Thanks !") == "English"
    assert detect_user_language_label("How many episodes still need to be aired ?") == "English"
    prompt = PromptBuilder().build_system_prompt(Intent.SEARCH, user_language_hint="English")
    assert "CURRENT USER MESSAGE LANGUAGE HINT: English" in prompt
    assert "Do not confuse it with media/download language preferences" in prompt


def main() -> None:
    asyncio.run(test_chat_turn_does_not_emit_status_for_trivial_chat())
    asyncio.run(test_action_turn_emits_generated_progress_status())
    asyncio.run(test_broad_future_episode_question_does_not_inherit_recent_episode_or_download_language())
    test_prompt_receives_current_user_language_hint_not_item_language()
    print("PASS round95 bridge/language/airdate tests")


if __name__ == "__main__":
    main()
