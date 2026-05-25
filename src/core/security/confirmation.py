"""
Two-phase confirmation service for risky LJS actions.

Destructive operations should first produce a receipt with a short-lived token;
only an exact matching confirmation may execute the operation later.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from src.core.models import ActionReceipt, SecurityConfirmationRequest


class SecurityConfirmationService:
    """In-memory confirmation-token service for UI and LLM action gates."""

    def __init__(self) -> None:
        """Initialize an empty confirmation store."""
        self._requests: dict[str, SecurityConfirmationRequest] = {}

    def create_request(
        self,
        action_name: str,
        payload: dict[str, Any],
        category_id: str | None = None,
        affected_paths: list[str] | None = None,
        blocked_paths: list[str] | None = None,
        risk_level: str = "destructive",
        ttl_minutes: int = 15,
        user_message: str = "Please confirm this action before it can run.",
    ) -> SecurityConfirmationRequest:
        """Create and store a short-lived confirmation request."""
        request = SecurityConfirmationRequest(
            token=uuid.uuid4().hex,
            action_name=action_name,
            category_id=category_id,
            risk_level=risk_level,  # type: ignore[arg-type]
            affected_paths=affected_paths or [],
            blocked_paths=blocked_paths or [],
            payload_hash=self.payload_hash(payload),
            expires_at=datetime.utcnow() + timedelta(minutes=ttl_minutes),
            user_message=user_message,
        )
        self._requests[request.token] = request
        return request

    def verify(self, token: str, action_name: str, payload: dict[str, Any]) -> bool:
        """Return whether a token matches the exact action and payload."""
        request = self._requests.get(token)
        if not request:
            return False
        if request.expires_at < datetime.utcnow():
            self._requests.pop(token, None)
            return False
        if request.action_name != action_name:
            return False
        if request.payload_hash != self.payload_hash(payload):
            return False
        self._requests.pop(token, None)
        return True

    def receipt_for_request(self, request: SecurityConfirmationRequest) -> ActionReceipt:
        """Build a standard ActionReceipt carrying confirmation details."""
        return ActionReceipt(
            category_id=request.category_id or "",
            action_name=request.action_name,
            status="needs_confirmation",
            user_message=request.user_message,
            data={
                "confirmation_token": request.token,
                "risk_level": request.risk_level,
                "affected_paths": request.affected_paths,
                "blocked_paths": request.blocked_paths,
                "expires_at": request.expires_at.isoformat(),
            },
        )

    def payload_hash(self, payload: dict[str, Any]) -> str:
        """Hash a payload with stable key ordering for token binding."""
        encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
