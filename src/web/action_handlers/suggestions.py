"""
Suggestion action handlers for LJS.

Provides SuggestionsActionHandler and SuggestionBatchApprover: the
canonical places for suggestion management mutation logic invoked
from UI routers.
"""

import asyncio
import json
import re
from typing import Any

from loguru import logger

from src.core.config import SettingsManager
from src.core.database import Database
from src.core.models import ActionCommand, ActionSource
from src.core.suggestion_support import summarize_suggestion_for_agent
from src.core.scheduler import MediaScheduler
from src.core.task_supervisor import TaskSupervisor


class SuggestionsActionHandler:
    """Handlers for suggestion management actions routed through ActionGateway.

    Each method receives keyword arguments from ActionCommand.arguments
    and returns a dict wrapped into ActionResult.data.

    Dependencies (injected at composition root):
        db — Database (suggestion CRUD)
        settings_manager — SettingsManager (tracked items)
        scheduler — MediaScheduler (check category item for downloads)
        supervisor — TaskSupervisor (spawn one-shot tasks)
    """

    def __init__(self, db: Database, settings_manager: SettingsManager, scheduler: MediaScheduler, supervisor: TaskSupervisor) -> None:
        self._db = db
        self._sm = settings_manager
        self._scheduler = scheduler
        self._supervisor = supervisor


    async def list(self, category_id: str | None = None, item_id: str | None = None, limit: int = 50) -> dict:
        """List pending suggestions with their human-readable explanations.

        This read-only action is intentionally exposed to the agent so chat can
        answer questions like "why is this suggested?" using the same evidence
        shown in the UI instead of guessing from stale library context.
        """
        safe_limit = max(1, min(int(limit or 50), 100))
        suggestions = await self._db.downloads.get_suggested_actions(
            category_id=category_id or None,
            item_id=item_id or None,
            status="pending",
        )
        rows = [summarize_suggestion_for_agent(s) for s in suggestions[:safe_limit]]
        return {
            "suggestions": rows,
            "total_pending_returned": len(rows),
            "total_pending_available": len(suggestions),
            "note": "Each suggestion includes explanation/evidence from the category workflow metadata.",
        }

    async def approve(self, action_id: int) -> dict:
        """Approve a single suggestion and optionally trigger a category item check.

        Returns found=False if the action_id does not exist.
        """
        actions = await self._db.downloads.get_suggested_actions()
        action = next((a for a in actions if a.id == action_id), None)
        if not action:
            return {"found": False}

        try:
            invocation = self._workflow_invocation_from_suggestion(action)
            if invocation:
                category_id, workflow_name, arguments = invocation
                receipt = await self._scheduler.execute_category_workflow(category_id, workflow_name, arguments)
                receipt_status = str(getattr(receipt, "status", "success") or "success")
                if receipt_status == "success":
                    await self._db.downloads.set_suggested_action_status(action_id, "approved")
                else:
                    # Keep unresolved suggestions visible.  A user click that finds
                    # no candidate is feedback, not successful completion of the
                    # suggested work.
                    logger.info(f"Suggestion {action_id} left pending after {receipt_status} receipt")
                return {
                    "status": "approved" if receipt_status == "success" else receipt_status,
                    "action_id": action_id,
                    "message": getattr(receipt, "user_message", "Suggestion action submitted"),
                    "receipt": receipt.model_dump() if hasattr(receipt, "model_dump") else str(receipt),
                }
            await self._db.downloads.set_suggested_action_status(action_id, "approved")
            item = next(
                (i for i in self._sm.settings.tracked_items if i.key == action.item_name or i.key == action.item_id), None
            )
            if item:
                if self._supervisor:
                    from src.core.models import TaskCriticality
                    self._supervisor.spawn_one_shot(
                        f"check_category_item_{item.key}",
                        self._scheduler.check_item(item, force=True),
                        TaskCriticality.BEST_EFFORT,
                    )
                else:
                    asyncio.create_task(self._scheduler.check_item(item, force=True))
                return {"status": "approved", "action_id": action_id, "message": "Suggestion approved"}
        except Exception as e:
            logger.error(f"Failed to execute suggestion {action_id}: {e}")

        return {"status": "approved", "action_id": action_id}

    async def deny(self, action_id: int) -> dict:
        """Deny a single suggestion."""
        await self._db.downloads.set_suggested_action_status(action_id, "denied")
        return {"status": "denied", "action_id": action_id}

    async def approve_all(self, item_id: str) -> dict:
        """Approve pending suggestions for one item using suggestion-declared workflows.

        Batch approval is intentionally category-neutral.  Suggestions already
        carry the category action endpoint and request body produced by their
        owning workflow, so this handler must not know what a missing episode,
        book volume, game DLC, or movie upgrade means.
        """
        suggestions = await self._db.downloads.get_suggested_actions(item_id=item_id, status="pending")
        if not suggestions:
            return {"status": "ok", "message": "No pending suggestions found"}

        for suggestion in suggestions:
            await self._db.downloads.set_suggested_action_status(suggestion.id, "approved")

        executable = [suggestion for suggestion in suggestions if self._workflow_invocation_from_suggestion(suggestion)]
        # Prefer one category-owned batch suggestion when present; otherwise run
        # the unique executable suggestions.  This avoids hardcoding TV's batch
        # action while still preventing duplicate queues when a workflow emits
        # both single-item and all-items suggestions.
        batch_like = [s for s in executable if any(token in (s.action_type or "") for token in ("all", "batch", "remaining"))]
        to_execute = batch_like[:1] if batch_like else executable

        executed = 0
        last_message = "Suggestions approved"
        seen: set[tuple[str, str, str]] = set()
        for suggestion in to_execute:
            invocation = self._workflow_invocation_from_suggestion(suggestion)
            if not invocation:
                continue
            category_id, workflow_name, arguments = invocation
            dedupe_key = (category_id, workflow_name, json.dumps(arguments, sort_keys=True, default=str))
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            try:
                receipt = await self._scheduler.execute_category_workflow(category_id, workflow_name, arguments)
                executed += 1
                last_message = getattr(receipt, "user_message", last_message)
            except Exception as exc:
                logger.error(f"Failed to execute approved suggestion {getattr(suggestion, 'id', '?')}: {exc}")

        return {
            "status": "approved",
            "count": len(suggestions),
            "executed": executed,
            "message": last_message if executed else f"Approved {len(suggestions)} suggestions.",
        }

    def _workflow_invocation_from_suggestion(self, action: Any) -> tuple[str, str, dict[str, Any]] | None:
        """Extract a category workflow call from a suggestion endpoint/body.

        Suggestion producers are category-owned and already know which action
        should run.  This parser keeps approval generic by respecting that
        declared endpoint instead of branching on category IDs or action types.
        """
        endpoint = str(getattr(action, "endpoint", "") or "")
        match = re.search(r"/api/categories/([^/]+)/items/([^/]+)/actions/([^/?#]+)", endpoint)
        if not match:
            return None
        category_id = str(getattr(action, "category_id", "") or match.group(1))
        item_id = str(getattr(action, "item_id", "") or match.group(2))
        workflow_name = match.group(3)
        try:
            arguments = json.loads(getattr(action, "body_json", "{}") or "{}")
        except Exception:
            arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        arguments.setdefault("item_id", item_id)
        return category_id, workflow_name, arguments



class SuggestionBatchApprover:
    """Handles batch-approval of suggestions via ActionGateway.

    A thin wrapper that translates router-level parameters into
    ActionCommand invocations. Extracted from the SuggestionsRouter
    to keep the router focused on HTTP concerns.

    Dependencies (injected at composition root):
        action_gateway — ActionGateway instance
    """

    def __init__(self, action_gateway: Any) -> None:
        self._action_gateway = action_gateway

    async def approve_all(self, item_id: str) -> dict:
        """Approve all pending suggestions for one category item.

        Args:
            item_id: The category item key to batch-approve.

        Returns:
            Result dict from the action handler.
        """
        result = await self._action_gateway.execute(ActionCommand(
            name='suggestion_approve_all',
            arguments={'item_id': item_id},
            source=ActionSource.UI,
        ))
        if not result.ok:
            raise RuntimeError(result.error or 'Batch approval failed')
        return result.data
