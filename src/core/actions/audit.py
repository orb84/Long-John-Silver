"""
Action audit store for LJS.

ActionEventStore persists action execution events to the database for
auditing, behavior tracking, and the assistant's memory composer.
"""

import json
from datetime import datetime, timezone
from typing import Any

import aiosqlite
from loguru import logger

from src.core.models import ActionSource


class ActionEventStore:
    """Persistent store for action audit events.

    Records every action executed through the gateway with its source,
    arguments, result, and timestamps. Supports querying recent actions
    by source and by action name for the assistant's memory composer.
    """

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    async def record(self, action_name: str, source: ActionSource,
                     user_id: str | None = None,
                     session_id: str | None = None,
                     arguments: dict[str, Any] | None = None,
                     result: dict[str, Any] | None = None) -> None:
        """Record an action execution event.

        Args:
            action_name: Name of the executed action.
            source: Where the action originated.
            user_id: Optional user identifier.
            session_id: Optional session identifier.
            arguments: The arguments passed to the action handler.
            result: The result dict (ok, error, data) from the handler.
        """
        try:
            await self._db.execute(
                """INSERT INTO action_events
                   (action_name, source, user_id, session_id,
                    arguments_json, result_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    action_name,
                    source.value,
                    user_id,
                    session_id,
                    json.dumps(arguments or {}, default=str),
                    json.dumps(result or {}, default=str),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            await self._db.commit()
        except Exception as exc:
            logger.warning(f'Failed to persist action event: {exc}')

    async def get_recent(self, limit: int = 50,
                         source: ActionSource | None = None,
                         action_name: str | None = None) -> list[dict]:
        """Return recent action events, optionally filtered.

        Args:
            limit: Maximum number of events to return.
            source: Optional filter by action source.
            action_name: Optional filter by action name.

        Returns:
            List of action event dicts ordered by created_at descending.
        """
        query = 'SELECT * FROM action_events'
        conditions: list[str] = []
        params: list[Any] = []

        if source is not None:
            conditions.append('source = ?')
            params.append(source.value)
        if action_name is not None:
            conditions.append('action_name = ?')
            params.append(action_name)

        if conditions:
            query += ' WHERE ' + ' AND '.join(conditions)
        query += ' ORDER BY created_at DESC LIMIT ?'
        params.append(limit)

        cursor = await self._db.execute(query, params)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_recent_by_user(self, user_id: str,
                                  limit: int = 20) -> list[dict]:
        """Return recent action events for a specific user."""
        cursor = await self._db.execute(
            """SELECT * FROM action_events
               WHERE user_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (user_id, limit),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def count(self) -> int:
        """Return the total number of recorded action events."""
        cursor = await self._db.execute('SELECT COUNT(*) FROM action_events')
        row = await cursor.fetchone()
        return row[0] if row else 0
