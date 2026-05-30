"""
Suggestions router for LJS.

Handles suggested action listing, approval, denial, and batch approval
for category-owned suggested actions.
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger

from src.core.models import ActionCommand, ActionSource
from src.core.suggestion_support import enrich_suggestion_record, load_suggestion_metadata
from src.web.action_handlers.suggestions import SuggestionBatchApprover
from src.web.dependencies import WebDependencies, verify_auth


class SuggestionsRouter:
    """Class-based router for suggestion management endpoints."""

    def __init__(self, deps: WebDependencies) -> None:
        self._deps = deps
        self._batch_approver = SuggestionBatchApprover(deps.action_gateway)

    async def _execute_action(self, name: str, arguments: dict) -> dict:
        """Execute an action through the gateway and return the data dict.

        Raises HTTPException on failure with an appropriate status code.
        """
        result = await self._deps.action_gateway.execute(ActionCommand(
            name=name,
            arguments=arguments,
            source=ActionSource.UI,
        ))
        if not result.ok:
            code = 404 if 'not found' in (result.error or '').lower() else 400
            raise HTTPException(status_code=code, detail=result.error or 'Action failed')
        return result.data

    def get_router(self) -> APIRouter:
        """Build and return an APIRouter with suggestion management endpoints."""
        router = APIRouter()
        router.add_api_route("/api/suggestions", self._get_suggestions, methods=["GET"])
        router.add_api_route("/api/suggestions/{action_id}/approve", self._approve_suggestion, methods=["POST"])
        router.add_api_route("/api/suggestions/{action_id}/deny", self._deny_suggestion, methods=["POST"])
        router.add_api_route("/api/suggestions/approve-all/{item_id}", self._approve_all_suggestions, methods=["POST"])
        return router

    async def _get_suggestions(self, category_id: str | None = None, item_id: str | None = None):
        deps = self._deps
        suggestions = await deps.db.downloads.get_suggested_actions(category_id=category_id, item_id=item_id, status="pending")
        compiling = False
        needs_refresh = self._needs_suggestion_refresh(suggestions)
        # First visit after startup/setup may arrive before the background job has
        # compiled anything. Also force one migration refresh when old pending
        # suggestions lack explanation metadata; otherwise bad Round-63/67 rows
        # can remain visible until the next lifecycle due date.
        if (not suggestions or needs_refresh) and getattr(deps, "scheduler", None):
            task = getattr(deps, "suggestion_compile_task", None)
            if not task or task.done():
                async def _compile_once() -> None:
                    try:
                        await deps.scheduler.compile_suggestions(force=needs_refresh)
                    except Exception as exc:
                        logger.warning(f"On-demand suggestion compilation failed: {exc}")

                task = asyncio.create_task(_compile_once())
                deps.suggestion_compile_task = task
            compiling = not task.done()
            # Let very fast compilers populate this first response; never wait
            # long enough to freeze the page.
            if compiling:
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=0.75)
                except asyncio.TimeoutError:
                    pass
                except Exception:
                    pass
                suggestions = await deps.db.downloads.get_suggested_actions(category_id=category_id, item_id=item_id, status="pending")
                compiling = not task.done()
        return {
            "suggestions": [enrich_suggestion_record(s) for s in suggestions],
            "summary": await deps.db.downloads.get_suggestion_summary(),
            "compiling": compiling,
        }


    @staticmethod
    def _needs_suggestion_refresh(suggestions) -> bool:
        """Return True when pending rows predate explanation/evidence metadata.

        Explanation/evidence is now a category workflow contract, not a TV-only
        nicety.  Any actionable suggestion without those fields should be
        refreshed when the lifecycle engine permits it.
        """
        for suggestion in suggestions or []:
            metadata = load_suggestion_metadata(getattr(suggestion, "metadata_json", "{}"))
            if not metadata.get("explanation") or not isinstance(metadata.get("evidence"), dict):
                return True
        return False

    async def _approve_suggestion(self, action_id: int, _auth: bool = Depends(verify_auth)):
        data = await self._execute_action('suggestion_approve', {'action_id': action_id})
        if not data.get('found', True):
            raise HTTPException(status_code=404, detail='Suggestion not found')
        return data

    async def _deny_suggestion(self, action_id: int, _auth: bool = Depends(verify_auth)):
        return await self._execute_action('suggestion_deny', {'action_id': action_id})

    async def _approve_all_suggestions(self, item_id: str, _auth: bool = Depends(verify_auth)):
        try:
            return await self._batch_approver.approve_all(item_id)
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e))
