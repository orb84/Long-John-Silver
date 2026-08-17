"""Opaque principal-bound conversation handles for external control surfaces."""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.core.models import InvocationPrincipal


class ConversationHandleAccessError(PermissionError):
    """Raised when an external handle does not belong to the caller."""


class ConversationHandleLimitError(RuntimeError):
    """Raised when one external principal exceeds its durable handle quota."""


@dataclass(frozen=True, slots=True)
class ResolvedConversationHandle:
    """Resolved public handle and its private canonical session."""

    handle_id: str
    internal_session_id: str
    user_id: str | None


class ConversationHandleService:
    """Mint and resolve opaque handles without exposing internal session IDs."""

    DEFAULT_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
    DEFAULT_MAX_ACTIVE_PER_PRINCIPAL = 100
    DEFAULT_TOUCH_INTERVAL_SECONDS = 60

    def __init__(
        self,
        database: object,
        *,
        max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
        max_active_per_principal: int = DEFAULT_MAX_ACTIVE_PER_PRINCIPAL,
        touch_interval_seconds: int = DEFAULT_TOUCH_INTERVAL_SECONDS,
    ) -> None:
        self._database = database
        self._max_age_seconds = max(60, int(max_age_seconds))
        self._max_active_per_principal = max(1, int(max_active_per_principal))
        self._touch_interval_seconds = max(1, int(touch_interval_seconds))

    async def mint(self, principal: InvocationPrincipal) -> ResolvedConversationHandle:
        """Create a durable handle bound to one principal/client and canonical user."""
        repository = self._repository()
        await self._expire_old_handles(repository)
        count_active = getattr(repository, "count_active_for_principal", None)
        if callable(count_active):
            count = await count_active(principal.principal_id, str(principal.client_id or ""))
            if int(count) >= self._max_active_per_principal:
                raise ConversationHandleLimitError(
                    f"Principal '{principal.principal_id}' has reached the active conversation-handle limit"
                )

        users = getattr(self._database, "users", None)
        if users is None:
            raise RuntimeError("User repository is unavailable")
        requested_user_id = str(principal.user_id or "local").strip() or "local"
        if requested_user_id != "local":
            get_user = getattr(users, "get_user_by_id", None)
            existing_user = await get_user(requested_user_id) if callable(get_user) else None
            if existing_user is None:
                raise ConversationHandleAccessError(
                    f"Configured external user '{requested_user_id}' does not exist"
                )
        internal_session_id = f"external_{uuid.uuid4().hex}"
        session = await users.ensure_session(
            internal_session_id,
            user_id=requested_user_id,
            channel=principal.source,
            channel_user_id=principal.principal_id,
        )
        resolved_user_id = str(session.get("user_id") or principal.user_id or "local")
        handle_id = secrets.token_urlsafe(32)
        try:
            await repository.create(
                handle_id=handle_id,
                internal_session_id=internal_session_id,
                principal_id=principal.principal_id,
                client_id=str(principal.client_id or ""),
                user_id=resolved_user_id,
                source=principal.source,
            )
        except Exception:
            revoke_handle = getattr(repository, "revoke", None)
            if callable(revoke_handle):
                try:
                    await revoke_handle(handle_id)
                except Exception:
                    pass
            delete_session = getattr(users, "delete_session", None)
            if callable(delete_session):
                try:
                    await delete_session(internal_session_id)
                except Exception:
                    pass
            raise
        return ResolvedConversationHandle(
            handle_id=handle_id,
            internal_session_id=internal_session_id,
            user_id=resolved_user_id,
        )

    async def resolve(
        self,
        handle_id: str,
        principal: InvocationPrincipal,
    ) -> ResolvedConversationHandle:
        """Resolve only an unexpired handle owned by the same principal/client."""
        repository = self._repository()
        row = await repository.get(str(handle_id or ""))
        if row is None:
            raise ConversationHandleAccessError("Conversation handle is invalid or revoked")
        if self._is_expired(row):
            await self._revoke_row(str(handle_id), row)
            raise ConversationHandleAccessError("Conversation handle has expired")
        if str(row.get("principal_id") or "") != principal.principal_id:
            raise ConversationHandleAccessError("Conversation handle does not belong to this principal")
        if str(row.get("client_id") or "") != str(principal.client_id or ""):
            raise ConversationHandleAccessError("Conversation handle does not belong to this client")
        expected_user_id = str(principal.user_id or "local").strip() or "local"
        if str(row.get("user_id") or "local") != expected_user_id:
            raise ConversationHandleAccessError("Conversation handle does not belong to this user binding")
        if self._should_touch(row):
            await repository.touch(str(handle_id))
        return ResolvedConversationHandle(
            handle_id=str(handle_id),
            internal_session_id=str(row["internal_session_id"]),
            user_id=str(row.get("user_id")) if row.get("user_id") is not None else principal.user_id,
        )

    async def revoke(self, handle_id: str, principal: InvocationPrincipal) -> bool:
        """Revoke one owned handle and clean its private external session/history."""
        resolved = await self.resolve(handle_id, principal)
        row = {"internal_session_id": resolved.internal_session_id}
        return await self._revoke_row(handle_id, row)

    async def resolve_or_mint(
        self,
        handle_id: str | None,
        principal: InvocationPrincipal,
    ) -> ResolvedConversationHandle:
        """Resolve a supplied public handle or mint a fresh one."""
        if handle_id:
            return await self.resolve(handle_id, principal)
        return await self.mint(principal)

    async def _expire_old_handles(self, repository: object) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=self._max_age_seconds)
        cutoff_iso = cutoff.isoformat()
        list_expired = getattr(repository, "list_expired", None)
        if callable(list_expired):
            for row in await list_expired(cutoff_iso):
                handle_id = str(row.get("handle_id") or "")
                if handle_id:
                    await self._revoke_row(handle_id, row)
            return
        expire = getattr(repository, "revoke_expired", None)
        if callable(expire):
            await expire(cutoff_iso)

    async def _revoke_row(self, handle_id: str, row: dict[str, object]) -> bool:
        """Revoke one handle and best-effort remove its external-only session state."""
        repository = self._repository()
        revoke = getattr(repository, "revoke", None)
        if not callable(revoke):
            raise RuntimeError("Conversation handle repository cannot revoke handles")
        changed = bool(await revoke(handle_id))
        internal_session_id = str(row.get("internal_session_id") or "")
        users = getattr(self._database, "users", None)
        delete_session = getattr(users, "delete_session", None) if users is not None else None
        if internal_session_id and callable(delete_session):
            await delete_session(internal_session_id)
        return changed

    def _is_expired(self, row: dict[str, object]) -> bool:
        raw = str(row.get("last_active_at") or row.get("created_at") or "").strip()
        if not raw:
            return True
        try:
            timestamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
        except ValueError:
            return True
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=self._max_age_seconds)
        return timestamp < cutoff

    def _should_touch(self, row: dict[str, object]) -> bool:
        raw = str(row.get("last_active_at") or "").strip()
        if not raw:
            return True
        try:
            timestamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
        except ValueError:
            return True
        return datetime.now(timezone.utc) - timestamp >= timedelta(seconds=self._touch_interval_seconds)

    def _repository(self) -> object:
        repository = getattr(self._database, "conversation_handles", None)
        if repository is None:
            raise RuntimeError("Conversation handle repository is unavailable")
        return repository
