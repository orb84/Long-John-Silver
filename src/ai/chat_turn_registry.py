"""Session-scoped ownership for assistant chat turns.

WebSocket reconnects, REST fallbacks, messaging bridges, and external adapters
can all target the same conversation while an earlier turn is still running.
Per-transport ``active_turn`` variables cannot protect the conversation from
those races. This registry is the process-local authority for one active
assistant task per chat session, independent of transport connection.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(slots=True)
class ActiveChatTurn:
    """One active session turn and its cancellable task."""

    session_id: str
    turn_id: str
    task: asyncio.Task[Any]


class ChatTurnRegistry:
    """Own at most one live assistant task for each chat session."""

    def __init__(self) -> None:
        self._turns: dict[str, ActiveChatTurn] = {}
        self._lock = asyncio.Lock()

    async def start(
        self,
        session_id: str,
        turn_id: str,
        run: Callable[[], Awaitable[Any]],
        *,
        task_name: str | None = None,
    ) -> tuple[bool, ActiveChatTurn]:
        """Atomically start ``run`` unless this session already has a live turn."""
        sid = str(session_id or "default")
        tid = str(turn_id or "")
        async with self._lock:
            current = self._turns.get(sid)
            if current is not None and not current.task.done():
                return False, current
            task = asyncio.create_task(run(), name=task_name or f"chat-turn-{tid}")
            active = ActiveChatTurn(session_id=sid, turn_id=tid, task=task)
            self._turns[sid] = active
            return True, active

    async def active(self, session_id: str) -> ActiveChatTurn | None:
        """Return the current live turn for ``session_id`` if one exists."""
        sid = str(session_id or "default")
        async with self._lock:
            current = self._turns.get(sid)
            if current is None:
                return None
            if current.task.done():
                self._turns.pop(sid, None)
                return None
            return current

    async def cancel(self, session_id: str, turn_id: str | None = None) -> ActiveChatTurn | None:
        """Cancel the matching live turn and return it, or return ``None``."""
        sid = str(session_id or "default")
        requested = str(turn_id or "")
        async with self._lock:
            current = self._turns.get(sid)
            if current is None or current.task.done():
                self._turns.pop(sid, None)
                return None
            if requested and requested != current.turn_id:
                return None
            current.task.cancel()
            return current

    async def cancel_and_wait(
        self,
        session_id: str,
        turn_id: str | None = None,
        *,
        timeout_seconds: float = 5.0,
    ) -> tuple[ActiveChatTurn | None, bool]:
        """Cancel a matching turn and wait briefly for cancellation to settle.

        Returning from a browser Stop action while the assistant task is still
        alive makes later provider/search work look like mysterious background
        activity.  The transport therefore gets both the matched turn and a
        settlement flag.  A false flag means cancellation was signalled but a
        child/provider boundary did not unwind within the bounded wait; the UI
        should remain in ``stopping`` rather than pretending the turn is idle.
        """
        active = await self.cancel(session_id, turn_id)
        if active is None:
            return None, True
        try:
            await asyncio.wait_for(
                asyncio.shield(active.task),
                timeout=max(0.05, float(timeout_seconds)),
            )
        except asyncio.CancelledError:
            return active, True
        except asyncio.TimeoutError:
            return active, False
        except Exception:
            # The task did terminate; its ordinary turn wrapper owns reporting
            # the actual failure to the user.  For cancellation ownership, this
            # is settled rather than background work.
            return active, True
        return active, True

    async def release(self, session_id: str, turn_id: str) -> None:
        """Release ownership only when the same turn still owns the session."""
        sid = str(session_id or "default")
        tid = str(turn_id or "")
        async with self._lock:
            current = self._turns.get(sid)
            if current is not None and current.turn_id == tid:
                self._turns.pop(sid, None)
