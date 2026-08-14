"""Normalize heterogeneous action-handler responses into truthful receipts."""

from __future__ import annotations

from typing import Any

from src.core.models import ActionResult


class ActionResultNormalizer:
    """Convert handler return values into internally consistent ``ActionResult`` objects."""

    _FAILURE_STATUSES = {
        "failed",
        "failure",
        "error",
        "blocked",
        "not_found",
        "not found",
        "cancelled_before_start",
        "uncertain",
        "in_progress",
    }

    def normalize(self, raw: Any, action_name: str) -> ActionResult:
        """Return a receipt whose status, error, and success flag cannot contradict."""
        if isinstance(raw, ActionResult):
            result = raw.model_copy(deep=True)
            result.action_name = result.action_name or action_name
            return self._cohere(result)

        data = self._as_dict(raw)
        status = str(data.get("status") or "succeeded").strip() or "succeeded"
        error = self._error_text(data.get("error"))
        explicit_ok = data.get("ok") if isinstance(data.get("ok"), bool) else None
        explicit_success = data.get("success") if isinstance(data.get("success"), bool) else None
        status_failed = self._status_is_failure(status)
        ok = explicit_ok if explicit_ok is not None else explicit_success
        if ok is None:
            ok = not bool(error) and not status_failed
        elif error or status_failed:
            ok = False
        if not ok and not error:
            error = self._fallback_error(data, status)
        return self._cohere(ActionResult(
            ok=bool(ok),
            data=data,
            error=error,
            action_name=action_name,
            status=status,
        ))

    def _cohere(self, result: ActionResult) -> ActionResult:
        status = str(result.status or "").strip() or ("succeeded" if result.ok else "failed")
        status_failed = self._status_is_failure(status)
        if result.error or status_failed:
            result.ok = False
        if not result.ok and status.lower() in {"succeeded", "success", "ok"}:
            status = "failed"
        if result.ok and status_failed:
            result.ok = False
        if not result.ok and not result.error:
            result.error = f"Action ended with status '{status}'."
        result.status = status
        return result

    def _as_dict(self, raw: Any) -> dict[str, Any]:
        if raw is None:
            return {}
        if isinstance(raw, dict):
            return dict(raw)
        if hasattr(raw, "model_dump"):
            dumped = raw.model_dump()
            return dict(dumped) if isinstance(dumped, dict) else {"value": dumped}
        return {"value": raw}

    def _status_is_failure(self, status: str) -> bool:
        normalized = status.strip().lower().replace("-", "_")
        return normalized in self._FAILURE_STATUSES or normalized.startswith(("fail", "error", "block"))

    @staticmethod
    def _error_text(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _fallback_error(data: dict[str, Any], status: str) -> str:
        message = data.get("message") or data.get("reason")
        if message:
            return str(message)
        return f"Action ended with status '{status}'."
