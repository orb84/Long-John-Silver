"""Persistent notification inbox API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from src.core.models import ActionCommand, ActionSource
from src.web.dependencies import WebDependencies, verify_auth


class NotificationsRouter:
    """Expose durable web notifications and notification actions."""

    def __init__(self, deps: WebDependencies) -> None:
        self._deps = deps

    def get_router(self) -> APIRouter:
        """Build the notification inbox router."""
        router = APIRouter()
        router.add_api_route("/api/notifications", self._list_notifications, methods=["GET"])
        router.add_api_route("/api/notifications/{notification_id}/read", self._mark_read, methods=["POST"])
        router.add_api_route("/api/notifications/read-all", self._mark_all_read, methods=["POST"])
        router.add_api_route("/api/notifications/{notification_id}/actions/{action_key}", self._run_action, methods=["POST"])
        return router

    async def _list_notifications(self, status: str | None = None, limit: int = 50, _auth: bool = Depends(verify_auth)) -> dict[str, Any]:
        repo = self._repo()
        notifications = await repo.list(status=status, limit=limit)
        return {"notifications": notifications, "unread": await repo.unread_count()}

    async def _mark_read(self, notification_id: int, _auth: bool = Depends(verify_auth)) -> dict[str, Any]:
        repo = self._repo()
        ok = await repo.mark_read(notification_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Notification not found")
        self._emit_update()
        return {"status": "read", "id": notification_id, "unread": await repo.unread_count()}

    async def _mark_all_read(self, _auth: bool = Depends(verify_auth)) -> dict[str, Any]:
        repo = self._repo()
        count = await repo.mark_all_read()
        self._emit_update()
        return {"status": "read", "count": count, "unread": await repo.unread_count()}

    async def _run_action(self, notification_id: int, action_key: str, _auth: bool = Depends(verify_auth)) -> dict[str, Any]:
        repo = self._repo()
        row = await repo.get(notification_id)
        if not row:
            raise HTTPException(status_code=404, detail="Notification not found")
        action = next((a for a in row.get("actions") or [] if str(a.get("key") or a.get("id") or "") == action_key), None)
        if not action:
            raise HTTPException(status_code=404, detail="Notification action not found")
        result_payload: dict[str, Any]
        endpoint = str(action.get("endpoint") or "")
        category_workflow = action.get("category_workflow") if isinstance(action.get("category_workflow"), dict) else None
        if category_workflow:
            category_id = str(category_workflow.get("category_id") or row.get("category_id") or "")
            workflow_name = str(category_workflow.get("workflow") or "")
            arguments = category_workflow.get("arguments") if isinstance(category_workflow.get("arguments"), dict) else {}
            result = await self._deps.scheduler.execute_category_workflow(category_id, workflow_name, arguments)
            result_payload = result.model_dump() if hasattr(result, "model_dump") else {"result": str(result)}
        elif endpoint.startswith("/api/categories/"):
            # Keep endpoint parsing generic for notification actions created by category workflows.
            parts = endpoint.strip("/").split("/")
            try:
                category_id = parts[2]
                item_id = parts[4]
                workflow_name = parts[6]
            except Exception as exc:
                raise HTTPException(status_code=400, detail="Malformed notification action endpoint") from exc
            arguments = action.get("body") if isinstance(action.get("body"), dict) else {}
            arguments.setdefault("item_id", item_id)
            result = await self._deps.scheduler.execute_category_workflow(category_id, workflow_name, arguments)
            result_payload = result.model_dump() if hasattr(result, "model_dump") else {"result": str(result)}
        else:
            raise HTTPException(status_code=400, detail="Unsupported notification action")
        await repo.mark_read(notification_id)
        self._emit_update()
        return {"status": "executed", "id": notification_id, "action": action_key, "receipt": result_payload, "unread": await repo.unread_count()}

    def _repo(self):
        repo = getattr(getattr(self._deps, "db", None), "notifications", None)
        if not repo:
            raise HTTPException(status_code=503, detail="Notification store unavailable")
        return repo

    def _emit_update(self) -> None:
        try:
            if self._deps.event_bus:
                self._deps.event_bus.emit_system("notifications_updated", {})
        except Exception:
            pass
