"""Generic request/turn trace context shared across runtime subsystems.

This context is deliberately independent from LLM telemetry. Search providers,
chat lifecycle logging, tools, and model calls all need the same authoritative
session/turn identity so one user operation can be reconstructed without
inferring relationships from timestamps.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import time
from typing import Iterator


_SESSION_ID: ContextVar[str | None] = ContextVar("ljs_operation_session_id", default=None)
_TURN_ID: ContextVar[str | None] = ContextVar("ljs_operation_turn_id", default=None)
_STARTED_MONOTONIC: ContextVar[float | None] = ContextVar("ljs_operation_started_monotonic", default=None)


class OperationTraceContext:
    """Bind and expose the stable identity of one user-visible operation."""

    @staticmethod
    @contextmanager
    def bind(*, session_id: str | None, turn_id: str | None) -> Iterator[None]:
        """Bind session/turn identity for all nested asynchronous work."""
        session_token = _SESSION_ID.set(session_id)
        turn_token = _TURN_ID.set(turn_id)
        started_token = _STARTED_MONOTONIC.set(time.monotonic())
        try:
            yield
        finally:
            _STARTED_MONOTONIC.reset(started_token)
            _TURN_ID.reset(turn_token)
            _SESSION_ID.reset(session_token)

    @staticmethod
    def current() -> tuple[str | None, str | None]:
        """Return the current session and turn identifiers."""
        return _SESSION_ID.get(), _TURN_ID.get()

    @staticmethod
    def fields() -> dict[str, object]:
        """Return compact trace fields suitable for structured logs."""
        session_id, turn_id = OperationTraceContext.current()
        started = _STARTED_MONOTONIC.get()
        elapsed_ms = None
        if started is not None:
            elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
        return {
            "session_id": session_id,
            "turn_id": turn_id,
            "turn_elapsed_ms": elapsed_ms,
        }


class OperationTraceLogEnricher:
    """Attach current operation identity to Loguru records.

    File logging can then correlate ordinary subsystem rows (Jackett, tools,
    metadata, queueing) with the same session/turn lifecycle without every
    module manually threading IDs through logger calls. Background work simply
    receives ``-`` markers.
    """

    def __call__(self, record: dict[str, object]) -> bool:
        """Populate stable trace fields expected by the LJS file-log format."""
        trace = OperationTraceContext.fields()
        extra = record.setdefault("extra", {})
        if isinstance(extra, dict):
            extra["session_id"] = trace.get("session_id") or "-"
            extra["turn_id"] = trace.get("turn_id") or "-"
            elapsed = trace.get("turn_elapsed_ms")
            extra["turn_elapsed_ms"] = elapsed if elapsed is not None else "-"
        return True
