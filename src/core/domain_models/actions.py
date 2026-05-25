"""Action command/result models for the unified action gateway."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_serializer, model_validator

from src.core.domain_models.enums import ActionSource

# --- Action Models ---


class ActionCommand(BaseModel):
    """A typed command for the unified action pipeline.

    Encapsulates everything needed to execute, audit, and trace an action:
    the action name, its arguments, the source, and identity information.
    """

    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    source: ActionSource
    user_id: str | None = None
    session_id: str | None = None


class ActionResult(BaseModel):
    """Result of executing an action through the gateway.

    Always includes an ok flag. On success, data carries the handler's
    return value. On failure, error describes what went wrong.
    """

    ok: bool
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    action_name: str = ""

