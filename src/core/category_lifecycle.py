"""Generic category lifecycle and suggestion policy engine.

The engine is deliberately category-neutral.  It persists item-scoped processing
state, computes stable fingerprints for metadata/library/taste/suggestion inputs,
and asks the owning category for policy decisions such as the next useful check.
It does not know what a TV episode, game version, book saga, or movie upgrade is;
those meanings live behind category hooks.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Protocol

from loguru import logger

from src.core.models import CategoryItem, SuggestedActionRecord
from src.core.library_objects import CanonicalLibraryObjectBuilder


class SuggestionWorkflow(Protocol):
    """Minimal protocol implemented by category-owned suggestion workflows."""

    async def build_suggestions(self, item: CategoryItem) -> list[SuggestedActionRecord]:
        """Return suggestions for one category item without persisting them."""
        ...


@dataclass(slots=True)
class LifecycleFingerprints:
    """Stable hashes for item inputs that can invalidate processing results."""

    metadata: str = ""
    library: str = ""
    taste: str = ""
    suggestions: str = ""
    policy_version: int = 1

    def to_state_payload(self) -> dict[str, Any]:
        """Return the subset stored in ``category_item_processing_state``."""
        return {
            "metadata_fingerprint": self.metadata,
            "library_fingerprint": self.library,
            "taste_fingerprint": self.taste,
            "suggestion_fingerprint": self.suggestions,
            "policy_version": self.policy_version,
        }


@dataclass(slots=True)
class LifecycleDecision:
    """Decision for whether one item should be processed now."""

    category_id: str
    item_id: str
    should_process: bool
    reason: str
    purpose: str = "scheduled_check"
    invalidated_by: list[str] = field(default_factory=list)
    next_check_at: str | None = None
    valid_until: str | None = None
    confidence: float = 1.0
    fingerprints: LifecycleFingerprints = field(default_factory=LifecycleFingerprints)
    previous_state: dict[str, Any] | None = None


class CategoryLifecycleEngine:
    """Persist item-scoped lifecycle state and gate expensive category work.

    The scheduler and suggestion compiler call this engine before running
    provider-heavy workflows.  If fingerprints are unchanged and ``next_check_at``
    has not arrived, the previous suggestions/state remain valid and no category
    provider or LLM call is needed.
    """

    DEFAULT_POLICY_VERSION = 1

    def __init__(self, db: Any, category_registry: Any | None = None, settings_manager: Any | None = None) -> None:
        """Create a lifecycle engine from the initialized database facade."""
        self._db = db
        self._categories = category_registry
        self._settings_manager = settings_manager
        self._library_objects = CanonicalLibraryObjectBuilder(db=db, category_registry=category_registry)

    # ── Public orchestration API ──────────────────────────────────

    async def reconcile_item(self, item: CategoryItem, *, reason: str = "startup_reconcile") -> None:
        """Ensure one item has a cheap ledger row without running workflows."""
        category_id, item_id = self._item_identity(item)
        if not category_id or not item_id:
            return
        current = await self._fingerprints_for_item(item)
        previous = await self.get_processing_state(category_id, item_id)
        if previous:
            return
        decision = await self._category_next_decision(
            item,
            purpose=reason,
            invalidated_by=["new_item"],
            previous_state=None,
            fingerprints=current,
        )
        await self.record_processing_result(
            item,
            purpose=reason,
            status="reconciled",
            reason="Initial lifecycle ledger reconciliation; no provider work performed.",
            fingerprints=current,
            next_check_at=decision.next_check_at,
            valid_until=decision.valid_until,
        )

    async def should_process_item(
        self,
        item: CategoryItem,
        *,
        purpose: str = "scheduled_check",
        force: bool = False,
    ) -> LifecycleDecision:
        """Return whether a category item is due or invalidated.

        ``force`` is used for explicit user/manual refreshes.  It bypasses due
        checks while still recording the fingerprints that caused the run.
        """
        category_id, item_id = self._item_identity(item)
        current = await self._fingerprints_for_item(item)
        previous = await self.get_processing_state(category_id, item_id)
        invalidated_by = self._invalidation_reasons(current, previous)
        due_reason = self._due_reason(previous)

        should = bool(force or not previous or invalidated_by or due_reason)
        reason_parts: list[str] = []
        if force:
            reason_parts.append("manual_refresh")
        if not previous:
            reason_parts.append("new_item")
        if invalidated_by:
            reason_parts.append("invalidated:" + ",".join(invalidated_by))
        if due_reason:
            reason_parts.append(due_reason)
        if not reason_parts:
            reason_parts.append("ledger_valid")

        category_decision = await self._category_next_decision(
            item,
            purpose=purpose,
            invalidated_by=invalidated_by,
            previous_state=previous,
            fingerprints=current,
        )
        return LifecycleDecision(
            category_id=category_id,
            item_id=item_id,
            should_process=should,
            reason="; ".join(reason_parts),
            purpose=purpose,
            invalidated_by=invalidated_by,
            next_check_at=category_decision.next_check_at,
            valid_until=category_decision.valid_until,
            confidence=category_decision.confidence,
            fingerprints=current,
            previous_state=previous,
        )

    async def run_scheduled_workflow(
        self,
        item: CategoryItem,
        workflow_coro_factory: Any,
        *,
        purpose: str = "scheduled_check",
        force: bool = False,
    ) -> bool:
        """Gate one category scheduled workflow and persist the outcome.

        Args:
            item: Category-owned tracked item.
            workflow_coro_factory: Callable returning the coroutine that performs
                the actual category workflow. It is invoked only when processing
                is due or invalidated.
            purpose: Ledger purpose label.
            force: Whether this is a manual run.

        Returns:
            True if the workflow ran, false if the existing ledger was still valid.
        """
        decision = await self.should_process_item(item, purpose=purpose, force=force)
        if not decision.should_process:
            return False
        status = "success"
        reason = decision.reason
        try:
            await workflow_coro_factory()
        except Exception as exc:
            status = "failed"
            reason = f"{decision.reason}; error={exc}"
            await self.record_processing_result(
                item,
                purpose=purpose,
                status=status,
                reason=reason,
                fingerprints=decision.fingerprints,
                next_check_at=self._fallback_retry_at(hours=6),
                valid_until=None,
                invalidated_by=decision.invalidated_by,
            )
            raise
        await self.record_processing_result(
            item,
            purpose=purpose,
            status=status,
            reason=reason,
            fingerprints=await self._fingerprints_for_item(item),
            next_check_at=decision.next_check_at,
            valid_until=decision.valid_until,
            invalidated_by=decision.invalidated_by,
        )
        return True

    async def compile_suggestions_for_item(
        self,
        item: CategoryItem,
        workflow: SuggestionWorkflow,
        *,
        force: bool = False,
    ) -> int:
        """Compile suggestions only when item state is new, changed, or due."""
        decision = await self.should_process_item(item, purpose="suggestions", force=force)
        if not decision.should_process:
            return await self._active_suggestion_count(decision.category_id, decision.item_id)

        suggestions: list[SuggestedActionRecord] = []
        try:
            raw_suggestions = await workflow.build_suggestions(item)
            suggestions = self._dedupe_suggestions(raw_suggestions)
            logger.debug(
                f"Suggestion compile audit {decision.category_id}/{decision.item_id}: "
                f"reason={decision.reason!r}, raw={len(raw_suggestions)}, persisted={len(suggestions)}"
            )
            await self._db.downloads.clear_suggestions_for_item(decision.category_id, decision.item_id)
            for suggestion in suggestions:
                await self._db.downloads.upsert_suggested_action(suggestion)
            suggestion_fingerprint = self._fingerprint_records([self._suggestion_payload(s) for s in suggestions])
            current = await self._fingerprints_for_item(item, suggestion_fingerprint=suggestion_fingerprint)
            await self._replace_suggestion_state(
                item=item,
                suggestions=suggestions,
                valid_until=decision.valid_until or decision.next_check_at,
                suggestion_fingerprint=suggestion_fingerprint,
                policy_version=current.policy_version,
            )
            await self._record_suggestion_audit_event(
                decision=decision,
                raw_count=len(raw_suggestions),
                persisted_count=len(suggestions),
                suggestions=suggestions,
                suggestion_fingerprint=suggestion_fingerprint,
                policy_version=current.policy_version,
            )
            await self.record_processing_result(
                item,
                purpose="suggestions",
                status="success",
                reason=decision.reason,
                fingerprints=current,
                next_check_at=decision.next_check_at,
                valid_until=decision.valid_until,
                invalidated_by=decision.invalidated_by,
            )
        except Exception as exc:
            await self.record_processing_result(
                item,
                purpose="suggestions",
                status="failed",
                reason=f"{decision.reason}; error={exc}",
                fingerprints=decision.fingerprints,
                next_check_at=self._fallback_retry_at(hours=6),
                valid_until=None,
                invalidated_by=decision.invalidated_by,
            )
            raise
        return len(suggestions)

    async def record_processing_result(
        self,
        item: CategoryItem,
        *,
        purpose: str,
        status: str,
        reason: str,
        fingerprints: LifecycleFingerprints | None = None,
        next_check_at: str | None = None,
        valid_until: str | None = None,
        invalidated_by: list[str] | None = None,
    ) -> None:
        """Persist the latest processing state and append an event row.

        ``invalidated_by`` describes why this run happened.  It belongs in the
        immutable event history for auditing, but it should stay in the current
        state row only while a failure remains unresolved.  A successful run has
        already consumed those invalidations, so keeping them in state would make
        the next scheduler pass reprocess the same item forever.
        """
        category_id, item_id = self._item_identity(item)
        now = self._now()
        fps = fingerprints or await self._fingerprints_for_item(item)
        event_invalidations = list(invalidated_by or [])
        pending_invalidations = event_invalidations if status in {"failed", "pending"} else []
        connection = await self._connection()
        await connection.execute(
            """INSERT INTO category_item_processing_state
               (category_id, item_id, metadata_fingerprint, library_fingerprint,
                taste_fingerprint, suggestion_fingerprint, last_processed_at,
                next_check_at, next_check_reason, valid_until, policy_version,
                invalidated_by, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(category_id, item_id) DO UPDATE SET
                    metadata_fingerprint = excluded.metadata_fingerprint,
                    library_fingerprint = excluded.library_fingerprint,
                    taste_fingerprint = excluded.taste_fingerprint,
                    suggestion_fingerprint = excluded.suggestion_fingerprint,
                    last_processed_at = excluded.last_processed_at,
                    next_check_at = excluded.next_check_at,
                    next_check_reason = excluded.next_check_reason,
                    valid_until = excluded.valid_until,
                    policy_version = excluded.policy_version,
                    invalidated_by = excluded.invalidated_by,
                    updated_at = excluded.updated_at""",
            (
                category_id,
                item_id,
                fps.metadata,
                fps.library,
                fps.taste,
                fps.suggestions,
                now,
                next_check_at,
                reason,
                valid_until,
                fps.policy_version,
                json.dumps(pending_invalidations, ensure_ascii=False),
                now,
            ),
        )
        await connection.execute(
            """INSERT INTO category_item_processing_events
               (category_id, item_id, event_type, purpose, reason, status,
                metadata_fingerprint, library_fingerprint, taste_fingerprint,
                suggestion_fingerprint, policy_version, payload_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                category_id,
                item_id,
                "processing_result",
                purpose,
                reason,
                status,
                fps.metadata,
                fps.library,
                fps.taste,
                fps.suggestions,
                fps.policy_version,
                json.dumps({"invalidated_by": event_invalidations}, ensure_ascii=False),
                now,
            ),
        )
        await connection.commit()

    async def invalidate_item(
        self,
        category_id: str,
        item_id: str,
        *,
        reason: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Mark one item as due after an external event such as file deletion."""
        now = self._now()
        connection = await self._connection()
        existing = await self.get_processing_state(category_id, item_id)
        existing_invalidations = self._load_json(existing.get("invalidated_by"), []) if existing else []
        invalidated_by = list(dict.fromkeys([*existing_invalidations, reason]))
        await connection.execute(
            """INSERT INTO category_item_processing_state
               (category_id, item_id, last_processed_at, next_check_at,
                next_check_reason, invalidated_by, policy_version, updated_at)
               VALUES (?, ?, NULL, ?, ?, ?, ?, ?)
               ON CONFLICT(category_id, item_id) DO UPDATE SET
                    next_check_at = excluded.next_check_at,
                    next_check_reason = excluded.next_check_reason,
                    invalidated_by = excluded.invalidated_by,
                    updated_at = excluded.updated_at""",
            (
                category_id,
                item_id,
                now,
                reason,
                json.dumps(invalidated_by, ensure_ascii=False),
                self.DEFAULT_POLICY_VERSION,
                now,
            ),
        )
        await connection.execute(
            """INSERT INTO category_item_processing_events
               (category_id, item_id, event_type, purpose, reason, status, payload_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                category_id,
                item_id,
                "invalidated",
                "external_event",
                reason,
                "pending",
                json.dumps(payload or {}, ensure_ascii=False, default=str),
                now,
            ),
        )
        await connection.commit()

    async def get_processing_state(self, category_id: str, item_id: str) -> dict[str, Any] | None:
        """Return one lifecycle state row."""
        connection = await self._connection()
        cursor = await connection.execute(
            "SELECT * FROM category_item_processing_state WHERE category_id = ? AND item_id = ?",
            (category_id, item_id),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def reconcile_startup_ledgers(self, items: Iterable[CategoryItem]) -> int:
        """Create missing ledger rows for configured items without provider calls."""
        count = 0
        for item in items:
            if not getattr(item, "enabled", True):
                continue
            try:
                await self.reconcile_item(item, reason="startup_reconcile")
                count += 1
            except Exception as exc:
                logger.debug(f"Lifecycle startup reconcile skipped for {getattr(item, 'key', '?')}: {exc}")
        return count

    # ── Fingerprints ──────────────────────────────────────────────

    async def _fingerprints_for_item(
        self,
        item: CategoryItem,
        *,
        suggestion_fingerprint: str | None = None,
    ) -> LifecycleFingerprints:
        category_id, item_id = self._item_identity(item)
        category = self._category(category_id)
        policy_version = int(self._policy(category).get("policy_version") or self.DEFAULT_POLICY_VERSION)
        metadata_rows = []
        library_object = {}
        if getattr(self._db, "media", None):
            # These reads are intentionally cheap local ledger reads. Provider
            # refreshes belong inside category workflows and only happen after
            # this engine has decided the item is actually due.  Fingerprint the
            # category-built canonical object, not raw units, so lifecycle
            # invalidation follows the same source of truth as UI/suggestions.
            metadata_rows = await self._safe_call(self._db.media.get_category_metadata(category_id, item_id), default=[])
            library_object = await self._safe_call(
                self._library_objects.build(category_id, item_id, settings_item=item),
                default={},
            )
        taste_snapshot = None
        if getattr(self._db, "system", None) and hasattr(self._db.system, "get_taste_profile_snapshot"):
            # Taste changes should invalidate suggestions, but prompt-time taste
            # rebuilds are expensive.  Fingerprint the persisted snapshot here
            # and let taste ingestion refresh it when new evidence arrives.
            taste_snapshot = await self._safe_call(self._db.system.get_taste_profile_snapshot(None, category_id), default=None)
        suggestions = []
        if suggestion_fingerprint is None and getattr(self._db, "downloads", None):
            suggestions = await self._safe_call(
                self._db.downloads.get_suggested_actions(category_id=category_id, item_id=item_id, status="pending"),
                default=[],
            )
        return LifecycleFingerprints(
            metadata=self._fingerprint_records(metadata_rows),
            library=self._fingerprint_records([library_object] if library_object else []),
            taste=self._fingerprint_records([taste_snapshot] if taste_snapshot else []),
            suggestions=suggestion_fingerprint if suggestion_fingerprint is not None else self._fingerprint_records([
                self._suggestion_payload(s) for s in suggestions
            ]),
            policy_version=policy_version,
        )

    def _invalidation_reasons(
        self,
        current: LifecycleFingerprints,
        previous: dict[str, Any] | None,
    ) -> list[str]:
        """Compare current fingerprints with the last resolved state.

        The state row may also carry explicit pending invalidations from
        repository events such as file deletion or download completion.  Those
        explicit reasons are intentionally consumed by successful processing and
        preserved only in the events table afterward.
        """
        if not previous:
            return ["new_item"]
        reasons: list[str] = []
        comparisons = {
            "metadata_changed": (current.metadata, previous.get("metadata_fingerprint")),
            "library_changed": (current.library, previous.get("library_fingerprint")),
            "taste_changed": (current.taste, previous.get("taste_fingerprint")),
            "suggestion_policy_changed": (current.policy_version, previous.get("policy_version")),
        }
        for reason, (left, right) in comparisons.items():
            if str(left or "") != str(right or ""):
                reasons.append(reason)
        stored = self._load_json(previous.get("invalidated_by"), [])
        for reason in stored:
            if reason and reason not in reasons:
                reasons.append(str(reason))
        return reasons

    def _due_reason(self, previous: dict[str, Any] | None) -> str:
        """Return a scheduler reason when the persisted due time has arrived."""
        if not previous:
            return "new_item"
        next_check = self._parse_dt(previous.get("next_check_at"))
        if not next_check:
            return "no_next_check_at"
        if next_check <= datetime.now(timezone.utc):
            return "due_at:" + next_check.isoformat()
        return ""

    # ── Category policy decisions ─────────────────────────────────

    async def _category_next_decision(
        self,
        item: CategoryItem,
        *,
        purpose: str,
        invalidated_by: list[str],
        previous_state: dict[str, Any] | None,
        fingerprints: LifecycleFingerprints,
    ) -> LifecycleDecision:
        category_id, item_id = self._item_identity(item)
        category = self._category(category_id)
        context = {
            "now": self._now(),
            "purpose": purpose,
            "invalidated_by": invalidated_by,
            "previous_state": previous_state or {},
            "fingerprints": fingerprints.to_state_payload(),
            "policy": self._policy(category),
        }
        payload: dict[str, Any] = {}
        if category and hasattr(category, "lifecycle_decision"):
            try:
                maybe_payload = category.lifecycle_decision(item, context)
                if isinstance(maybe_payload, dict):
                    payload = maybe_payload
            except Exception as exc:
                logger.debug(f"Category lifecycle decision failed for {category_id}/{item_id}: {exc}")
        if not payload:
            payload = self._fallback_lifecycle_decision(context)
        return LifecycleDecision(
            category_id=category_id,
            item_id=item_id,
            should_process=True,
            reason=str(payload.get("reason") or "category_policy"),
            purpose=purpose,
            invalidated_by=invalidated_by,
            next_check_at=str(payload.get("next_check_at") or "") or None,
            valid_until=str(payload.get("valid_until") or payload.get("next_check_at") or "") or None,
            confidence=float(payload.get("confidence") or 1.0),
            fingerprints=fingerprints,
            previous_state=previous_state,
        )

    def _fallback_lifecycle_decision(self, context: dict[str, Any]) -> dict[str, Any]:
        policy = context.get("policy") or {}
        days = int(policy.get("default_check_interval_days") or 90)
        next_check_at = (datetime.now(timezone.utc) + timedelta(days=max(days, 1))).isoformat()
        return {
            "next_check_at": next_check_at,
            "valid_until": next_check_at,
            "reason": f"Generic category policy: next check in {days} day(s).",
            "confidence": 0.6,
        }

    def _policy(self, category: Any | None) -> dict[str, Any]:
        if category:
            try:
                settings = getattr(self._settings_manager, "settings", None) if self._settings_manager else None
                if settings is not None and hasattr(category, "lifecycle_policy_from_settings"):
                    policy = category.lifecycle_policy_from_settings(settings)
                elif hasattr(category, "lifecycle_policy"):
                    policy = category.lifecycle_policy()
                else:
                    policy = None
                if isinstance(policy, dict):
                    return policy
            except Exception:
                pass
        return {
            "policy_version": self.DEFAULT_POLICY_VERSION,
            "identity_fields": ["category_id", "item_id", "provider", "external_id"],
            "lifecycle_fields": ["status", "metadata", "library_units", "taste_snapshot"],
            "suggestion_types": ["metadata_repair", "better_release", "manual_review"],
            "invalidation_triggers": [
                "metadata_changed", "library_changed", "taste_changed",
                "download_completed", "download_failed", "manual_refresh", "policy_version_changed",
            ],
            "default_check_interval_days": 90,
            "llm_policy_description": (
                "Generic media lifecycle policy. Categories should override this with domain-specific rules."
            ),
        }

    # ── Suggestion state ──────────────────────────────────────────

    async def _replace_suggestion_state(
        self,
        *,
        item: CategoryItem,
        suggestions: list[SuggestedActionRecord],
        valid_until: str | None,
        suggestion_fingerprint: str,
        policy_version: int,
    ) -> None:
        category_id, item_id = self._item_identity(item)
        now = self._now()
        connection = await self._connection()
        await connection.execute(
            "DELETE FROM category_item_suggestion_state WHERE category_id = ? AND item_id = ?",
            (category_id, item_id),
        )
        for suggestion in suggestions:
            key = self._suggestion_key(suggestion)
            await connection.execute(
                """INSERT INTO category_item_suggestion_state
                   (category_id, item_id, suggestion_key, suggestion_type, status,
                    title, payload_json, suggestion_fingerprint, created_at,
                    valid_until, invalidated_by, policy_version, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(category_id, item_id, suggestion_key) DO UPDATE SET
                    suggestion_type = excluded.suggestion_type,
                    status = excluded.status,
                    title = excluded.title,
                    payload_json = excluded.payload_json,
                    suggestion_fingerprint = excluded.suggestion_fingerprint,
                    valid_until = excluded.valid_until,
                    invalidated_by = excluded.invalidated_by,
                    policy_version = excluded.policy_version,
                    updated_at = excluded.updated_at""",
                (
                    category_id,
                    item_id,
                    key,
                    suggestion.action_type,
                    suggestion.status,
                    suggestion.title,
                    json.dumps(self._suggestion_payload(suggestion), ensure_ascii=False, default=str),
                    suggestion_fingerprint,
                    suggestion.created_at or now,
                    valid_until,
                    "[]",
                    policy_version,
                    now,
                ),
            )
        await connection.commit()


    async def _record_suggestion_audit_event(
        self,
        *,
        decision: LifecycleDecision,
        raw_count: int,
        persisted_count: int,
        suggestions: list[SuggestedActionRecord],
        suggestion_fingerprint: str,
        policy_version: int,
    ) -> None:
        """Append durable diagnostics explaining a suggestion compilation pass.

        The normal ``suggested_actions`` rows are user-facing and mutable. This
        event is an audit breadcrumb for debugging weird suggestions after the
        fact: it records how many raw suggestions the category produced, how many
        survived deduplication, and each persisted row's title/explanation
        metadata without requiring verbose application logs to stay enabled.
        """
        now = self._now()
        payload = {
            "raw_count": raw_count,
            "persisted_count": persisted_count,
            "suggestions": [
                {
                    "action_type": getattr(s, "action_type", ""),
                    "title": getattr(s, "title", ""),
                    "priority": getattr(s, "priority", 0),
                    "metadata": self._load_json(getattr(s, "metadata_json", "{}"), {}),
                }
                for s in suggestions[:40]
            ],
            "truncated": len(suggestions) > 40,
        }
        connection = await self._connection()
        await connection.execute(
            """INSERT INTO category_item_processing_events
               (category_id, item_id, event_type, purpose, reason, status,
                suggestion_fingerprint, policy_version, payload_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                decision.category_id,
                decision.item_id,
                "suggestions_compiled",
                "suggestions",
                decision.reason,
                "success",
                suggestion_fingerprint,
                policy_version,
                json.dumps(payload, ensure_ascii=False, default=str),
                now,
            ),
        )
        await connection.commit()

    def _dedupe_suggestions(self, suggestions: list[SuggestedActionRecord]) -> list[SuggestedActionRecord]:
        """Return one stable row per lifecycle suggestion key.

        Category workflows can legitimately discover the same user-facing action
        through multiple paths: for example, a provider result and a heuristic may
        both propose the same related item, or two upgrade detectors may resolve
        to the same endpoint/body/title.  The old ``suggested_actions`` table did
        not enforce this uniqueness, but the lifecycle shadow ledger does because
        it tracks persisted suggestion validity by ``suggestion_key``.

        Deduplicating before writes keeps both stores aligned and makes scheduled
        compilation idempotent: rerunning the same category workflow refreshes the
        existing action state instead of failing with a SQLite UNIQUE constraint.
        When two rows collide, keep the higher-priority row and otherwise preserve
        the first row so ordering remains deterministic.
        """
        by_key: dict[str, SuggestedActionRecord] = {}
        order: list[str] = []
        duplicate_count = 0
        for suggestion in suggestions:
            key = self._suggestion_key(suggestion)
            existing = by_key.get(key)
            if existing is None:
                by_key[key] = suggestion
                order.append(key)
                continue
            duplicate_count += 1
            if getattr(suggestion, "priority", 0) > getattr(existing, "priority", 0):
                by_key[key] = suggestion
        if duplicate_count:
            logger.debug(f"Collapsed {duplicate_count} duplicate lifecycle suggestion(s) before persistence")
        return [by_key[key] for key in order]

    async def _active_suggestion_count(self, category_id: str, item_id: str) -> int:
        if not getattr(self._db, "downloads", None):
            return 0
        rows = await self._safe_call(
            self._db.downloads.get_suggested_actions(category_id=category_id, item_id=item_id, status="pending"),
            default=[],
        )
        return len(rows or [])

    @staticmethod
    def _suggestion_payload(suggestion: SuggestedActionRecord | Any) -> dict[str, Any]:
        if hasattr(suggestion, "model_dump"):
            data = suggestion.model_dump(mode="json")
        elif isinstance(suggestion, dict):
            data = dict(suggestion)
        else:
            data = {
                "category_id": getattr(suggestion, "category_id", ""),
                "item_id": getattr(suggestion, "item_id", ""),
                "action_type": getattr(suggestion, "action_type", ""),
                "title": getattr(suggestion, "title", ""),
            }
        # Database auto IDs/status timestamps should not churn fingerprints.
        for volatile in ("id", "created_at", "approved_at", "denied_at"):
            data.pop(volatile, None)
        return data

    def _suggestion_key(self, suggestion: SuggestedActionRecord) -> str:
        payload = self._suggestion_payload(suggestion)
        raw = self._stable_json({
            "type": payload.get("action_type"),
            "body": payload.get("body_json") or payload.get("body"),
            "endpoint": payload.get("endpoint"),
            "title": payload.get("title"),
        })
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    # ── Utilities ─────────────────────────────────────────────────

    async def _connection(self) -> Any:
        if hasattr(self._db, "get_connection"):
            connection = await self._db.get_connection()
            if connection is not None:
                return connection
        connection = getattr(self._db, "raw_connection", None)
        if connection is None:
            raise RuntimeError("Database connection is not initialized")
        return connection

    def _category(self, category_id: str) -> Any | None:
        if not self._categories:
            return None
        try:
            return self._categories.get(category_id)
        except Exception:
            return None

    @staticmethod
    async def _safe_call(awaitable: Any, default: Any) -> Any:
        try:
            return await awaitable
        except Exception:
            return default

    @staticmethod
    def _item_identity(item: CategoryItem | Any) -> tuple[str, str]:
        category_id = str(
            getattr(item, "category_id", None)
            or getattr(item, "item_type", None)
            or "media"
        )
        item_id = str(getattr(item, "key", None) or getattr(item, "item_id", None) or "").strip()
        return category_id, item_id

    @classmethod
    def _fingerprint_records(cls, records: Iterable[Any]) -> str:
        normalized = [cls._normalize_for_fingerprint(record) for record in records]
        return hashlib.sha256(cls._stable_json(normalized).encode("utf-8")).hexdigest()

    @classmethod
    def _normalize_for_fingerprint(cls, value: Any) -> Any:
        """Strip volatile fields before hashing category state.

        Fingerprints should change when meaningful metadata/library/taste values
        change, not because SQLite timestamps or provider refresh timestamps were
        rewritten during a no-op pass.
        """
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        elif not isinstance(value, (dict, list, tuple, str, int, float, bool, type(None))):
            try:
                value = dict(value)
            except Exception:
                value = str(value)
        if isinstance(value, dict):
            normalized: dict[str, Any] = {}
            for key, child in value.items():
                if key in {"created_at", "updated_at", "refreshed_at", "last_checked_at"}:
                    continue
                normalized[str(key)] = cls._normalize_for_fingerprint(child)
            return normalized
        if isinstance(value, (list, tuple)):
            return [cls._normalize_for_fingerprint(child) for child in value]
        return value

    @staticmethod
    def _stable_json(value: Any) -> str:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))

    @staticmethod
    def _load_json(value: Any, default: Any) -> Any:
        if isinstance(value, (dict, list)):
            return value
        if not value:
            return default
        try:
            return json.loads(str(value))
        except (TypeError, json.JSONDecodeError):
            return default

    @staticmethod
    def _parse_dt(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            text = str(value).replace("Z", "+00:00")
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except Exception:
            return None

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _fallback_retry_at(*, hours: int) -> str:
        return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
