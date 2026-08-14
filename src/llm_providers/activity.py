"""In-memory LLM activity telemetry for user-facing diagnostics.

The monitor records every task-aware model call at the one provider boundary used
by the assistant, planners, routers, and rankers. It keeps exact message/tool
payloads for a bounded local history and emits compact lifecycle events so the UI
can surface retries, timeouts, failures, and cancellations immediately.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timezone
import json
import threading
import time
import uuid
from typing import Any, Callable, Iterator

from src.utils.log_sanitizer import redact_secrets
from src.core.operation_trace import OperationTraceContext


_BUDGET_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar("ljs_llm_budget_context", default=None)


class LLMActivityValueCodec:
    """Normalize telemetry values without leaking provider-specific objects."""

    @staticmethod
    def utc_now() -> str:
        """Return an ISO-8601 UTC timestamp for telemetry records."""
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def json_size(value: Any) -> int:
        """Return the serialized character size of an arbitrary payload."""
        try:
            return len(json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":")))
        except Exception:
            return len(str(value or ""))

    @staticmethod
    def safe_copy(value: Any) -> Any:
        """Return a detached JSON-compatible copy for bounded inspection."""
        try:
            return deepcopy(value)
        except Exception:
            try:
                return json.loads(json.dumps(value, ensure_ascii=False, default=str))
            except Exception:
                return str(value)

    @staticmethod
    def usage_dict(response: Any) -> dict[str, int | None]:
        """Normalize provider token-usage metadata when it is available."""
        usage = getattr(response, "usage", None)
        if usage is None and isinstance(response, dict):
            usage = response.get("usage")
        if usage is None:
            return {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}

        def read_usage(*names: str) -> int | None:
            for name in names:
                value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
                try:
                    return int(value) if value is not None else None
                except (TypeError, ValueError):
                    continue
            return None

        return {
            "prompt_tokens": read_usage("prompt_tokens", "input_tokens"),
            "completion_tokens": read_usage("completion_tokens", "output_tokens"),
            "total_tokens": read_usage("total_tokens"),
        }


class LLMActivityContext:
    """Attach a chat session and turn id to all nested LLM calls."""

    @staticmethod
    @contextmanager
    def bind(*, session_id: str | None, turn_id: str | None) -> Iterator[None]:
        """Bind session/turn identifiers to all nested runtime work."""
        with OperationTraceContext.bind(session_id=session_id, turn_id=turn_id):
            yield

    @staticmethod
    def current() -> tuple[str | None, str | None]:
        """Return the session and turn currently attached to this task context."""
        return OperationTraceContext.current()

    @staticmethod
    @contextmanager
    def bind_budget(budget: dict[str, Any] | None) -> Iterator[None]:
        """Attach resolved hard/target context-budget metadata to one call."""
        token = _BUDGET_CONTEXT.set(LLMActivityValueCodec.safe_copy(budget or {}))
        try:
            yield
        finally:
            _BUDGET_CONTEXT.reset(token)

    @staticmethod
    def current_budget() -> dict[str, Any]:
        """Return the current call's detached budget metadata."""
        value = _BUDGET_CONTEXT.get()
        return LLMActivityValueCodec.safe_copy(value or {})


