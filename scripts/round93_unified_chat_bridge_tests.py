#!/usr/bin/env python3
"""Round 93 unified chat-interface regression checks.

The web UI and communication bridges must adapt to one shared assistant chat
runner.  Bridges may decide how to send status/final chunks, but they must not
own separate timeout, context, memory, or assistant-loop policies.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_shared_chat_runner_is_the_single_transport_neutral_entrypoint() -> None:
    src = read("src/ai/chat_session_runner.py")
    assert "class ChatSessionRunner" in src
    assert "ChatTurnRequest" in src
    assert "ChatTurnEvent" in src
    assert "self._assistant.run_stream" in src
    assert "format_progress_message" in src
    assert "format_chat_error" in src
    assert "timeout=120" not in src


def test_web_chat_adapts_to_shared_runner() -> None:
    src = read("src/web/app.py")
    assert "ChatSessionRunner" in src
    assert "ChatTurnRequest" in src
    assert "runner.run_events" in src
    assert "runner.collect_response" in src
    assert "deps.assistant.run(message" not in src
    assert "deps.assistant.run_stream(message" not in src


def test_discord_bridge_adapts_to_shared_runner_not_private_stream_logic() -> None:
    src = read("src/web/discord_bridge.py")
    assert "chat_runner.run_events" in src
    assert "make_chat_request" in src
    assert "self.assistant.run_stream" not in src
    assert "asyncio.wait_for" not in src
    assert "timeout=120" not in src


def test_telegram_and_whatsapp_use_same_runner_with_progress_events() -> None:
    telegram = read("src/web/telegram_bridge.py")
    whatsapp = read("src/web/whatsapp_bridge.py")
    assert "chat_runner.run_events" in telegram
    assert "make_chat_request" in telegram
    assert "self._assistant.run" not in telegram
    assert "chat_runner.run_events" in whatsapp
    assert "make_chat_request" in whatsapp
    assert "self._assistant.run" not in whatsapp


def test_comms_base_exposes_only_adapter_helpers() -> None:
    src = read("src/web/comms.py")
    assert "self._chat_runner = ChatSessionRunner(assistant)" in src
    assert "def make_chat_request" in src
    assert "def chat_error_text" in src
    assert "class CommsBridge" in src


def main() -> None:
    tests = [
        test_shared_chat_runner_is_the_single_transport_neutral_entrypoint,
        test_web_chat_adapts_to_shared_runner,
        test_discord_bridge_adapts_to_shared_runner_not_private_stream_logic,
        test_telegram_and_whatsapp_use_same_runner_with_progress_events,
        test_comms_base_exposes_only_adapter_helpers,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")


if __name__ == "__main__":
    main()
