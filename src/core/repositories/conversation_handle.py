"""Persistence for opaque external conversation handles."""

from __future__ import annotations

from datetime import datetime, timezone

from src.core.repositories.base import BaseRepository


class ConversationHandleRepository(BaseRepository):
    """Store and resolve principal-bound external conversation handles."""

    async def create(
        self,
        *,
        handle_id: str,
        internal_session_id: str,
        principal_id: str,
        client_id: str,
        user_id: str | None,
        source: str,
    ) -> dict[str, object]:
        """Persist a newly minted handle and return its canonical row."""
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """INSERT INTO external_conversation_handles
               (handle_id, internal_session_id, principal_id, client_id, user_id,
                source, created_at, last_active_at, revoked_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
            (
                handle_id,
                internal_session_id,
                principal_id,
                client_id,
                user_id,
                source,
                now,
                now,
            ),
        )
        await self._db.commit()
        row = await self.get(handle_id)
        if row is None:
            raise RuntimeError("Conversation handle was not persisted")
        return row

    async def get(self, handle_id: str) -> dict[str, object] | None:
        """Return a non-revoked handle without changing ownership semantics."""
        cursor = await self._db.execute(
            """SELECT * FROM external_conversation_handles
               WHERE handle_id = ? AND revoked_at IS NULL""",
            (str(handle_id or ""),),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def touch(self, handle_id: str) -> None:
        """Record successful use of a handle."""
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """UPDATE external_conversation_handles
               SET last_active_at = ?
               WHERE handle_id = ? AND revoked_at IS NULL""",
            (now, handle_id),
        )
        await self._db.commit()


    async def count_active_for_principal(self, principal_id: str, client_id: str) -> int:
        """Return active handle count for one credential/client binding."""
        cursor = await self._db.execute(
            """SELECT COUNT(*) FROM external_conversation_handles
               WHERE principal_id = ? AND client_id = ? AND revoked_at IS NULL""",
            (principal_id, client_id),
        )
        row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def list_expired(self, cutoff_iso: str) -> list[dict[str, object]]:
        """Return active handle rows whose inactivity timestamp is before cutoff."""
        cursor = await self._db.execute(
            """SELECT * FROM external_conversation_handles
               WHERE revoked_at IS NULL AND last_active_at < ?""",
            (cutoff_iso,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def revoke_expired(self, cutoff_iso: str) -> int:
        """Revoke handles whose last activity predates the supplied UTC cutoff."""
        now = datetime.now(timezone.utc).isoformat()
        cursor = await self._db.execute(
            """UPDATE external_conversation_handles
               SET revoked_at = ?
               WHERE revoked_at IS NULL AND last_active_at < ?""",
            (now, cutoff_iso),
        )
        await self._db.commit()
        return int(cursor.rowcount or 0)

    async def revoke(self, handle_id: str) -> bool:
        """Revoke a handle; returns whether an active row was changed."""
        now = datetime.now(timezone.utc).isoformat()
        cursor = await self._db.execute(
            """UPDATE external_conversation_handles
               SET revoked_at = ?
               WHERE handle_id = ? AND revoked_at IS NULL""",
            (now, handle_id),
        )
        await self._db.commit()
        return bool(cursor.rowcount)