class LLMActivityEventFactory:
    """Build compact UI-safe lifecycle events from activity records."""

    @staticmethod
    def build(
        record: dict[str, Any],
        *,
        event_type: str,
        severity: str = "info",
        title: str,
        message: str,
        attempt: int | None = None,
        max_attempts: int | None = None,
    ) -> dict[str, Any]:
        """Return an event without exact prompts, schemas, keys, or provider payloads."""
        return {
            "event_id": uuid.uuid4().hex,
            "event_type": event_type,
            "severity": severity,
            "title": title,
            "message": redact_secrets(str(message or ""))[:1000],
            "call_id": record.get("call_id"),
            "session_id": record.get("session_id"),
            "turn_id": record.get("turn_id"),
            "task": record.get("task"),
            "provider": record.get("provider"),
            "model": record.get("model"),
            "attempt": attempt,
            "max_attempts": max_attempts,
            "created_at": LLMActivityValueCodec.utc_now(),
        }

    @staticmethod
    def failure_kind(error: str | None) -> tuple[str, str]:
        """Classify a compact failure label for cards and filters."""
        text = str(error or "").casefold()
        if "timeout" in text or "timed out" in text:
            return "attempt_timeout", "LLM request timed out"
        if "context" in text and ("limit" in text or "ceiling" in text or "token" in text):
            return "context_rejected", "LLM context was rejected"
        if "rate limit" in text or "429" in text:
            return "rate_limited", "LLM provider rate-limited the request"
        if "auth" in text or "401" in text or "403" in text:
            return "provider_auth_error", "LLM provider authentication failed"
        return "attempt_failed", "LLM request attempt failed"


