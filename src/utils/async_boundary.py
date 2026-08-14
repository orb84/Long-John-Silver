"""Utilities for safely crossing synchronous and asynchronous callback boundaries."""

from __future__ import annotations

import inspect
from typing import Any


class AsyncBoundary:
    """Dispose of awaitables accidentally returned to synchronous call sites."""

    @staticmethod
    def close_if_awaitable(value: Any) -> bool:
        """Close an unexpected awaitable and report whether one was handled.

        Synchronous hooks are occasionally represented by ``AsyncMock`` in
        tests or third-party adapters.  A sync caller cannot await that value,
        but it must still close it so the process does not leak a coroutine or
        mistake the coroutine object's truthiness for a successful verdict.
        """
        if not inspect.isawaitable(value):
            return False
        close = getattr(value, "close", None)
        if callable(close):
            close()
        return True
