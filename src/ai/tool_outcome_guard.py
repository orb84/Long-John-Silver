"""Structured action-outcome guards for agent final responses.

The LLM may summarize read-only evidence freely, but it must never contradict a
state-changing tool receipt.  This module reads only structured tool messages;
it does not inspect natural-language user or assistant text.
"""

from __future__ import annotations

import json
from typing import Any


class ToolOutcomeLedger:
    """Track the latest authoritative queue outcome within one agent turn."""

    _QUEUE_TOOL = "queue_download"
    _SEARCH_TOOL = "search_media_torrents"

    def __init__(self) -> None:
        """Initialize an empty per-turn ledger."""
        self._latest_queue_result: dict[str, Any] | None = None
        self._latest_search_result: dict[str, Any] | None = None

    def record(self, tool_name: str, result_message: dict[str, Any]) -> None:
        """Record structured search/queue outcomes needed to guard final prose."""
        name = str(tool_name or "")
        if name == self._QUEUE_TOOL:
            self._latest_queue_result = self._decode_message(result_message)
        elif name == self._SEARCH_TOOL:
            self._latest_search_result = self._decode_message(result_message)

    def required_queue_followthrough(self) -> str | None:
        """Return an exact reprompt when a complete search still requires queueing."""
        if self._latest_queue_result is not None:
            return None
        result = self._latest_search_result or {}
        contract = result.get("completion_contract")
        if not isinstance(contract, dict):
            return None
        if contract.get("follow_up_required") is not False or contract.get("action_required") != self._QUEUE_TOOL:
            return None
        arguments = contract.get("queue_download_arguments")
        if not isinstance(arguments, dict) or not arguments:
            return None
        return (
            "The previous prose was not sent because the structured search result fully covers the user's current "
            "download target and explicitly requires queue_download without another confirmation. Call queue_download "
            f"now with these exact arguments: {json.dumps(arguments, ensure_ascii=False, separators=(',', ':'))}. "
            "Do not present a menu, ask whether to proceed, or describe catalogue episode count as released count."
        )

    def unresolved_queue_failure(self) -> str | None:
        """Return exact failure detail when the latest queue attempt did not succeed."""
        result = self._latest_queue_result
        if not result:
            return None
        command_issue = self.command_receipt_issue(result)
        if command_issue:
            return command_issue
        if self._success_count(result) > 0:
            return None
        details = self._failure_details(result)
        return "; ".join(details) if details else "The queue tool returned no verified queued or active download receipt."

    def partial_queue_failure(self) -> tuple[int, str] | None:
        """Return verified success count plus failures for a partial batch receipt."""
        result = self._latest_queue_result
        if not result:
            return None
        success_count = self._success_count(result)
        details = self._failure_details(result)
        if success_count <= 0 or not details:
            return None
        return success_count, "; ".join(details)

    @staticmethod
    def _decode_message(result_message: dict[str, Any]) -> dict[str, Any]:
        """Decode the compact JSON tool-message content conservatively."""
        content = result_message.get("content") if isinstance(result_message, dict) else None
        if isinstance(content, dict):
            return content
        if not isinstance(content, str):
            return {}
        try:
            parsed = json.loads(content)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _is_verified_success(result: dict[str, Any]) -> bool:
        """Return true only for a structured queued/already-active receipt."""
        return ToolOutcomeLedger._success_count(result) > 0

    @staticmethod
    def _success_count(result: dict[str, Any]) -> int:
        """Count only successes backed by structured queue identifiers."""
        if ToolOutcomeLedger.command_receipt_issue(result):
            return 0
        try:
            queued_count = int(result.get("queued_count") or 0)
        except (TypeError, ValueError):
            queued_count = 0
        if queued_count > 0:
            return queued_count

        for key in ("download_ids", "queued"):
            rows = result.get(key)
            if isinstance(rows, list):
                identified = 0
                for row in rows:
                    if isinstance(row, dict):
                        if row.get("download_id") or row.get("id"):
                            identified += 1
                    elif str(row or "").strip():
                        identified += 1
                if identified:
                    return identified

        status = str(result.get("status") or "").strip().lower()
        if status not in {"queued", "already_active"}:
            return 0
        return 1 if result.get("download_id") else 0


    @staticmethod
    def command_receipt_issue(result: dict[str, Any]) -> str | None:
        """Return a cautious explanation when command durability is not verified."""
        receipt = result.get("command_receipt")
        if not isinstance(receipt, dict):
            return None
        status = str(receipt.get("status") or "").strip().lower()
        if receipt.get("ok") is False:
            return str(
                result.get("error")
                or receipt.get("persistence_error")
                or f"The queue command failed with status {status or 'failed'}."
            ).strip()
        if receipt.get("receipt_persisted") is not True:
            return (
                "The queue operation may have executed, but its durable command receipt "
                "was not recorded. Verify current downloads before retrying."
            )
        if status in {
            "uncertain", "in_progress", "idempotency_unavailable",
            "idempotency_conflict", "succeeded_unrecorded", "failed_unrecorded",
        }:
            return f"The queue command is not durably verified (status: {status})."
        return None

    @classmethod
    def _failure_details(cls, result: dict[str, Any]) -> list[str]:
        """Collect compact exact errors from the queue result and nested receipts."""
        details: list[str] = []
        cls._append_detail(details, result.get("error"))
        raw = result.get("raw_result")
        if isinstance(raw, dict):
            cls._append_detail(details, raw.get("error"))
        for row in result.get("errors") or []:
            if isinstance(row, dict):
                label = cls._row_label(row)
                error = str(row.get("error") or "").strip()
                cls._append_detail(details, f"{label}: {error}" if label and error else error)
        status = str(result.get("status") or "").strip().lower()
        if not details and status and status not in {"queued", "already_active", "complete", "completed", "success", "succeeded"}:
            cls._append_detail(details, f"queue status was {result.get('status')}")
        return details[:6]

    @staticmethod
    def _row_label(row: dict[str, Any]) -> str:
        """Return a descriptor-first label for one failed candidate receipt."""
        descriptor = row.get("unit_descriptor") if isinstance(row.get("unit_descriptor"), dict) else {}
        return str(descriptor.get("label") or descriptor.get("stable_key") or row.get("title") or "").strip()[:100]

    @staticmethod
    def _append_detail(details: list[str], value: Any) -> None:
        """Append a unique non-empty detail string."""
        text = str(value or "").strip()
        if text and text not in details:
            details.append(text)