class LLMActivityMonitor:
    """Bounded, thread-safe history of model calls and retries."""

    def __init__(self, *, max_history: int = 40) -> None:
        self._max_history = max(5, int(max_history))
        self._lock = threading.RLock()
        self._calls: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []
        self._events: list[dict[str, Any]] = []
        self._max_events = self._max_history * 12
        self._event_sink: Callable[[dict[str, Any]], None] | None = None

    def set_event_sink(self, sink: Callable[[dict[str, Any]], None] | None) -> None:
        """Attach the UI/event-bus lifecycle sink used for immediate problem cards."""
        with self._lock:
            self._event_sink = sink

    def start_call(
        self,
        *,
        task: str,
        provider: str,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        stream: bool,
        generation: dict[str, Any] | None = None,
        budget: dict[str, Any] | None = None,
    ) -> str:
        """Register one provider-bound call and return its stable activity ID."""
        call_id = uuid.uuid4().hex
        session_id, turn_id = LLMActivityContext.current()
        message_chars = LLMActivityValueCodec.json_size(messages)
        tool_chars = LLMActivityValueCodec.json_size(tools or [])
        estimated_prompt_tokens = int(((message_chars / 3.4) + (tool_chars / 2.2)) * 1.15)
        record = {
            "call_id": call_id,
            "session_id": session_id,
            "turn_id": turn_id,
            "task": str(task or ""),
            "provider": str(provider or "default"),
            "model": str(model or ""),
            "stream": bool(stream),
            "status": "running",
            "started_at": LLMActivityValueCodec.utc_now(),
            "finished_at": None,
            "duration_seconds": None,
            "message_count": len(messages or []),
            "tool_count": len(tools or []),
            "message_chars": message_chars,
            "tool_schema_chars": tool_chars,
            "estimated_prompt_tokens": estimated_prompt_tokens,
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "generation": LLMActivityValueCodec.safe_copy(generation or {}),
            "budget": LLMActivityValueCodec.safe_copy(
                budget if budget is not None else LLMActivityContext.current_budget()
            ),
            "attempts": [],
            "error": None,
            "cancelled": False,
            "context": {
                "messages": LLMActivityValueCodec.safe_copy(messages),
                "tools": LLMActivityValueCodec.safe_copy(tools or []),
            },
            "_started_monotonic": time.monotonic(),
        }
        with self._lock:
            self._calls[call_id] = record
            self._order.append(call_id)
            self._prune_locked()
        self._emit(LLMActivityEventFactory.build(
            record,
            event_type="call_started",
            title="LLM call started",
            message=f"{record['task']} started on {record['model'] or 'the selected model'}.",
        ))
        return call_id

    def record_attempt(
        self,
        call_id: str,
        *,
        attempt: int,
        max_attempts: int,
        status: str,
        error: str | None = None,
    ) -> None:
        """Record one provider attempt and emit retries/failures immediately."""
        event: dict[str, Any] | None = None
        with self._lock:
            record = self._calls.get(call_id)
            if not record:
                return
            attempts = record.setdefault("attempts", [])
            now = time.monotonic()
            prior = attempts[-1] if attempts else None
            if status == "started":
                attempts.append({
                    "attempt": int(attempt),
                    "max_attempts": int(max_attempts),
                    "status": "running",
                    "started_at": LLMActivityValueCodec.utc_now(),
                    "finished_at": None,
                    "duration_seconds": None,
                    "error": None,
                    "_started_monotonic": now,
                })
                if int(attempt) > 1:
                    event = LLMActivityEventFactory.build(
                        record,
                        event_type="retry_started",
                        severity="warning",
                        title="Retrying LLM request",
                        message=f"Starting attempt {attempt} of {max_attempts} for {record.get('task') or 'LLM task'}.",
                        attempt=int(attempt),
                        max_attempts=int(max_attempts),
                    )
            else:
                target = prior if prior and int(prior.get("attempt") or 0) == int(attempt) else None
                if target is None:
                    target = {
                        "attempt": int(attempt),
                        "max_attempts": int(max_attempts),
                        "started_at": None,
                        "_started_monotonic": now,
                    }
                    attempts.append(target)
                started = float(target.pop("_started_monotonic", now))
                target.update({
                    "status": status,
                    "finished_at": LLMActivityValueCodec.utc_now(),
                    "duration_seconds": round(max(0.0, now - started), 3),
                    "error": str(error)[:2000] if error else None,
                })
                if status == "failed":
                    event_type, title = LLMActivityEventFactory.failure_kind(error)
                    event = LLMActivityEventFactory.build(
                        record,
                        event_type=event_type,
                        severity="warning" if int(attempt) < int(max_attempts) else "error",
                        title=title,
                        message=(
                            f"Attempt {attempt} of {max_attempts} for {record.get('task') or 'LLM task'} failed: "
                            f"{str(error or 'unknown provider error')[:500]}"
                        ),
                        attempt=int(attempt),
                        max_attempts=int(max_attempts),
                    )
        if event:
            self._emit(event)

    def record_configuration_change(
        self,
        call_id: str,
        *,
        old_revision: int,
        new_revision: int,
    ) -> None:
        """Record that an active call was invalidated by a route change."""
        with self._lock:
            record = self._calls.get(call_id)
            if not record:
                return
            event = LLMActivityEventFactory.build(
                record,
                event_type="route_configuration_changed",
                severity="warning",
                title="LLM route changed",
                message=(
                    f"Settings changed from revision {old_revision} to {new_revision}. "
                    "The active call using the old model was stopped; resend the request."
                ),
            )
        self._emit(event)

    def finish_call(
        self,
        call_id: str,
        *,
        status: str = "completed",
        response: Any = None,
        error: BaseException | str | None = None,
    ) -> None:
        """Finalize a call with latency, usage, failure, or cancellation details."""
        event: dict[str, Any] | None = None
        with self._lock:
            record = self._calls.get(call_id)
            if not record or record.get("status") != "running":
                return
            started = float(record.pop("_started_monotonic", time.monotonic()))
            usage = LLMActivityValueCodec.usage_dict(response)
            record.update(usage)
            record.update({
                "status": status,
                "finished_at": LLMActivityValueCodec.utc_now(),
                "duration_seconds": round(max(0.0, time.monotonic() - started), 3),
                "error": str(error)[:4000] if error else None,
                "cancelled": status == "cancelled",
            })
            for attempt in record.get("attempts") or []:
                attempt.pop("_started_monotonic", None)
            if status == "failed":
                _attempt_kind, attempt_title = LLMActivityEventFactory.failure_kind(str(error or ""))
                event = LLMActivityEventFactory.build(
                    record,
                    event_type="call_failed",
                    severity="error",
                    title=attempt_title.replace("attempt", "call"),
                    message=f"{record.get('task') or 'LLM task'} failed after {record['duration_seconds']}s: {str(error or 'unknown error')[:500]}",
                )
            elif status == "cancelled":
                event = LLMActivityEventFactory.build(
                    record,
                    event_type="call_cancelled",
                    severity="info",
                    title="LLM request cancelled",
                    message=f"{record.get('task') or 'LLM task'} was cancelled.",
                )
        if event:
            self._emit(event)

    def snapshot(self, *, limit: int = 20, include_context: bool = False) -> dict[str, Any]:
        """Return a newest-first bounded activity summary for dashboard polling."""
        with self._lock:
            ids = list(reversed(self._order[-max(1, min(int(limit or 20), self._max_history)):]))
            calls = [
                self._public_record(self._calls[call_id], include_context=include_context)
                for call_id in ids
                if call_id in self._calls
            ]
        active = [row for row in calls if row.get("status") == "running"]
        with self._lock:
            events = [
                LLMActivityValueCodec.safe_copy(event)
                for event in self._events[-min(self._max_events, max(20, int(limit or 20) * 8)):]
            ]
        return {
            "ok": True,
            "active_count": len(active),
            "active": active,
            "last_call": calls[0] if calls else None,
            "calls": calls,
            "events": events,
        }

    def status(self, call_id: str) -> str | None:
        """Return one call status without copying its prompt or tool payload."""
        with self._lock:
            record = self._calls.get(str(call_id or ""))
            return str(record.get("status")) if record else None

    def detail(self, call_id: str) -> dict[str, Any] | None:
        """Return one call including its exact local messages and tool schemas."""
        with self._lock:
            record = self._calls.get(str(call_id or ""))
            return self._public_record(record, include_context=True) if record else None

    def _emit(self, event: dict[str, Any]) -> None:
        """Persist and deliver one compact lifecycle event safely."""
        safe_event = LLMActivityValueCodec.safe_copy(event)
        with self._lock:
            self._events.append(safe_event)
            if len(self._events) > self._max_events:
                del self._events[:len(self._events) - self._max_events]
            sink = self._event_sink
        if not sink:
            return
        try:
            sink(LLMActivityValueCodec.safe_copy(safe_event))
        except Exception:
            # Telemetry presentation must never break the provider boundary.
            return

    def _public_record(self, record: dict[str, Any], *, include_context: bool) -> dict[str, Any]:
        row = {
            key: LLMActivityValueCodec.safe_copy(value)
            for key, value in record.items()
            if not key.startswith("_")
        }
        if row.get("error"):
            row["error"] = redact_secrets(str(row["error"]))
        for attempt in row.get("attempts") or []:
            if isinstance(attempt, dict):
                if attempt.get("error"):
                    attempt["error"] = redact_secrets(str(attempt["error"]))
                for key in list(attempt):
                    if str(key).startswith("_"):
                        attempt.pop(key, None)
        if not include_context:
            row.pop("context", None)
        if row.get("status") == "running":
            started = float(record.get("_started_monotonic", time.monotonic()))
            row["duration_seconds"] = round(max(0.0, time.monotonic() - started), 3)
            attempts = row.get("attempts") or []
            if attempts and attempts[-1].get("status") == "running":
                raw_attempt = (record.get("attempts") or [])[-1]
                attempt_started = float(raw_attempt.get("_started_monotonic", time.monotonic()))
                attempts[-1]["duration_seconds"] = round(max(0.0, time.monotonic() - attempt_started), 3)
        return row

    def _prune_locked(self) -> None:
        while len(self._order) > self._max_history:
            removable_index = next(
                (
                    i
                    for i, call_id in enumerate(self._order)
                    if self._calls.get(call_id, {}).get("status") != "running"
                ),
                None,
            )
            if removable_index is None:
                break
            call_id = self._order.pop(removable_index)
            self._calls.pop(call_id, None)
