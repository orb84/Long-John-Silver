"""
Base repository for LJS.
"""

import aiosqlite
from typing import Optional


class BaseRepository:
    """Base class for all repositories, sharing the database connection."""

    def __init__(self, db: aiosqlite.Connection):
        self._db = db

    async def commit(self) -> None:
        """Commit the current transaction."""
        await self._db.commit()

    async def execute(self, sql: str, parameters: tuple = ()) -> aiosqlite.Cursor:
        """Execute a single SQL statement."""
        return await self._db.execute(sql, parameters)
