"""
User repository for LJS.
Handles users and sessions.
"""

from typing import Optional
from src.core.repositories.base import BaseRepository


class UserRepository(BaseRepository):
    """Repository for user and session data."""

    async def create_user(self, user_id: str, username: str, password_hash: str) -> None:
        """Create a new user."""
        await self._db.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (?, ?, ?)",
            (user_id, username, password_hash),
        )
        await self._db.commit()

    async def get_user_by_username(self, username: str) -> Optional[dict]:
        """Look up a user by username."""
        cursor = await self._db.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_user_by_id(self, user_id: str) -> Optional[dict]:
        """Look up a user by ID."""
        cursor = await self._db.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def create_session(self, session_id: str, user_id: str,
                             channel: str = "web", channel_user_id: str = "") -> None:
        """Create a new session for a user."""
        await self._db.execute(
            """INSERT OR REPLACE INTO sessions
            (id, user_id, channel, channel_user_id, created_at, last_active_at)
            VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))""",
            (session_id, user_id, channel, channel_user_id),
        )
        await self._db.commit()

    async def get_session(self, session_id: str) -> Optional[dict]:
        """Get a session by ID."""
        cursor = await self._db.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        )
        row = await cursor.fetchone()
        if row:
            await self._db.execute(
                "UPDATE sessions SET last_active_at = datetime('now') WHERE id = ?",
                (session_id,),
            )
            await self._db.commit()
        return dict(row) if row else None

    async def get_user_sessions(self, user_id: str) -> list[dict]:
        """Get all sessions for a user."""
        cursor = await self._db.execute(
            "SELECT * FROM sessions WHERE user_id = ? ORDER BY last_active_at DESC",
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
