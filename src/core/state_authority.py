"""Typed fact authority and reconciliation primitives.

The registry documents which subsystem may answer each operational question.
Fact verdicts keep verified absence distinct from uncertainty so history,
metadata, and conversation state cannot impersonate live or canonical truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class FactType(str, Enum):
    """Operational questions with an explicitly assigned authority."""

    LOCAL_PRESENCE = "local_presence"
    DOWNLOAD_ACTIVITY = "download_activity"
    DOWNLOAD_HISTORY = "download_history"
    AUTOMATION_PERMISSION = "automation_permission"
    CANDIDATE_SELECTION = "candidate_selection"
    ACTION_OUTCOME = "action_outcome"
    PROVIDER_AVAILABILITY = "provider_availability"


class FactConfidence(str, Enum):
    """Evidence confidence for one authority-backed fact verdict."""

    VERIFIED = "verified"
    UNKNOWN = "unknown"
    STALE = "stale"


@dataclass(frozen=True)
class AuthorityRule:
    """Authoritative and fallback sources for one operational fact type."""

    fact_type: FactType
    authority: str
    fallbacks: tuple[str, ...] = ()
    freshness: str = "current"
    absence_is_conclusive: bool = False


@dataclass(frozen=True)
class FactVerdict:
    """Observed fact value together with source and confidence provenance."""

    fact_type: FactType
    value: Any
    confidence: FactConfidence
    source: str
    reason: str = ""
    observed_at: str = ""

    @property
    def verified(self) -> bool:
        """Construct a verdict backed by the declared authoritative source."""
        return self.confidence is FactConfidence.VERIFIED

    @classmethod
    def unknown(cls, fact_type: FactType, source: str, reason: str) -> "FactVerdict":
        """Construct a verdict for a fact that cannot be verified safely."""
        return cls(fact_type=fact_type, value=None, confidence=FactConfidence.UNKNOWN, source=source, reason=reason)


class StateAuthorityRegistry:
    """Code-level authority matrix for operational facts."""

    def __init__(self) -> None:
        self._rules = {
            FactType.LOCAL_PRESENCE: AuthorityRule(
                FactType.LOCAL_PRESENCE, "canonical_library_object", ("category_scan_evidence",),
                freshness="current_scan", absence_is_conclusive=True,
            ),
            FactType.DOWNLOAD_ACTIVITY: AuthorityRule(
                FactType.DOWNLOAD_ACTIVITY, "live_downloader_projection", ("durable_active_queue",),
                freshness="live", absence_is_conclusive=False,
            ),
            FactType.DOWNLOAD_HISTORY: AuthorityRule(
                FactType.DOWNLOAD_HISTORY, "download_history", freshness="historical", absence_is_conclusive=True,
            ),
            FactType.AUTOMATION_PERMISSION: AuthorityRule(
                FactType.AUTOMATION_PERMISSION, "reconciled_item_policy", ("category_watch_plan",),
                freshness="current_policy", absence_is_conclusive=True,
            ),
            FactType.CANDIDATE_SELECTION: AuthorityRule(
                FactType.CANDIDATE_SELECTION, "pending_result_set", freshness="session_bound", absence_is_conclusive=False,
            ),
            FactType.ACTION_OUTCOME: AuthorityRule(
                FactType.ACTION_OUTCOME, "command_receipt", ("operational_event_ledger",),
                freshness="command_bound", absence_is_conclusive=False,
            ),
            FactType.PROVIDER_AVAILABILITY: AuthorityRule(
                FactType.PROVIDER_AVAILABILITY, "provider_snapshot", freshness="timestamped", absence_is_conclusive=False,
            ),
        }

    def rule_for(self, fact_type: FactType) -> AuthorityRule:
        """Return the authority rule governing a state question."""
        return self._rules[fact_type]

    def all_rules(self) -> tuple[AuthorityRule, ...]:
        """Return every declared state-authority rule."""
        return tuple(self._rules.values())

    def accepts(self, fact_type: FactType, source: str) -> bool:
        """Return whether evidence from a source is valid for a question."""
        rule = self.rule_for(fact_type)
        return source == rule.authority or source in rule.fallbacks
