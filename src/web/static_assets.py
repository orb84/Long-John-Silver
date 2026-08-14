"""Versioned static-asset URLs for browser cache coherence."""

from __future__ import annotations

import hashlib
from pathlib import Path
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class StaticAssetVersionResolver:
    """Build one content-derived version for the shipped browser bundle.

    Backend and frontend behavior are released together. Query-versioning every
    local CSS/JS URL prevents a newly deployed Python backend from silently
    running against an older cached chat or diagnostics controller.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._version = self._build_version()

    @property
    def version(self) -> str:
        """Return the compact bundle fingerprint."""
        return self._version

    def url(self, path: str) -> str:
        """Append the bundle fingerprint to one local static URL."""
        value = str(path or "")
        if not value.startswith("/static/"):
            return value
        separator = "&" if "?" in value else "?"
        return f"{value}{separator}v={self._version}"

    def matches(self, candidate: str | None) -> bool:
        """Return whether a browser reports the currently shipped bundle."""
        return bool(candidate) and str(candidate).strip() == self._version

    def _build_version(self) -> str:
        digest = hashlib.sha256()
        if not self._root.exists():
            return "missing"
        for path in sorted(item for item in self._root.rglob("*") if item.is_file()):
            digest.update(path.relative_to(self._root).as_posix().encode("utf-8"))
            digest.update(path.read_bytes())
        return digest.hexdigest()[:16]


class BrowserBundleCoherenceMiddleware(BaseHTTPMiddleware):
    """Prevent cached HTML from pinning an obsolete browser bundle.

    Static resources are content-versioned and may be cached freely. HTML must
    be revalidated on every navigation so a restarted backend cannot serve new
    APIs while the browser keeps an old chat/settings controller indefinitely.
    """

    def __init__(self, app, *, asset_version: str) -> None:
        super().__init__(app)
        self._asset_version = str(asset_version or "unknown")

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Attach bundle identity and no-store semantics to HTML responses."""
        response = await call_next(request)
        response.headers["X-LJS-Asset-Version"] = self._asset_version
        content_type = str(response.headers.get("content-type") or "").casefold()
        if "text/html" in content_type:
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
        return response
