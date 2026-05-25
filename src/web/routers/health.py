"""
Health check router for LJS.

Provides a lightweight health endpoint used by reverse proxies,
monitoring tools, and the frontend to verify the server is alive.
"""

from fastapi import APIRouter

from src.web.dependencies import WebDependencies


class HealthRouter:
    """Class-based router for health-check endpoints."""

    def __init__(self, deps: WebDependencies) -> None:
        self._deps = deps

    def get_router(self) -> APIRouter:
        """Build and return an APIRouter with health-check endpoints."""
        router = APIRouter()
        router.add_api_route("/api/health", self._health_check, methods=["GET"])
        return router

    async def _health_check(self):
        deps = self._deps
        browser_health = {
            "package_installed": False,
            "browser_installed": False,
            "launch_ok": False,
            "navigation_ok": False,
            "last_error": None,
        }
        if deps.browser_runtime:
            try:
                health = await deps.browser_runtime.health_check()
                browser_health = {
                    "package_installed": health.package_installed,
                    "browser_installed": health.browser_installed,
                    "launch_ok": health.launch_ok,
                    "navigation_ok": health.navigation_ok,
                    "last_error": health.last_error,
                }
            except Exception:
                pass
        storage = {"ok": True, "warnings": [], "critical": []}
        if deps.storage_monitor:
            try:
                report = deps.storage_monitor.build_report()
                storage = {
                    "ok": report.ok,
                    "warnings": report.warnings,
                    "critical": report.critical,
                }
            except Exception:
                storage = {"ok": False, "warnings": [], "critical": ["Storage monitor failed"]}
        return {
            "status": "ok" if storage.get("ok", True) else "degraded",
            "playwright": browser_health,
            "storage": storage,
        }
