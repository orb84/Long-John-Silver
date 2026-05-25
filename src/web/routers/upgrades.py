"""
Upgrades router for LJS.

Handles quality upgrade candidate listing, approval, and denial.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from src.core.models import ActionCommand, ActionSource
from src.web.dependencies import WebDependencies, verify_auth


class ApproveUpgradeBody(BaseModel):
    """Request body for upgrade approval."""
    confirmed: bool = False


class UpgradesRouter:
    """Class-based router for upgrade management endpoints."""

    def __init__(self, deps: WebDependencies) -> None:
        self._deps = deps

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
        """Build and return an APIRouter with upgrade management endpoints."""
        router = APIRouter()
        router.add_api_route("/api/upgrades", self._get_upgrades, methods=["GET"])
        router.add_api_route("/api/upgrades/{upgrade_id}/approve", self._approve_upgrade, methods=["POST"])
        router.add_api_route("/api/upgrades/{upgrade_id}/deny", self._deny_upgrade, methods=["POST"])
        return router

    async def _get_upgrades(self, item_id: str | None = None):
        deps = self._deps
        candidates = await deps.db.downloads.get_upgrade_candidates(item_id=item_id, status="pending")
        return {"upgrades": [c.model_dump() for c in candidates]}

    async def _approve_upgrade(self, upgrade_id: int, body: ApproveUpgradeBody = None, _auth: bool = Depends(verify_auth)):
        confirmed = (body or ApproveUpgradeBody()).confirmed
        data = await self._execute_action('upgrade_approve', {'upgrade_id': upgrade_id, 'confirmed': confirmed})
        if data.get('status') == 'confirmation_required':
            raise HTTPException(status_code=400, detail='User confirmation required')
        if not data.get('found', True):
            raise HTTPException(status_code=404, detail='Upgrade not found')
        return data

    async def _deny_upgrade(self, upgrade_id: int, _auth: bool = Depends(verify_auth)):
        return await self._execute_action('upgrade_deny', {'upgrade_id': upgrade_id})
