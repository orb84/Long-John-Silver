"""Release-watch diagnostics API.

The endpoint is deliberately generic: it exposes the category-provided watch
state/payload/requirements so the UI and local agents can debug scheduling
without teaching the web layer TV episode semantics.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from src.web.dependencies import WebDependencies, verify_auth


class ReleaseWatchesRouter:
    """Expose durable release-watch state for diagnostics and UI surfaces."""

    def __init__(self, deps: WebDependencies):
        self._deps = deps
        self._router = APIRouter()
        self._router.add_api_route("/api/release-watches", self._list_release_watches, methods=["GET"])

    def get_router(self) -> APIRouter:
        return self._router

    async def _list_release_watches(
        self,
        status: str | None = None,
        category_id: str | None = None,
        item_id: str | None = None,
        limit: int = 200,
        _auth: bool = Depends(verify_auth),
    ) -> dict[str, Any]:
        repo = getattr(getattr(self._deps, "db", None), "release_watches", None)
        if repo is None or not hasattr(repo, "list"):
            raise HTTPException(status_code=503, detail="Release-watch store unavailable")
        rows = await repo.list(status=status, category_id=category_id, item_id=item_id, limit=limit)
        counts: dict[str, int] = {}
        for row in rows:
            key = str(row.get("status") or "unknown")
            counts[key] = counts.get(key, 0) + 1
        return {"status": "ok", "count": len(rows), "status_counts": counts, "watches": rows}
