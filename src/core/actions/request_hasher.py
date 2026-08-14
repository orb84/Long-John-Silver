"""Stable request fingerprints for command idempotency."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from src.core.models import ActionCommand


class ActionRequestHasher:
    """Hash complete command semantics without persisting sensitive arguments."""

    def fingerprint(self, command: ActionCommand) -> str:
        """Return a stable hash for the complete scoped command request."""
        payload = {
            "action_name": command.name,
            "arguments": self._normalize(command.arguments),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _normalize(self, value: Any) -> Any:
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, dict):
            return {str(key): self._normalize(child) for key, child in sorted(value.items(), key=lambda item: str(item[0]))}
        if isinstance(value, (list, tuple)):
            return [self._normalize(child) for child in value]
        if isinstance(value, set):
            return sorted(self._normalize(child) for child in value)
        if hasattr(value, "model_dump"):
            return self._normalize(value.model_dump())
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return str(value)
