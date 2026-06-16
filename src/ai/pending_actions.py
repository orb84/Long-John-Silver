"""Structured pending-action context for assistant follow-ups.

This module deliberately does not interpret the user's text. It surfaces recent
machine-readable torrent/search state (result_set_id, candidate_id, queue args)
so the LLM can understand follow-ups in any language and even after intervening
conversation turns.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from src.ai.download_context_policy import DownloadContextPolicy
from src.utils.candidate_ids import load_result_set


class PendingActionContextBuilder:
    """Build compact structured context for pending user actions."""

    def __init__(self, database: Any | None = None) -> None:
        self._db = database

    async def build_for_session(
        self,
        session_id: str | None,
        *,
        max_result_sets: int = 4,
        max_candidates_per_set: int = 8,
        current_user_prompt: str | None = None,
        intent: object | None = None,
    ) -> str:
        """Return a compact context packet for recent candidate/result sets.

        Args:
            session_id: Conversation/session identifier.
            max_result_sets: Maximum recent result sets to expose.
            max_candidates_per_set: Maximum candidates per result set.

        Returns:
            A system-prompt-ready string, or empty string if no pending state.
        """
        if not session_id or not self._db:
            return ""
        fresh_request_guard = DownloadContextPolicy.should_suppress_pending_candidates(current_user_prompt, intent)
        if fresh_request_guard:
            logger.info(
                "PendingActionContextBuilder: retaining recent candidate handles with fresh-request guard session_id={}",
                session_id,
            )
        try:
            result_sets = await self._load_recent_result_sets(session_id, max_result_sets)
        except Exception as exc:
            logger.debug(f"Failed to build pending action context: {exc}")
            return ""
        if not result_sets:
            return ""

        packets: list[dict[str, Any]] = []
        for data in result_sets:
            if not isinstance(data, dict):
                continue
            candidates = []
            for candidate in (data.get("candidates") or [])[:max_candidates_per_set]:
                candidates.append({
                    "candidate_id": candidate.get("candidate_id"),
                    "index": candidate.get("index") or candidate.get("option_index"),
                    "title": candidate.get("title"),
                    "seeders": candidate.get("seeders"),
                    "size": candidate.get("size") or candidate.get("size_bytes"),
                    "languages": candidate.get("languages") or candidate.get("language"),
                    "resolution": candidate.get("resolution"),
                    "unit_label": candidate.get("unit_label"),
                })
            packet = {
                "type": "recent_torrent_candidates",
                "result_set_id": data.get("result_set_id"),
                "name": data.get("name") or data.get("query"),
                "category_id": data.get("category_id"),
                "query": data.get("query"),
                "batch_recommendation": self._compact_batch(data.get("batch_recommendation")),
                "quality_choice_policy": self._compact_quality_choice(data.get("quality_choice_policy")),
                "llm_candidate_review": self._compact_llm_review(data.get("llm_candidate_review")),
                "recommended_candidate_id": data.get("recommended_candidate_id"),
                "fresh_request_guard": fresh_request_guard,
                "fresh_request_guard_rule": (
                    "This result set is still actionable for corrections/refinements/selections/confirmations, "
                    "but must not satisfy an unrelated fresh request unless the current message semantically refers to it."
                    if fresh_request_guard else None
                ),
                "candidates": candidates,
            }
            packets.append(packet)

        if not packets:
            return ""
        return (
            "PENDING ACTION CONTEXT (structured, not natural-language parsed):\n"
            "The following recent result sets remain actionable. If the user semantically "
            "refers to choosing, continuing, correcting, confirming, changing, or queueing one of these, "
            "the LLM should route/plan using the listed result_set_id and candidate_id values. "
            "If a packet has fresh_request_guard=true, treat it as a guarded prior workspace: do not queue "
            "from it for an unrelated new title, but do use it to understand complaints/corrections/refinements.\n"
            + json.dumps(packets, ensure_ascii=False, indent=2, default=str)
        )

    async def _load_recent_result_sets(self, session_id: str, max_result_sets: int) -> list[dict[str, Any]]:
        ids_raw = await self._db.system.get_preference(f"torrent_result_sets_{session_id}")
        ids: list[str] = []
        try:
            parsed = json.loads(ids_raw) if ids_raw else []
            if isinstance(parsed, list):
                ids = [str(value) for value in parsed if value]
        except Exception:
            ids = []

        result_sets: list[dict[str, Any]] = []
        latest = await load_result_set(self._db, session_id=session_id)
        if latest:
            result_sets.append(latest)
        for result_set_id in ids:
            if len(result_sets) >= max_result_sets:
                break
            data = await load_result_set(self._db, session_id=session_id, result_set_id=result_set_id)
            if not data:
                continue
            if any(existing.get("result_set_id") == data.get("result_set_id") for existing in result_sets):
                continue
            result_sets.append(data)
        return result_sets


    @staticmethod
    def _compact_quality_choice(policy: Any) -> dict[str, Any] | None:
        if not isinstance(policy, dict) or not policy.get("requires_user_choice"):
            return None
        return {
            "requires_user_choice": True,
            "reason": policy.get("reason"),
            "message": policy.get("message"),
            "candidate_ids": policy.get("candidate_ids"),
            "choices": [
                {
                    "candidate_id": choice.get("candidate_id"),
                    "title": choice.get("title"),
                    "resolution": choice.get("resolution"),
                    "codec": choice.get("codec"),
                    "size": choice.get("size"),
                    "seeders": choice.get("seeders"),
                    "estimated_bitrate_kbps": choice.get("estimated_bitrate_kbps"),
                    "languages": choice.get("languages"),
                }
                for choice in (policy.get("choices") or [])[:8]
                if isinstance(choice, dict)
            ],
        }

    @staticmethod
    def _compact_llm_review(review: Any) -> dict[str, Any] | None:
        if not isinstance(review, dict):
            return None
        return {
            "recommended_candidate_ids": review.get("recommended_candidate_ids"),
            "confidence": review.get("confidence"),
            "needs_user_choice": review.get("needs_user_choice"),
            "should_queue_now": review.get("should_queue_now"),
            "answer_hint": review.get("answer_hint"),
        }

    @staticmethod
    def _compact_batch(batch: Any) -> dict[str, Any] | None:
        if not isinstance(batch, dict):
            return None
        return {
            "intent": batch.get("intent"),
            "reason": batch.get("reason"),
            "result_set_id": batch.get("result_set_id"),
            "candidate_ids": batch.get("candidate_ids"),
            "queue_download_arguments": batch.get("queue_download_arguments"),
            "groups": [
                {
                    "unit": group.get("unit"),
                    "recommended_candidate_id": group.get("recommended_candidate_id"),
                    "seeders": group.get("seeders"),
                    "title": group.get("title"),
                }
                for group in (batch.get("groups") or [])[:12]
                if isinstance(group, dict)
            ],
        }
