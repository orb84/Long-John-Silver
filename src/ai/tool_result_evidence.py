"""Structural extraction of stable public handles from private agent tool results."""

from __future__ import annotations

from typing import Any

from src.core.models import InvocationEvidence


class ToolResultEvidenceCollector:
    """Collect non-secret stable IDs without interpreting tool prose or prompts."""

    @classmethod
    def record(cls, result: Any, evidence: InvocationEvidence | None) -> None:
        """Record stable result/candidate handles and persisted command receipts."""
        if evidence is None:
            return
        cls._walk(result, evidence)

    @classmethod
    def _walk(cls, value: Any, evidence: InvocationEvidence) -> None:
        if isinstance(value, dict):
            cls._record_mapping(value, evidence)
            for child in value.values():
                cls._walk(child, evidence)
            return
        if isinstance(value, (list, tuple)):
            for child in value:
                cls._walk(child, evidence)

    @classmethod
    def _record_mapping(cls, value: dict[str, Any], evidence: InvocationEvidence) -> None:
        """Record structural continuation and stable-handle evidence in occurrence order."""
        status = str(value.get("status") or "").strip().casefold()
        if (
            value.get("clarification_required") is True
            or value.get("requires_confirmation") is True
            or value.get("confirmation_required") is True
            or status in {"needs_confirmation", "needs_input", "clarification_required"}
        ):
            evidence.needs_input = True

        cls._append_unique(evidence.result_set_ids, value.get("result_set_id"))
        cls._append_unique(evidence.candidate_ids, value.get("candidate_id"))

        candidate_ids = value.get("candidate_ids")
        if isinstance(candidate_ids, (list, tuple, set, frozenset)):
            for candidate_id in candidate_ids:
                cls._append_unique(evidence.candidate_ids, candidate_id)

        receipt = value.get("command_receipt")
        if isinstance(receipt, dict) and receipt.get("receipt_persisted") is True:
            cls._append_unique(evidence.action_receipt_ids, receipt.get("command_id"))

    @staticmethod
    def _append_unique(target: list[str], value: object) -> None:
        normalized = str(value or "").strip()
        if normalized and normalized not in target:
            target.append(normalized)
