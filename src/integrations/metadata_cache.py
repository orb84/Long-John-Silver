"""Persistent metadata cache and provider rate-limit helpers.

The cache is deliberately small and provider-neutral: adapters store normalized
provider search results by category/provider/query, not library-side decisions.
This lets the LLM and category workflow reuse fresh metadata while still being
able to re-run disambiguation when the user asks a slightly different question.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from email.utils import parsedate_to_datetime
from typing import Any

from loguru import logger


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str:
    """Serialize a UTC timestamp for SQLite."""
    return (dt or utc_now()).astimezone(timezone.utc).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO timestamp, returning None when missing/invalid."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def stable_cache_key(*parts: Any) -> str:
    """Return a stable hash key for a provider lookup."""
    raw = json.dumps([str(part or "").strip().lower() for part in parts], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class MetadataCacheHit:
    """Provider-cache payload.

    ``stale`` is intentionally explicit: live provider failures may reuse stale
    metadata for LLM disambiguation, but user-visible receipts must be able to
    say that the data was reused rather than freshly fetched.
    """

    payload: dict[str, Any]
    fetched_at: str
    expires_at: str
    status: str = "ok"
    stale: bool = False


class MetadataCacheStore:
    """Async SQLite-backed cache for metadata provider lookups."""

    def __init__(self, db: Any | None) -> None:
        self._db = getattr(db, "_db", None) or db

    @property
    def available(self) -> bool:
        """Return whether a backing SQLite connection is available."""
        return self._db is not None

    async def get(self, *, category_id: str, provider: str, cache_key: str, allow_stale: bool = False) -> MetadataCacheHit | None:
        """Return a cached provider payload.

        By default only fresh rows are returned.  ``allow_stale`` is used after
        live provider failures so LJS can still give the LLM previous evidence
        while clearly marking it as stale.
        """
        if not self.available:
            return None
        try:
            cursor = await self._db.execute(
                """SELECT payload_json, fetched_at, expires_at, status FROM category_metadata_cache
                   WHERE category_id = ? AND provider = ? AND cache_key = ?""",
                (category_id, provider, cache_key),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            expires_at = parse_iso(row["expires_at"] if hasattr(row, "keys") else row[2])
            stale = bool(expires_at and expires_at <= utc_now())
            if stale and not allow_stale:
                return None
            payload_text = row["payload_json"] if hasattr(row, "keys") else row[0]
            payload = json.loads(payload_text or "{}")
            await self._db.execute(
                """UPDATE category_metadata_cache
                   SET last_accessed_at = ?, hit_count = hit_count + 1
                   WHERE category_id = ? AND provider = ? AND cache_key = ?""",
                (iso(utc_now()), category_id, provider, cache_key),
            )
            await self._db.commit()
            return MetadataCacheHit(
                payload=payload,
                fetched_at=row["fetched_at"] if hasattr(row, "keys") else row[1],
                expires_at=row["expires_at"] if hasattr(row, "keys") else row[2],
                status=row["status"] if hasattr(row, "keys") and "status" in row.keys() else "ok",
                stale=stale,
            )
        except Exception as exc:
            logger.debug(f"metadata cache read skipped for {provider}: {exc}")
            return None

    async def get_latest_for_query(self, *, category_id: str, provider: str, query: str, allow_stale: bool = True) -> MetadataCacheHit | None:
        """Return the most recent cache row for a provider/query.

        This intentionally ignores limit/adapter kwargs. It is only used as a
        stale-on-error fallback when the exact cache key missed or expired.
        """
        if not self.available:
            return None
        try:
            cursor = await self._db.execute(
                """SELECT cache_key FROM category_metadata_cache
                   WHERE category_id = ? AND provider = ? AND query = ?
                   ORDER BY fetched_at DESC LIMIT 1""",
                (category_id, provider, query),
            )
            row = await cursor.fetchone()
            cache_key = row["cache_key"] if row and hasattr(row, "keys") else row[0] if row else None
            if not cache_key:
                return None
            return await self.get(category_id=category_id, provider=provider, cache_key=cache_key, allow_stale=allow_stale)
        except Exception as exc:
            logger.debug(f"metadata cache latest-row read skipped for {provider}: {exc}")
            return None

    async def put(
        self,
        *,
        category_id: str,
        provider: str,
        cache_key: str,
        query: str,
        payload: dict[str, Any],
        ttl_seconds: int,
        stable_id: str = "",
        status: str = "ok",
        provider_signature: str = "",
    ) -> None:
        """Persist one provider payload with a TTL."""
        if not self.available:
            return
        now = utc_now()
        expires_at = now + timedelta(seconds=max(60, int(ttl_seconds or 60)))
        try:
            await self._db.execute(
                """INSERT INTO category_metadata_cache
                   (category_id, provider, cache_key, query, stable_id, status, payload_json,
                    provider_signature, fetched_at, expires_at, last_accessed_at, hit_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                   ON CONFLICT(category_id, provider, cache_key) DO UPDATE SET
                        query = excluded.query,
                        stable_id = excluded.stable_id,
                        status = excluded.status,
                        payload_json = excluded.payload_json,
                        provider_signature = excluded.provider_signature,
                        fetched_at = excluded.fetched_at,
                        expires_at = excluded.expires_at""",
                (
                    category_id,
                    provider,
                    cache_key,
                    query,
                    stable_id,
                    status,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    provider_signature,
                    iso(now),
                    iso(expires_at),
                    iso(now),
                ),
            )
            await self._db.commit()
        except Exception as exc:
            logger.debug(f"metadata cache write skipped for {provider}: {exc}")


class ProviderRateLimiter:
    """Persisted provider backoff with a conservative in-process lock."""

    _locks: dict[str, asyncio.Lock] = {}
    _last_call: dict[str, datetime] = {}

    def __init__(self, db: Any | None, provider: str, *, minimum_interval_seconds: float = 1.0) -> None:
        self._db = getattr(db, "_db", None) or db
        self.provider = provider
        self.minimum_interval = max(0.0, float(minimum_interval_seconds or 0.0))
        self._lock = self._locks.setdefault(provider, asyncio.Lock())

    async def __aenter__(self) -> "ProviderRateLimiter":
        await self._lock.acquire()
        await self._sleep_until_allowed()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self._last_call[self.provider] = utc_now()
        self._lock.release()

    async def _sleep_until_allowed(self) -> None:
        now = utc_now()
        next_allowed = await self.get_next_allowed_at()
        last = self._last_call.get(self.provider)
        if last:
            min_next = last + timedelta(seconds=self.minimum_interval)
            if not next_allowed or min_next > next_allowed:
                next_allowed = min_next
        if next_allowed and next_allowed > now:
            await asyncio.sleep(min((next_allowed - now).total_seconds(), 10.0))

    async def get_next_allowed_at(self) -> datetime | None:
        """Return the persisted provider backoff timestamp, if still active."""
        if self._db is None:
            return None
        try:
            cursor = await self._db.execute(
                "SELECT next_allowed_at FROM provider_rate_limits WHERE provider = ?",
                (self.provider,),
            )
            row = await cursor.fetchone()
            value = row["next_allowed_at"] if row and hasattr(row, "keys") else row[0] if row else None
            parsed = parse_iso(value)
            return parsed if parsed and parsed > utc_now() else None
        except Exception:
            return None

    async def record_response(self, *, status_code: int, headers: dict[str, Any] | None = None) -> None:
        """Persist backoff details from response status/headers."""
        headers = headers or {}
        now = utc_now()
        retry_after = _retry_after_seconds(headers)
        remaining = str(headers.get("X-Discogs-Ratelimit-Remaining") or headers.get("X-RateLimit-Remaining") or "")
        reset = str(headers.get("X-Discogs-Ratelimit-Reset") or headers.get("X-RateLimit-Reset") or "")
        next_allowed = now + timedelta(seconds=self.minimum_interval)
        if status_code == 429:
            next_allowed = now + timedelta(seconds=retry_after or max(30.0, self.minimum_interval * 5))
        elif remaining == "0" and reset:
            try:
                next_allowed = datetime.fromtimestamp(float(reset), tz=timezone.utc)
            except ValueError:
                pass
        await self._persist(next_allowed=next_allowed, status_code=status_code, remaining=remaining, reset_at=reset)

    async def _persist(self, *, next_allowed: datetime, status_code: int, remaining: str, reset_at: str) -> None:
        if self._db is None:
            return
        try:
            await self._db.execute(
                """INSERT INTO provider_rate_limits
                   (provider, next_allowed_at, last_status, remaining, reset_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(provider) DO UPDATE SET
                       next_allowed_at = excluded.next_allowed_at,
                       last_status = excluded.last_status,
                       remaining = excluded.remaining,
                       reset_at = excluded.reset_at,
                       updated_at = excluded.updated_at""",
                (self.provider, iso(next_allowed), str(status_code), remaining, reset_at, iso(utc_now())),
            )
            await self._db.commit()
        except Exception as exc:
            logger.debug(f"provider rate-limit persistence skipped for {self.provider}: {exc}")


def _retry_after_seconds(headers: dict[str, Any]) -> float | None:
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        parsed = parse_iso(str(raw))
        if not parsed:
            try:
                parsed = parsedate_to_datetime(str(raw))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                parsed = parsed.astimezone(timezone.utc)
            except (TypeError, ValueError, IndexError, OverflowError):
                parsed = None
        if parsed:
            return max(0.0, (parsed - utc_now()).total_seconds())
    return None
