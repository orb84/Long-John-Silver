"""Shared object helpers for download agent tools.

The classes in this module keep download-tool implementations small and
object-oriented.  They centralize serialization, queue ordering, status
normalization, and priority coercion so UI, LLM, and future automation tools
speak the same download vocabulary.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.core.models import DownloadPriority


class DownloadSnapshotPresenter:
    """Build stable, LLM-safe views of download domain objects.

    Use this class when a tool or UI adapter needs to expose a
    ``DownloadItem``-like object outside the downloader boundary.  Keep new
    presentation fields here rather than scattering ``getattr`` probes across
    tools.  Extension rule: add additive keys only, because chat plans and the
    frontend may already rely on the existing serialized contract.
    """

    PRIORITY_ORDER = {
        "high": 0,
        "normal": 1,
        "low": 2,
    }

    @staticmethod
    def enum_value(value: Any, default: str = "") -> str:
        """Return the string representation of enum-like fields.

        Args:
            value: Enum, string, or ``None`` value read from a download model.
            default: Fallback returned when the value is empty.

        Returns:
            A stable string suitable for JSON payloads, comparisons, and logs.
        """
        if value is None:
            return default
        raw = getattr(value, "value", value)
        return str(raw or default)

    @staticmethod
    def created_at_value(item: Any) -> datetime:
        """Return a stable timestamp used to order queued downloads.

        Args:
            item: Download item or item-like object with an optional
                ``created_at`` attribute.

        Returns:
            A timezone-naive ``datetime``. Missing or invalid timestamps sort
            last so malformed legacy rows do not jump ahead of valid queue
            entries.
        """
        value = getattr(item, "created_at", None)
        if isinstance(value, datetime):
            return value
        if isinstance(value, str) and value:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                pass
        return datetime.max

    @classmethod
    def sort_key(cls, item: Any) -> tuple:
        """Return the canonical queue/report ordering key for a download.

        Queue order is priority first, then category unit order, then insertion
        time. Unit order must come from ``DownloadItem.unit_sort_key`` because
        that property prefers category-owned descriptors and only falls back to
        legacy structured coordinates for old rows.
        """
        priority = cls.enum_value(getattr(item, "priority", None), "normal").lower()
        return (
            cls.PRIORITY_ORDER.get(priority, 1),
            tuple(getattr(item, "unit_sort_key", ()) or ()),
            cls.created_at_value(item),
            getattr(item, "id", ""),
        )

    @classmethod
    def health_state(cls, item: Any) -> str:
        """Classify the operational health of a download for reports.

        The label is deliberately compact because it is consumed by both LLM
        filters and UI badges.  When extending, prefer adding new labels only
        when a user can act on them in ``manage_downloads``.
        """
        status = cls.enum_value(getattr(item, "status", None)).lower()
        if status == "stalled":
            return "stalled"
        if status == "paused":
            return "paused"
        if status == "queued":
            return "queued"
        if status == "downloading":
            rate = float(getattr(item, "download_rate", 0.0) or 0.0)
            peers = int(getattr(item, "num_peers", 0) or 0)
            if rate <= 0 and peers <= 0:
                return "no_peers"
            if rate <= 50_000:
                return "slow"
            return "healthy"
        if status in {"seeding", "complete"}:
            return status
        return status or "unknown"

    @classmethod
    def serialize(cls, item: Any, queue_position: int | None = None) -> dict[str, Any]:
        """Convert a download object into the stable external JSON shape.

        Args:
            item: Download item, ORM row, or compatible object.
            queue_position: Optional one-based queue position for queued items.

        Returns:
            Dictionary safe to return from tools or APIs.  File details are
            capped to the first 20 entries to keep LLM contexts bounded.
        """
        progress = float(getattr(item, "progress", 0.0) or 0.0)
        import_context = getattr(item, "import_context", None)
        provider_identity = None
        if import_context and getattr(import_context, "stable_provider_key", ""):
            provider_identity = {
                "provider": import_context.provider,
                "provider_media_type": import_context.provider_media_type,
                "provider_id": import_context.provider_id,
                "stable_key": import_context.stable_provider_key,
                "stable_unit_key": import_context.stable_unit_key,
                "canonical_title": import_context.canonical_title,
                "series_start_year": import_context.series_start_year,
                "release_year": import_context.release_year,
                "season_order_type": import_context.season_order_type,
                "unit_descriptor": import_context.unit_descriptor or {},
            }
        data = {
            "id": getattr(item, "id", ""),
            "item_name": getattr(item, "item_name", ""),
            "torrent_title": getattr(item, "torrent_title", ""),
            "status": cls.enum_value(getattr(item, "status", None)),
            "priority": cls.enum_value(getattr(item, "priority", None), "normal"),
            "progress": round(progress * 100),
            "progress_fraction": progress,
            "download_rate": float(getattr(item, "download_rate", 0.0) or 0.0),
            "upload_rate": float(getattr(item, "upload_rate", 0.0) or 0.0),
            "eta_seconds": getattr(item, "eta_seconds", None),
            "num_peers": int(getattr(item, "num_peers", 0) or 0),
            "num_seeds": int(getattr(item, "num_seeds", 0) or 0),
            "source_seeders": getattr(item, "source_seeders", None),
            "total_size": int(getattr(item, "total_size", 0) or 0),
            "downloaded_bytes": int(getattr(item, "downloaded_bytes", 0) or 0),
            "season": getattr(item, "season", None),
            "episode": getattr(item, "episode", None),
            "unit_descriptor": getattr(item, "unit_descriptor", {}) or (getattr(import_context, "unit_descriptor", {}) if import_context else {}),
            "unit_label": getattr(item, "unit_label", ""),
            "stable_unit_identity": getattr(item, "stable_unit_identity", ""),
            "category_id": getattr(item, "category_id", ""),
            "item_id": getattr(item, "item_id", ""),
            "language": getattr(item, "language", ""),
            "reason": getattr(item, "reason", ""),
            "provider_identity": provider_identity,
            "health_state": cls.health_state(item),
            "created_at": str(getattr(item, "created_at", "") or ""),
        }
        if queue_position is not None:
            data["queue_position"] = queue_position
        files = getattr(item, "files", None) or []
        if files:
            data["files"] = [
                {
                    "file_index": getattr(f, "file_index", None),
                    "file_path": getattr(f, "file_path", ""),
                    "priority": getattr(f, "priority", None),
                    "season": getattr(f, "season", None),
                    "episode": getattr(f, "episode", None),
                    "unit_descriptor": getattr(f, "unit_descriptor", {}) or {},
                    "status": getattr(f, "status", ""),
                }
                for f in files[:20]
            ]
        return data

    @staticmethod
    def coerce_priority(value: Any) -> DownloadPriority | None:
        """Parse a user/model priority value into ``DownloadPriority``.

        Args:
            value: String, enum value, or ``None`` coming from UI or LLM input.

        Returns:
            Matching ``DownloadPriority`` or ``None`` when the input is not a
            valid public priority.
        """
        if value is None:
            return None
        if isinstance(value, DownloadPriority):
            return value
        try:
            return DownloadPriority(str(value).strip().lower())
        except ValueError:
            return None

    @staticmethod
    def matches_text(haystack: str, needle: str, *, exact: bool = False) -> bool:
        """Perform case-insensitive user-friendly text matching.

        Args:
            haystack: Text from a media item or torrent title.
            needle: User-provided filter text.
            exact: When true, require equality instead of containment.

        Returns:
            ``True`` when the filter matches or when the filter is empty.
        """
        h = (haystack or "").strip().lower()
        n = (needle or "").strip().lower()
        if not n:
            return True
        return h == n if exact else n in h


class SupportToolProvider:
    """Compatibility provider for helper-only tool modules.

    This module contributes service collaborators consumed by a higher-level
    provider, so it intentionally returns no standalone agent tools.  Keeping a
    provider-shaped facade preserves package-wide smoke checks while still
    allowing implementation modules to remain focused and dependency-light.
    """

    def get_tools(self) -> list:
        """Return no tools because this support module is not an agent boundary."""
        return []
