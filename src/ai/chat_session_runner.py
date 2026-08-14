"""Shared assistant chat-turn runner.

All user-facing chat surfaces must enter the agent through this class.  Web,
Discord, Telegram, WhatsApp, REST, and future bridges may adapt delivery and
formatting, but they must not maintain their own assistant-loop, timeout, or
conversation-memory rules.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Any, AsyncIterator, Literal

from src.core.models import Intent

from src.ai.assistant import AIAssistant
from src.llm_providers.activity import LLMActivityContext

ChatEventType = Literal["status", "token", "done"]


@dataclass(frozen=True)
class ChatTurnRequest:
    """One user message entering the canonical assistant conversation pipeline."""

    prompt: str
    session_id: str
    user_id: str | None = None
    turn_id: str | None = None
    first_progress_seconds: float = 5.0
    later_progress_seconds: float = 75.0
    max_status_updates: int = 3


@dataclass(frozen=True)
class ChatTurnEvent:
    """Transport-neutral event produced by one assistant turn."""

    type: ChatEventType
    content: str = ""


class ChatSessionRunner:
    """Run assistant turns once and expose transport-neutral progress events.

    This class is the single chat execution seam for all UI and communication
    bridges.  It owns the immediate acknowledgement, periodic progress pings,
    streaming assistant token consumption, and no-arbitrary-whole-turn-timeout
    policy.  Bridge code may only translate these events into platform sends.
    """

    def __init__(self, assistant: AIAssistant) -> None:
        """Create a runner for the injected assistant instance."""
        self._assistant = assistant

    async def run_events(self, request: ChatTurnRequest) -> AsyncIterator[ChatTurnEvent]:
        """Yield status/token/done events for a single assistant turn."""
        # Bind before the preflight router so every nested model call—including
        # intent classification—appears under the same user-visible turn.
        with LLMActivityContext.bind(session_id=request.session_id, turn_id=request.turn_id):
            status_task = asyncio.create_task(
                self._status_intent(request),
                name=f"assistant-route-{request.session_id}",
            )
            routing_status_sent = False
            try:
                status_intent = await asyncio.wait_for(
                    asyncio.shield(status_task),
                    timeout=max(0.1, request.first_progress_seconds),
                )
            except asyncio.TimeoutError:
                routing_status_sent = True
                yield ChatTurnEvent(
                    "status",
                    "The LLM is still classifying this request. Any timeout or retry will appear as a notification and in LLM Diagnostics.",
                )
                status_intent = await status_task
            except asyncio.CancelledError:
                await self._cancel_if_pending(status_task)
                raise
            except Exception:
                status_intent = None

            queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
            task = asyncio.create_task(
                self._consume_assistant_stream(request, queue),
                name=f"assistant-chat-{request.session_id}",
            )
            try:
                async for event in self._drain_events(
                    request,
                    queue,
                    status_intent=status_intent,
                    initial_tick=1 if routing_status_sent else 0,
                ):
                    yield event
            finally:
                await self._cancel_if_pending(task)
                await self._cancel_if_pending(status_task)

    async def collect_response(self, request: ChatTurnRequest) -> str:
        """Run a turn through the shared event pipeline and return final text."""
        parts: list[str] = []
        async for event in self.run_events(request):
            if event.type == "token":
                parts.append(event.content)
        return "".join(parts).strip()

    def format_error(self, operation: str, exc: BaseException | str) -> str:
        """Return the assistant/persona error for a failed transport turn."""
        formatter = getattr(self._assistant, "format_chat_error", None)
        if callable(formatter):
            return formatter(operation, exc)
        return f"⚠️ Error during {operation}: {exc}"

    async def _progress_message(self, prompt: str, tick: int, intent: Intent | None = None) -> str:
        """Return useful progress without spending another model invocation."""
        if intent == Intent.SEARCH:
            return "Searching the configured sources and checking the useful matches…"
        if intent == Intent.DOWNLOAD:
            return "Checking the selected release and queue state…"
        if intent == Intent.CONFIG:
            return "Checking and applying the requested configuration…"
        formatter = getattr(self._assistant, "format_progress_message", None)
        if callable(formatter):
            return formatter(prompt, tick)
        return "Still working on this request…"

    async def _status_intent(self, request: ChatTurnRequest) -> Intent | None:
        """Return preflight intent used only to decide status visibility.

        Trivial CHAT turns should not get progress pings.  Tool/action turns
        should acknowledge quickly across every bridge.  The assistant caches
        this preflight result so the actual turn uses the same routing decision.
        """
        classifier = getattr(self._assistant, "preflight_intent_for_chat_status", None)
        if not callable(classifier):
            return None
        try:
            return await classifier(request.prompt, session_id=request.session_id, user_id=request.user_id)
        except Exception:
            return None

    async def _consume_assistant_stream(
        self,
        request: ChatTurnRequest,
        queue: asyncio.Queue[tuple[str, Any]],
    ) -> None:
        try:
            async for chunk in self._assistant.run_stream(
                request.prompt,
                session_id=request.session_id,
                user_id=request.user_id,
            ):
                await queue.put(("token", chunk))
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            await queue.put(("error", exc))
        finally:
            await queue.put(("done", None))

    async def _drain_events(
        self,
        request: ChatTurnRequest,
        queue: asyncio.Queue[tuple[str, Any]],
        *,
        status_intent: Intent | None = None,
        initial_tick: int = 0,
    ) -> AsyncIterator[ChatTurnEvent]:
        tick = max(0, int(initial_tick))
        saw_answer_text = False
        should_send_status = status_intent in {Intent.SEARCH, Intent.DOWNLOAD, Intent.CONFIG}
        delay = request.first_progress_seconds
        while True:
            try:
                if delay <= 0:
                    raise asyncio.TimeoutError
                kind, payload = await asyncio.wait_for(queue.get(), timeout=delay)
            except asyncio.TimeoutError:
                if should_send_status and not saw_answer_text and tick < request.max_status_updates:
                    yield ChatTurnEvent("status", await self._progress_message(request.prompt, tick, status_intent))
                    tick += 1
                delay = self._next_progress_delay(request, tick)
                continue
            if kind == "token":
                saw_answer_text = True
                yield ChatTurnEvent("token", str(payload))
            elif kind == "error":
                raise payload
            elif kind == "done":
                yield ChatTurnEvent("done")
                return

    @staticmethod
    def _next_progress_delay(request: ChatTurnRequest, tick: int) -> float:
        """Return a progressive, capped progress interval for long turns.

        Status pings are meant to reassure the user, not become a second noisy
        conversation.  The first ping is quick; later pings back off so Discord
        and other bridges do not repeat the same-looking messages during large
        searches or slow provider calls.
        """
        if tick <= 0:
            return request.first_progress_seconds
        if tick == 1:
            return request.later_progress_seconds
        return request.later_progress_seconds * 1.75

    async def _cancel_if_pending(self, task: asyncio.Task[Any]) -> None:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
