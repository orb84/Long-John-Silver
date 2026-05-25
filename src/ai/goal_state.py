"""Session-scoped agent goal state.

The chat transcript is useful prose, but long-running tool tasks need compact
machine state.  This module stores a lightweight active goal per session so
follow-ups can attach to the same task without re-inflating the whole history.
It is intentionally category-neutral: categories supply capabilities and result
sets; the goal state records intent, constraints, handles, and allowed next
actions.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
import json
from typing import Any

from loguru import logger

from src.core.models import Intent
from src.utils.candidate_ids import load_result_set


@dataclass
class AgentGoalState:
    """Compact persistent state for one active conversational goal."""

    goal_id: str
    session_id: str
    intent: str
    user_goal: str
    category_id: str | None = None
    status: str = "active"
    constraints: dict[str, Any] = field(default_factory=dict)
    result_sets: list[dict[str, Any]] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_context_packet(self) -> dict[str, Any]:
        """Return a prompt-safe context packet for the LLM."""
        packet = asdict(self)
        packet["rule"] = (
            "Use this as task state, not as prose. Continue the active goal when the "
            "current message semantically refers to it. Use result_set_id/candidate_id "
            "handles rather than raw magnets or guessed JSON paths."
        )
        return packet


class AgentGoalStateManager:
    """Persist and render active goal state using the existing system store."""

    def __init__(self, database: Any | None = None) -> None:
        self._db = database

    async def build_context_and_update(
        self,
        *,
        session_id: str | None,
        user_prompt: str,
        intent: Intent,
        category_id: str | None,
    ) -> str:
        """Return previous/updated active-goal context and store current turn."""
        if not session_id or not self._db:
            return ""
        previous = await self._load(session_id)
        current = self._merge_goal(previous, session_id, user_prompt, intent, category_id)
        current.result_sets = await self._recent_result_set_summaries(session_id)
        current.next_actions = self._next_actions_for_intent(intent, bool(current.result_sets))
        await self._save(current)
        return self._format_context(previous, current)

    async def mark_result_set(self, *, session_id: str | None, result_set: dict[str, Any]) -> None:
        """Attach a newly produced result set to the active goal if possible."""
        if not session_id or not self._db or not isinstance(result_set, dict):
            return
        goal = await self._load(session_id)
        if not goal:
            return
        summary = self._result_set_summary(result_set)
        existing = [r for r in goal.result_sets if r.get("result_set_id") != summary.get("result_set_id")]
        goal.result_sets = [summary, *existing][:4]
        goal.updated_at = datetime.now(timezone.utc).isoformat()
        await self._save(goal)

    async def _load(self, session_id: str) -> AgentGoalState | None:
        try:
            raw = await self._db.system.get_preference(self._key(session_id))
        except Exception:
            return None
        if not raw:
            return None
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return AgentGoalState(**data)
        except Exception as exc:
            logger.debug(f"Failed to load active goal for {session_id}: {exc}")
        return None

    async def _save(self, goal: AgentGoalState) -> None:
        try:
            await self._db.system.set_preference(self._key(goal.session_id), json.dumps(asdict(goal), default=str))
        except Exception as exc:
            logger.debug(f"Failed to save active goal for {goal.session_id}: {exc}")

    def _merge_goal(
        self,
        previous: AgentGoalState | None,
        session_id: str,
        user_prompt: str,
        intent: Intent,
        category_id: str | None,
    ) -> AgentGoalState:
        now = datetime.now(timezone.utc).isoformat()
        if previous and self._should_continue(previous, intent, category_id):
            previous.user_goal = user_prompt if intent != Intent.CHAT else previous.user_goal
            previous.intent = intent.value
            previous.category_id = category_id or previous.category_id
            previous.status = "active"
            previous.updated_at = now
            return previous
        goal_id = f"goal_{abs(hash((session_id, user_prompt, now))) & 0xffffffff:08x}"
        return AgentGoalState(
            goal_id=goal_id,
            session_id=session_id,
            intent=intent.value,
            user_goal=user_prompt,
            category_id=category_id,
            updated_at=now,
        )

    @staticmethod
    def _should_continue(previous: AgentGoalState, intent: Intent, category_id: str | None) -> bool:
        if intent == Intent.CHAT:
            return False
        if previous.status not in {"active", "awaiting_user_choice", "searching"}:
            return False
        if previous.intent == intent.value:
            return True
        if previous.result_sets and intent in {Intent.DOWNLOAD, Intent.SEARCH}:
            return True
        if category_id and previous.category_id == category_id:
            return True
        return False

    async def _recent_result_set_summaries(self, session_id: str) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        latest = await load_result_set(self._db, session_id=session_id)
        if latest:
            summaries.append(self._result_set_summary(latest))
        try:
            raw_ids = await self._db.system.get_preference(f"torrent_result_sets_{session_id}")
            ids = json.loads(raw_ids) if raw_ids else []
        except Exception:
            ids = []
        for rid in ids[:4] if isinstance(ids, list) else []:
            data = await load_result_set(self._db, session_id=session_id, result_set_id=str(rid))
            if not data:
                continue
            summary = self._result_set_summary(data)
            if any(s.get("result_set_id") == summary.get("result_set_id") for s in summaries):
                continue
            summaries.append(summary)
            if len(summaries) >= 4:
                break
        return summaries

    @staticmethod
    def _result_set_summary(data: dict[str, Any]) -> dict[str, Any]:
        candidates = data.get("candidates") or []
        return {
            "result_set_id": data.get("result_set_id"),
            "name": data.get("name") or data.get("display_name") or data.get("query"),
            "category_id": data.get("category_id"),
            "query": data.get("query"),
            "search_scope": data.get("search_scope"),
            "candidate_count": len(candidates) if isinstance(candidates, list) else data.get("candidate_count"),
            "batch_recommendation": data.get("batch_recommendation"),
            "sample_candidate_ids": [
                c.get("candidate_id") for c in candidates[:8] if isinstance(c, dict) and c.get("candidate_id")
            ] if isinstance(candidates, list) else [],
        }

    @staticmethod
    def _next_actions_for_intent(intent: Intent, has_results: bool) -> list[str]:
        if intent == Intent.DOWNLOAD and has_results:
            return [
                "queue_download by candidate_id/result_set_id when choice is clear",
                "inspect/request more candidate detail when coverage is ambiguous",
                "search_media_torrents with a narrower search_scope if the current set is insufficient",
                "ask the user to choose only when multiple plausible candidates remain",
            ]
        if intent == Intent.DOWNLOAD:
            return [
                "use category context/enquire_about_media if target state is unclear",
                "call search_media_torrents with literal name/constraints/search_scope",
                "do not invent internal JSON placeholders",
            ]
        if intent == Intent.SEARCH:
            return ["use metadata_lookup for media facts", "use web/research tools only when metadata is insufficient"]
        return []

    @staticmethod
    def _format_context(previous: AgentGoalState | None, current: AgentGoalState) -> str:
        packet = {"active_goal": current.to_context_packet()}
        if previous and previous.goal_id != current.goal_id:
            packet["previous_goal"] = previous.to_context_packet()
        return "ACTIVE GOAL STATE (structured):\n" + json.dumps(packet, ensure_ascii=False, indent=2, default=str)

    @staticmethod
    def _key(session_id: str) -> str:
        return f"agent_active_goal_{session_id}"
