"""Principal-level admission control for delegated LJS agent turns."""

from __future__ import annotations

import asyncio

from src.core.models import InvocationPrincipal


class AgentDelegationAdmissionGate:
    """Bound concurrent delegated turns per external principal/client identity."""

    DEFAULT_MAX_ACTIVE_PER_PRINCIPAL = 4

    def __init__(self, *, max_active_per_principal: int = DEFAULT_MAX_ACTIVE_PER_PRINCIPAL) -> None:
        self._limit = max(1, int(max_active_per_principal))
        self._active: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def try_acquire(self, principal: InvocationPrincipal) -> bool:
        """Acquire one turn slot without queueing unbounded external work."""
        key = self._key(principal)
        async with self._lock:
            current = self._active.get(key, 0)
            if current >= self._limit:
                return False
            self._active[key] = current + 1
            return True

    async def release(self, principal: InvocationPrincipal) -> None:
        """Release one acquired turn slot and discard idle counters."""
        key = self._key(principal)
        async with self._lock:
            current = self._active.get(key, 0)
            if current <= 1:
                self._active.pop(key, None)
            else:
                self._active[key] = current - 1

    @staticmethod
    def _key(principal: InvocationPrincipal) -> str:
        return f"{principal.principal_id}\x1f{principal.client_id or ''}"
