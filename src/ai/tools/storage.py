"""
Storage-awareness tools for the LJS assistant.

These tools expose read-only disk-space status and preflight capacity
checks. They never mutate filesystem state; they help the LLM avoid
queueing downloads that would fill a category's target disk.
"""

from __future__ import annotations

from typing import Any

from src.ai.tools.base import AgentTool
from src.core.models import Intent, ToolExecutionContext


class GetStorageStatusTool:
    """Return category-aware disk-space status grouped by physical volume."""

    name = "get_storage_status"
    description = (
        "Get category-aware disk-space status grouped by physical/logical disk. "
        "Use this before large downloads, troubleshooting storage warnings, "
        "or explaining which categories share the same disk."
    )
    intents = {Intent.CHAT, Intent.SEARCH, Intent.DOWNLOAD, Intent.CONFIG}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["storage_monitor"]

    def __init__(self, storage_monitor: object | None = None) -> None:
        """Initialize with an optional storage monitor."""
        self._storage_monitor = storage_monitor

    def parameters(self) -> dict:
        """Return JSON schema for this read-only tool."""
        return {
            "type": "object",
            "properties": {
                "category_id": {
                    "type": "string",
                    "description": "Optional category ID to focus the status on, such as tv or movie.",
                }
            },
            "required": [],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Return storage report data and compact summary."""
        if not self._storage_monitor:
            return {"ok": False, "error": "Storage monitor is not configured"}
        report = self._storage_monitor.build_report()
        category_id = arguments.get("category_id")
        data = report.model_dump(mode="json")
        if category_id:
            data["paths"] = [p for p in data.get("paths", []) if p.get("category_id") == category_id]
            data["volumes"] = [
                v for v in data.get("volumes", [])
                if category_id in (v.get("category_ids") or [])
            ]
        return data


class CheckStorageCapacityTool:
    """Check whether a category target has enough room for a planned download."""

    name = "check_storage_capacity"
    description = (
        "Check whether a category's target disk has enough room for an estimated download size. "
        "Use when considering large releases or when storage context shows warning/critical status."
    )
    intents = {Intent.DOWNLOAD, Intent.CONFIG}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["storage_monitor"]

    def __init__(self, storage_monitor: object | None = None) -> None:
        """Initialize with an optional storage monitor."""
        self._storage_monitor = storage_monitor

    def parameters(self) -> dict:
        """Return JSON schema for capacity checks."""
        return {
            "type": "object",
            "properties": {
                "category_id": {
                    "type": "string",
                    "description": "Target category ID, such as tv or movie.",
                },
                "estimated_gb": {
                    "type": "number",
                    "description": "Estimated download size in GiB.",
                },
            },
            "required": [],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Return a preflight storage-capacity decision."""
        if not self._storage_monitor:
            return {"ok": False, "error": "Storage monitor is not configured"}
        estimated_gb = arguments.get("estimated_gb")
        estimated_bytes = None
        if estimated_gb is not None:
            estimated_bytes = int(float(estimated_gb) * 1024 ** 3)
        decision = self._storage_monitor.check_download_capacity(
            category_id=arguments.get("category_id"),
            estimated_bytes=estimated_bytes,
        )
        return decision.model_dump(mode="json")


class StorageToolProvider:
    """Provides read-only storage tools to the assistant."""

    def __init__(self, storage_monitor: object | None = None) -> None:
        """Initialize with the shared storage monitor."""
        self._storage_monitor = storage_monitor

    def get_tools(self) -> list[AgentTool]:
        """Return storage tools."""
        return [
            GetStorageStatusTool(self._storage_monitor),
            CheckStorageCapacityTool(self._storage_monitor),
        ]
