"""Regression tests for synchronous callers receiving unexpected awaitables."""

from __future__ import annotations

from unittest.mock import AsyncMock

from src.utils.async_boundary import AsyncBoundary


class TestAsyncBoundary:
    """Verify unexpected awaitables are closed instead of leaked or trusted."""

    def test_plain_value_is_not_handled(self) -> None:
        """Ordinary synchronous callback results remain untouched."""
        assert AsyncBoundary.close_if_awaitable(True) is False

    def test_async_mock_result_is_closed(self) -> None:
        """An AsyncMock coroutine is closed when a sync boundary receives it."""
        result = AsyncMock()()
        assert AsyncBoundary.close_if_awaitable(result) is True
