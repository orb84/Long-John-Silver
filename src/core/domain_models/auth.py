"""Authentication configuration models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_serializer, model_validator


# --- Auth Configuration ---


class AuthConfig(BaseModel):
    """Configuration for JWT-based web authentication.

    Attributes:
        secret_key: HMAC-SHA256 signing key for JWT tokens.
        allow_insecure_dev_secret: If True, allows a warning-only fallback
            when the env var is unset (dev mode).
        token_expiry_minutes: Lifetime of issued JWT tokens.
    """

    secret_key: str
    allow_insecure_dev_secret: bool = False
    token_expiry_minutes: int = 10080

