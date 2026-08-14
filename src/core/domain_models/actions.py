"""Action command, claim, and receipt models for the unified action gateway."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from src.core.domain_models.enums import ActionSource


class CommandClaimDisposition(str, Enum):
    """Outcome of atomically claiming an idempotent command."""

    ACQUIRED = "acquired"
    REPLAY = "replay"
    IN_PROGRESS = "in_progress"
    CONFLICT = "conflict"
    UNCERTAIN = "uncertain"


class ActionCommand(BaseModel):
    """Durable mutation command shared by chat, UI, automation, and repair flows."""

    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    source: ActionSource
    user_id: str | None = None
    session_id: str | None = None
    actor: str = ""
    command_id: str = Field(default_factory=lambda: str(uuid4()))
    correlation_id: str = Field(default_factory=lambda: str(uuid4()))
    idempotency_key: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("name", "command_id", "correlation_id")
    @classmethod
    def _require_bounded_identity(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("Command identity fields cannot be empty")
        if len(normalized) > 200:
            raise ValueError("Command identity fields cannot exceed 200 characters")
        return normalized

    @field_validator("idempotency_key")
    @classmethod
    def _bound_idempotency_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        if not normalized:
            return None
        if len(normalized) > 240:
            raise ValueError("idempotency_key cannot exceed 240 characters")
        return normalized


class ActionResult(BaseModel):
    """Authoritative receipt returned by the unified mutation pipeline."""

    ok: bool
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    action_name: str = ""
    status: str = "succeeded"
    command_id: str = ""
    correlation_id: str = ""
    idempotency_key: str | None = None
    request_fingerprint: str = ""
    replayed: bool = False
    receipt_persisted: bool | None = None
    persistence_error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def _normalize_default_status(self) -> "ActionResult":
        """Prevent the default success label from contradicting ``ok=False``."""
        if not self.ok and self.status.strip().lower() in {"", "succeeded", "success", "ok"}:
            self.status = "failed"
        return self


class CommandClaimOutcome(BaseModel):
    """Result of an atomic idempotency-claim attempt."""

    disposition: CommandClaimDisposition
    owner_command_id: str
    correlation_id: str
    request_fingerprint: str
    receipt: ActionResult | None = None
    message: str | None = None
