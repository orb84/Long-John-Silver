"""
Security audit logging for LJS.

Every filesystem or shell-level operation can write compact JSONL events here,
which gives operators a durable record of what was attempted, allowed, blocked,
and executed.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from src.core.models import SecurityAuditEvent


class SecurityAuditLogger:
    """Append-only JSONL audit logger for security-sensitive operations."""

    def __init__(self, log_path: Path | str = "./data/security_audit.jsonl") -> None:
        """Initialize the logger with an application-controlled output path.

        Args:
            log_path: JSONL file used for audit events.
        """
        self._log_path = Path(log_path)

    def record(
        self,
        action_name: str,
        operation: str,
        status: str,
        risk_level: str = "read",
        category_id: str | None = None,
        paths: list[str] | None = None,
        actor: str = "system",
        source: str = "system",
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SecurityAuditEvent:
        """Write and return a security audit event.

        Args:
            action_name: Logical action or tool name.
            operation: Low-level operation, such as move, unlink, or subprocess.
            status: Result state, such as allowed, blocked, success, or failed.
            risk_level: Risk classification for the operation.
            category_id: Optional category that owns the action.
            paths: Resolved paths affected by the operation.
            actor: User or system actor label.
            source: Initiating surface, such as chat, ui, scheduler, or system.
            reason: Optional explanation.
            metadata: Optional structured metadata for debugging.

        Returns:
            The event that was written.
        """
        event = SecurityAuditEvent(
            event_id=str(uuid.uuid4()),
            actor=actor,
            source=source,
            action_name=action_name,
            category_id=category_id,
            operation=operation,
            risk_level=risk_level,  # type: ignore[arg-type]
            status=status,
            paths=paths or [],
            reason=reason,
            metadata=metadata or {},
        )
        self._write_event(event)
        return event

    def _write_event(self, event: SecurityAuditEvent) -> None:
        """Append a serialized audit event, degrading to loguru on failure."""
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as handle:
                handle.write(event.model_dump_json() + "\n")
        except OSError as exc:
            logger.warning(f"Security audit write failed: {exc}")
