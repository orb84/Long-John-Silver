"""Compatibility wrapper for historical upgrade detection imports.

Quality-upgrade workflows now live in category-owned services and scheduler
collaborators.  This module remains as a small import-safe facade for older
callers and tests that still import ``src.core.upgrade_detector`` during smoke
checks.
"""

from __future__ import annotations

from typing import Any


class UpgradeDetector:
    """No-op compatibility facade for category-owned upgrade detection.

    New upgrade logic should be implemented inside the relevant category or a
    focused scheduler service.  The facade intentionally performs no work; it
    only preserves the old import path while migration continues.
    """

    async def scan(self, *_args: Any, **_kwargs: Any) -> list[Any]:
        """Return an empty result for legacy callers.

        Extension code should depend on category workflows instead of this
        compatibility shim.
        """
        return []
