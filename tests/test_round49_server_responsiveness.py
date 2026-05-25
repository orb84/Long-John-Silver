"""Round 49 regression tests for non-blocking library scans."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.web.action_handlers.library import LibraryActionHandler


class _FakeScheduler:
    def __init__(self) -> None:
        self.scan_library = AsyncMock()
        self._running = False

    def request_library_scan(self, force: bool = True, *, refresh_metadata: bool = True, reason: str = "manual") -> dict:
        return {
            "status": "queued",
            "scan_in_progress": True,
            "message": "Library scan started in the background.",
            "force": force,
            "refresh_metadata": refresh_metadata,
            "reason": reason,
        }


@pytest.mark.asyncio
async def test_library_scan_action_queues_background_work_without_awaiting_scan():
    scheduler = _FakeScheduler()
    handler = LibraryActionHandler(scheduler, settings_manager=None, librarian=None)  # type: ignore[arg-type]

    result = await handler.scan()

    assert result["status"] == "queued"
    assert result["scan_in_progress"] is True
    assert result["force"] is True
    assert result["refresh_metadata"] is True
    assert result["reason"] == "manual"
    scheduler.scan_library.assert_not_awaited()
