"""Web-layer collaborators for live LLM diagnostics."""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any, Iterable

from src.utils.log_sanitizer import redact_secrets


class LLMActivityBroadcaster:
    """Forward compact LLM lifecycle events onto the browser event bus."""

    def __init__(self, event_bus: Any) -> None:
        self._event_bus = event_bus

    def __call__(self, event: dict[str, Any]) -> None:
        """Broadcast one already-sanitized activity event."""
        self._event_bus.emit("llm_activity", {"event": event})


class LLMDiagnosticLogReader:
    """Read bounded, secret-redacted LLM diagnostic logs for the local UI."""

    _SOURCES = {
        "context": Path("logs/llm_context.log"),
        "responses": Path("logs/llm_raw_response.log"),
        "routing": Path("logs/structured_replies.log"),
        "turns": Path("logs/chat_turns.log"),
        "searches": Path("logs/searches.log"),
        "application": Path("logs/ljs.log"),
    }
    _APPLICATION_MARKERS = (
        "llm",
        "litellm",
        "nvidia nim",
        "taskllmclient",
        "intent rout",
        "context budget",
        "prompt token",
        "completion token",
        "provider attempt",
        "model call",
        "turn",
        "cancel",
        "search",
    )

    def read(self, source: str, lines: int) -> dict[str, Any]:
        """Return a bounded log tail without loading the complete file in memory."""
        selected = str(source or "context").strip().lower()
        if selected not in self._SOURCES:
            raise ValueError(f"Unsupported LLM log source: {selected}")
        requested = max(1, min(int(lines or 400), 2000))
        path = self._SOURCES[selected]
        if not path.exists():
            return self._response(selected, path, requested, ["Log file not found."])
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            rows = (
                self._application_tail(handle, requested)
                if selected == "application"
                else self._raw_tail(handle, requested)
            )
        return self._response(selected, path, requested, rows)

    def _raw_tail(self, lines: Iterable[str], requested: int) -> list[str]:
        """Return a bounded tail for context, response, or routing logs."""
        tail: deque[str] = deque(maxlen=requested)
        for line in lines:
            tail.append(line.rstrip("\n"))
        return list(tail)

    def _application_tail(self, lines: Iterable[str], requested: int) -> list[str]:
        """Return matching app rows and nearby traceback continuations."""
        tail: deque[str] = deque(maxlen=requested)
        continuation_budget = 0
        for raw_line in lines:
            line = raw_line.rstrip("\n")
            lowered = line.casefold()
            if any(marker in lowered for marker in self._APPLICATION_MARKERS):
                tail.append(line)
                continuation_budget = 8
                continue
            if continuation_budget > 0 and self._looks_like_continuation(line):
                tail.append(line)
                continuation_budget -= 1
            else:
                continuation_budget = 0
        return list(tail)

    @staticmethod
    def _response(source: str, path: Path, requested: int, rows: list[str]) -> dict[str, Any]:
        """Build one consistent authenticated API response."""
        return {
            "ok": True,
            "source": source,
            "path": str(path),
            "logs": [redact_secrets(line) for line in rows],
            "line_limit": requested,
        }

    @staticmethod
    def _looks_like_continuation(line: str) -> bool:
        """Return whether a row resembles a traceback or wrapped diagnostic."""
        stripped = str(line or "").lstrip()
        return bool(
            not stripped
            or line.startswith((" ", "\t"))
            or stripped.startswith(("Traceback", "File ", "During handling", "Caused by"))
        )
