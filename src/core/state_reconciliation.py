"""Read-only reconciliation diagnostics for contradictory operational state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.core.download_completion_authority import CompletedDownloadAuthority
from src.core.models import DownloadStatus


@dataclass(frozen=True)
class ReconciliationIssue:
    """Structured contradiction detected between operational authorities."""

    issue_type: str
    severity: str
    entity_id: str
    summary: str
    details: dict[str, Any]
    repair_action: str = ""


class DownloadStateReconciler:
    """Detect terminal transfer history that contradicts canonical presence."""

    def __init__(self, repository: Any, completion_authority: CompletedDownloadAuthority) -> None:
        self._repository = repository
        self._completion_authority = completion_authority

    async def inspect_recent(self, limit: int = 500) -> list[ReconciliationIssue]:
        """Find recent download-history contradictions against canonical state."""
        rows = await self._repository.get_recent_downloads(limit=limit)
        issues: list[ReconciliationIssue] = []
        for item in rows:
            if item.status is not DownloadStatus.COMPLETE:
                continue
            decision = await self._completion_authority.evaluate(
                import_context=item.import_context,
                category_id=item.category_id,
                item_name=item.item_name,
            )
            if not decision.retry_completed_row:
                continue
            issues.append(ReconciliationIssue(
                issue_type="terminal_download_canonical_target_absent",
                severity="warning",
                entity_id=item.id,
                summary=f"Completed transfer history exists but {decision.unit_label or item.item_name} is absent from the canonical library.",
                details={
                    "download_id": item.id,
                    "category_id": decision.category_id,
                    "item_id": decision.item_id,
                    "unit_label": decision.unit_label,
                    "authority": decision.as_receipt(),
                },
                repair_action="download_history_mark_stale",
            ))
        return issues
