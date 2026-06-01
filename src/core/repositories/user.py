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


    async def ensure_session(self, session_id: str, user_id: str | None = None,
                             channel: str = "web", channel_user_id: str = "") -> dict:
        """Return an existing session or create a safe local one.

        Chat transports can reconnect with browser-generated session ids before
        the login/register path has inserted a row into ``sessions``.  The
        database enforces conversation_history -> sessions in upgraded installs,
        so the repository must own this invariant instead of every caller trying
        to remember it.

        ``user_id`` is optional because local/self-hosted LJS can run before a
        real account is created.  In that case we create a reserved local user
        that keeps foreign keys valid without weakening the schema.
        """
        session_id = str(session_id or "").strip()
        if not session_id:
            session_id = "web_local"
        channel = str(channel or "web").strip() or "web"
        channel_user_id = str(channel_user_id or "")

        row = await self.get_session(session_id)
        if row:
            return row

        resolved_user_id = str(user_id or "").strip() or "local"
        if not await self.get_user_by_id(resolved_user_id):
            username = "local" if resolved_user_id == "local" else f"local_{resolved_user_id}"
            await self.create_user(
                user_id=resolved_user_id,
                username=username,
                password_hash="local-session-placeholder",
            )
        await self.create_session(
            session_id=session_id,
            user_id=resolved_user_id,
            channel=channel,
            channel_user_id=channel_user_id,
        )
        row = await self.get_session(session_id)
        return row or {
            "id": session_id,
            "user_id": resolved_user_id,
            "channel": channel,
            "channel_user_id": channel_user_id,
        }

    async def get_user_sessions(self, user_id: str) -> list[dict]:
        """Get all sessions for a user."""
        cursor = await self._db.execute(
            "SELECT * FROM sessions WHERE user_id = ? ORDER BY last_active_at DESC",
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
