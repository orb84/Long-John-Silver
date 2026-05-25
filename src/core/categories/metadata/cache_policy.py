"""Provider metadata snapshot refresh policy helpers for category workflows.

Category metadata rows are persistent provider snapshots attached to a stable
item identity.  They are not throwaway cache entries and should not be refreshed
by title search on every boot.  These helpers decide when an existing snapshot is
fresh enough to reuse, when a missing provider identity deserves a backoff, and
when artwork-only gaps can be handled without a full provider lookup.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# Default refresh windows.  Callers may still pass max_age_seconds explicitly for
# old workflows, but new code should usually let ``_snapshot_refresh_age_seconds``
# choose based on item lifecycle and provider identity completeness.
ONGOING_SNAPSHOT_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
ENDED_SNAPSHOT_MAX_AGE_SECONDS = 180 * 24 * 60 * 60
UNKNOWN_LIFECYCLE_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
MISSING_PROVIDER_BACKOFF_SECONDS = 24 * 60 * 60
ARTWORK_ONLY_REFRESH_SECONDS = 24 * 60 * 60

ENDED_STATUSES = {
    "ended",
    "canceled",
    "cancelled",
    "released",
    "returning ended",
    "production complete",
}
ONGOING_STATUSES = {
    "returning series",
    "continuing",
    "in production",
    "planned",
    "pilot",
    "post production",
}


def _parse_dt(value: object) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _metadata_from_row(row: dict[str, Any] | None) -> dict[str, Any]:
    metadata = (row or {}).get("metadata") or {}
    return metadata if isinstance(metadata, dict) else {}


def _provider_identity_present(metadata: dict[str, Any] | None, row: dict[str, Any] | None = None) -> bool:
    """Return whether a provider row can be refreshed by stable ID, not title."""
    metadata = metadata or {}
    row = row or {}
    return any(
        metadata.get(key) or row.get(key)
        for key in ("provider_id", "external_id", "tmdb_id", "tvdb_id", "tvmaze_id", "imdb_id", "id")
    )


def _artwork_present(metadata: dict[str, Any] | None) -> bool:
    """Return whether a snapshot already has usable local or provider artwork."""
    metadata = metadata or {}
    return bool(
        metadata.get("local_poster_url")
        or metadata.get("poster_url")
        or metadata.get("poster_path")
        or metadata.get("artwork_path")
    )


def _snapshot_refresh_age_seconds(metadata: dict[str, Any] | None, row: dict[str, Any] | None = None) -> int:
    """Choose a snapshot refresh age from lifecycle/provider completeness.

    Provider identity wins over title guessing.  If no provider ID is known, use
    a short backoff so repeated boots do not spam provider search APIs.  Ongoing
    shows refresh more often than ended shows because seasons, translations, and
    artwork evolve while they air.
    """
    metadata = metadata or {}
    if not _provider_identity_present(metadata, row):
        return MISSING_PROVIDER_BACKOFF_SECONDS

    status = str(metadata.get("status") or metadata.get("lifecycle") or "").strip().lower()
    if status in ENDED_STATUSES:
        return ENDED_SNAPSHOT_MAX_AGE_SECONDS
    if status in ONGOING_STATUSES:
        return ONGOING_SNAPSHOT_MAX_AGE_SECONDS
    return UNKNOWN_LIFECYCLE_MAX_AGE_SECONDS


def _snapshot_timestamp(row: dict[str, Any] | None) -> datetime | None:
    """Return the most reliable timestamp attached to a provider snapshot row."""
    if not row:
        return None
    metadata = _metadata_from_row(row)
    return _parse_dt(row.get("refreshed_at")) or _parse_dt(metadata.get("enriched_at")) or _parse_dt(metadata.get("selected_at"))


def metadata_row_is_fresh(row: dict[str, Any] | None, max_age_seconds: int | None = None) -> bool:
    """Return whether a category metadata snapshot is young enough to reuse."""
    timestamp = _snapshot_timestamp(row)
    if not timestamp:
        return False
    metadata = _metadata_from_row(row)
    max_age = max_age_seconds if max_age_seconds is not None else _snapshot_refresh_age_seconds(metadata, row)
    return (datetime.now(timezone.utc) - timestamp).total_seconds() < max_age


def _metadata_row_needs_artwork_only_refresh(row: dict[str, Any] | None) -> bool:
    """Return whether a fresh provider row only needs artwork materialization.

    This lets boot/status flows materialize already-known poster paths without
    running a fresh provider title search just because local artwork is missing.
    """
    if not row:
        return False
    metadata = _metadata_from_row(row)
    if _artwork_present(metadata):
        return False
    if not _provider_identity_present(metadata, row):
        return False
    return metadata_row_is_fresh(row, max_age_seconds=ARTWORK_ONLY_REFRESH_SECONDS)


async def get_fresh_category_metadata(
    db: Any,
    category_id: str,
    item_id: str,
    *,
    provider: str | None = None,
    max_age_seconds: int | None = None,
) -> dict[str, Any] | None:
    """Return the newest fresh provider snapshot payload for an item, if present."""
    media = getattr(db, "media", None) if db is not None else None
    if not media:
        return None
    rows = await media.get_category_metadata(category_id, item_id, provider=provider)
    for row in rows or []:
        if metadata_row_is_fresh(row, max_age_seconds=max_age_seconds):
            payload = dict(row.get("metadata") or {})
            payload.setdefault("provider", row.get("provider") or provider or payload.get("provider") or "metadata")
            payload.setdefault(
                "external_id",
                row.get("external_id")
                or payload.get("external_id")
                or payload.get("provider_id")
                or payload.get("tmdb_id")
                or payload.get("id")
                or "",
            )
            return payload
    return None
