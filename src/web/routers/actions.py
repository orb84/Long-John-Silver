"""
Actions router for LJS.

Provides the POST /api/actions endpoint that accepts ActionCommand
objects and routes them through the ActionGateway. This is the single
entry point for all deterministic mutations from UI, chat, scheduler,
and automation.
"""

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import JSONResponse

from fastapi.encoders import jsonable_encoder

from src.core.models import ActionCommand, ActionSource, ActionResult
from src.web.dependencies import WebDependencies, verify_auth


class ActionsRouter:
    """Class-based router for the unified action endpoint."""

    def __init__(self, deps: WebDependencies) -> None:
        self._deps = deps

    def get_router(self) -> APIRouter:
        """Build and return an APIRouter with the unified action endpoint."""
        router = APIRouter()
        router.add_api_route("/api/actions", self._execute_action, methods=["POST"])
        router.add_api_route("/api/actions", self._list_actions, methods=["GET"])
        return router

    async def _execute_action(self, request: Request, _auth: bool = Depends(verify_auth)):
        """Execute a single action through the ActionGateway."""
        deps = self._deps
        gateway = deps.action_gateway
        if not gateway:
            raise HTTPException(status_code=500, detail='ActionGateway not configured')
        body = await request.json()
        try:
            if not isinstance(body, dict):
                raise ValueError("Request body must be a JSON object")
            name = str(body.get("name") or "").strip()
            arguments = body.get("arguments") or {}
            if not name:
                raise ValueError("Action name is required")
            if not isinstance(arguments, dict):
                raise ValueError("Action arguments must be an object")
            # Provenance and durable identities are server-owned. A browser may
            # choose the action and its arguments, but it cannot impersonate the
            # scheduler/system or overwrite command/correlation identifiers.
            command = ActionCommand(
                name=name,
                arguments=arguments,
                source=ActionSource.UI,
                user_id="web",
                session_id="web_actions",
                actor="authenticated_web_user",
            )
        except Exception as exc:
            return JSONResponse(
                status_code=400,
                content=jsonable_encoder(ActionResult(
                    ok=False, error=f'Invalid action request: {exc}',
                )),
            )
        result = await gateway.execute(command)
        status_code = 200 if result.ok else (404 if 'not found' in (result.error or '').lower() else 400)
        return JSONResponse(status_code=status_code, content=jsonable_encoder(result))

    async def _list_actions(self, _auth: bool = Depends(verify_auth)):
        """Return the list of registered action names."""
        deps = self._deps
        gateway = deps.action_gateway
        if not gateway:
            raise HTTPException(status_code=500, detail='ActionGateway not configured')
        return {'actions': gateway.registered_actions}
