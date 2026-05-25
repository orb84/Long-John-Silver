"""LLM-facing download control objects.

This module contains the object model behind the ``manage_downloads`` tool.
It intentionally separates schema declaration, target resolution, confirmation,
and mutation execution so future download operations can be extended without
recreating a monolithic chat tool.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from loguru import logger

from src.ai.tools.download_support import DownloadSnapshotPresenter
from src.core.models import DownloadPriority, Intent, ToolExecutionContext

if TYPE_CHECKING:
    from src.core.downloader import DownloadManager


class DownloadControlSchema:
    """Build the public JSON schema for chat-driven download control.

    Keep this schema declarative and side-effect free.  When adding a new
    operation, update the enum, document its required fields here, then add the
    matching behavior to the action services.  This avoids hidden tool
    capabilities that the LLM cannot discover or validate.
    """

    ACTIONS = [
        "pause", "resume", "cancel", "set_priority",
        "move_top", "move_before", "move_after", "health_test",
    ]

    @classmethod
    def parameters(cls) -> dict[str, Any]:
        """Return the OpenAI-compatible parameter schema for the tool.

        Returns:
            JSON-schema dictionary describing actions, filters, confirmation
            fields, and queue movement anchors.  Keep fields additive when
            possible so old prompts and cached plans remain valid.
        """
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": cls.ACTIONS,
                    "description": "Download control action to apply to existing downloads.",
                },
                "filters": cls.filter_schema(
                    "Natural target filters. Omit all=true unless the user explicitly asked for every matching download."
                ),
                "priority": {
                    "type": "string",
                    "enum": ["high", "normal", "low"],
                    "description": "Priority for set_priority/move operations. move_top defaults to high.",
                },
                "anchor_filters": cls.filter_schema(
                    "Target to move before/after for move_before or move_after. Same fields as filters."
                ),
                "selection": {
                    "type": "string",
                    "enum": ["all", "first", "next_unit", "latest_unit", "next_episode", "latest_episode"],
                    "description": "Optional narrowing after filters. next_unit means earliest category unit; next_episode/latest_episode are legacy aliases.",
                },
                "limit": {"type": "integer", "description": "Optional maximum number of matched downloads to affect."},
                "confirmed": {"type": "boolean", "description": "Set true only after explicit user confirmation for cancel/broad actions."},
                "dry_run": {"type": "boolean", "description": "Resolve targets and return what would happen without mutating anything."},
                "cleanup_files": {"type": "boolean", "description": "For cancel; default true."},
                "health_test_minutes": {"type": "integer", "description": "Informational duration requested for health_test reports."},
            },
            "required": ["action"],
        }

    @staticmethod
    def filter_schema(description: str) -> dict[str, Any]:
        """Return the reusable target-filter schema.

        Args:
            description: Context-specific description for the filter object.

        Returns:
            Schema fragment shared by primary target filters and movement
            anchor filters.  Add fields here and in ``DownloadFilterPredicates``
            together so discovery and execution stay aligned.
        """
        return {
            "type": "object",
            "description": description,
            "properties": {
                "download_ids": {"type": "array", "items": {"type": "string"}},
                "id": {"type": "string"},
                "name": {"type": "string", "description": "Media/item name. Contains match by default."},
                "name_exact": {"type": "string", "description": "Exact media/item name."},
                "torrent_title_contains": {"type": "string"},
                "query": {"type": "string", "description": "Fallback text to match against item and torrent title."},
                "category_id": {"type": "string"},
                "item_id": {"type": "string"},
                "unit_key": {"type": "string", "description": "Category-owned unit stable key from unit_descriptor."},
                "unit_label": {"type": "string", "description": "Human unit label from unit_descriptor, such as a chapter, track, version, or legacy episode label."},
                "unit_granularity": {"type": "string", "description": "Category-owned unit granularity from unit_descriptor."},
                "season": {"type": "integer", "description": "Legacy structured coordinate; prefer unit_key/unit_label for new category-aware calls."},
                "episode": {"type": "integer", "description": "Legacy structured coordinate; prefer unit_key/unit_label for new category-aware calls."},
                "status": {"type": "string"},
                "statuses": {"type": "array", "items": {"type": "string"}},
                "priority": {"type": "string", "enum": ["high", "normal", "low"]},
                "stalled": {"type": "boolean"},
                "slow": {"type": "boolean"},
                "all": {"type": "boolean", "description": "True only for explicit broad commands like pause all queued downloads."},
            },
        }


class DownloadFilterPredicates:
    """Evaluate individual download filter predicates.

    This class is deliberately read-only and free of ``DownloadManager`` access.
    Extend it when adding a new filter field, then expose the field through
    ``DownloadControlSchema`` so LLM planning and execution share one contract.
    """

    SPECIFIC_FILTERS = {
        "name", "name_exact", "torrent_title_contains", "query", "category_id",
        "item_id", "unit_key", "unit_label", "unit_granularity",
        "season", "episode", "status", "statuses", "priority",
        "stalled", "slow",
    }

    def matches(self, item: Any, filters: dict[str, Any], ids: set[str], statuses: set[str]) -> bool:
        """Return whether an item matches IDs and all supplied filters.

        Empty filters never match anything unless ``all`` or explicit IDs are
        present.  That safety rule prevents accidental all-queue mutations from
        underspecified LLM calls.
        """
        item_id = str(getattr(item, "id", ""))
        if ids and item_id not in ids:
            return False
        if not bool(filters.get("all")) and not ids and not self.has_specific_filter(filters):
            return False
        return self.matches_fields(item, filters, statuses)

    def matches_fields(self, item: Any, filters: dict[str, Any], statuses: set[str]) -> bool:
        """Evaluate non-ID target fields for a download item.

        Keep each condition a narrow guard clause.  That makes user-visible
        targeting behavior easier to audit and safer to extend.
        """
        status = DownloadSnapshotPresenter.enum_value(getattr(item, "status", None)).lower()
        priority = DownloadSnapshotPresenter.enum_value(getattr(item, "priority", None), "normal").lower()
        item_name = str(getattr(item, "item_name", "") or "")
        torrent_title = str(getattr(item, "torrent_title", "") or "")
        if statuses and status not in statuses:
            return False
        if filters.get("priority") and priority != str(filters.get("priority")).lower():
            return False
        if filters.get("category_id") and str(getattr(item, "category_id", "")) != str(filters.get("category_id")):
            return False
        if filters.get("item_id") and str(getattr(item, "item_id", "")) != str(filters.get("item_id")):
            return False
        descriptor = getattr(item, "unit_descriptor", {}) or {}
        if filters.get("unit_key") and str(descriptor.get("stable_key") or "") != str(filters.get("unit_key")):
            return False
        if filters.get("unit_label") and str(descriptor.get("label") or "") != str(filters.get("unit_label")):
            return False
        if filters.get("unit_granularity") and str(descriptor.get("granularity") or "") != str(filters.get("unit_granularity")):
            return False
        # Transitional compatibility only: public schemas still accept these
        # fields for existing TV-oriented prompts and persisted rows. New tool
        # calls should prefer descriptor-backed unit_key/unit_label filters.
        if filters.get("season") is not None and getattr(item, "season", None) != int(filters.get("season")):
            return False
        if filters.get("episode") is not None and getattr(item, "episode", None) != int(filters.get("episode")):
            return False
        return self.matches_text_fields(item_name, torrent_title, filters)

    def matches_text_fields(self, item_name: str, torrent_title: str, filters: dict[str, Any]) -> bool:
        """Evaluate name, torrent-title, and health text filters.

        Returns:
            ``True`` when all text and health constraints pass.  Reuse this
            method for tests that exercise natural-language matching behavior.
        """
        if filters.get("name_exact") and not DownloadSnapshotPresenter.matches_text(item_name, str(filters.get("name_exact")), exact=True):
            return False
        if filters.get("name") and not DownloadSnapshotPresenter.matches_text(item_name, str(filters.get("name"))):
            return False
        if filters.get("torrent_title_contains") and not DownloadSnapshotPresenter.matches_text(torrent_title, str(filters.get("torrent_title_contains"))):
            return False
        if filters.get("query"):
            query = str(filters.get("query"))
            if not (DownloadSnapshotPresenter.matches_text(item_name, query) or DownloadSnapshotPresenter.matches_text(torrent_title, query)):
                return False
        return True

    def matches_health(self, item: Any, filters: dict[str, Any]) -> bool:
        """Evaluate health filters that depend on live swarm/progress state.

        Args:
            item: Download item with status, speed, and peer fields.
            filters: Target filter object.

        Returns:
            ``True`` when health constraints pass.  The method is separated so
            future health labels can be added without disturbing text matching.
        """
        health = DownloadSnapshotPresenter.health_state(item)
        if filters.get("stalled") is True and health != "stalled":
            return False
        if filters.get("slow") is True and health not in {"slow", "no_peers", "stalled"}:
            return False
        return True

    def has_specific_filter(self, filters: dict[str, Any]) -> bool:
        """Return whether filters contain any non-empty targeted constraint.

        This guard is a safety net for LLM calls: a call with ``filters: {}``
        must not mutate the whole queue by accident.
        """
        return any(
            key in filters and filters.get(key) not in (None, "", [], {})
            for key in self.SPECIFIC_FILTERS
        )


class DownloadFilterResolver:
    """Resolve natural-language download filters against live downloads.

    The resolver is a read-only adapter around ``DownloadFilterPredicates``.
    It can be unit-tested with simple item stubs and reused by UI previews,
    dry runs, scheduled automations, or future non-LLM control surfaces.
    """

    def __init__(self, predicates: DownloadFilterPredicates | None = None) -> None:
        """Create a resolver with injectable predicate logic for tests.

        Args:
            predicates: Optional predicate evaluator.  Inject a custom instance
                to test new filter fields independently from resolution order.
        """
        self._predicates = predicates or DownloadFilterPredicates()

    def resolve(self, downloads: list[Any], filters: dict[str, Any]) -> list[Any]:
        """Return downloads matching explicit IDs or semantic filters.

        Args:
            downloads: Current download snapshot from ``DownloadManager``.
            filters: User/LLM filter object from ``manage_downloads``.
        """
        if not filters:
            return []
        ids = self.filter_ids(filters)
        statuses = self.normalized_statuses(filters)
        out = [item for item in downloads if self.matches(item, filters, ids, statuses)]
        out.sort(key=DownloadSnapshotPresenter.sort_key)
        return out

    def matches(self, item: Any, filters: dict[str, Any], ids: set[str], statuses: set[str]) -> bool:
        """Return whether a download item passes all resolver predicates.

        This wrapper keeps the health predicate close to the main predicate while
        allowing ``DownloadFilterPredicates`` to stay focused and small.
        """
        return self._predicates.matches(item, filters, ids, statuses) and self._predicates.matches_health(item, filters)

    def apply_selection(self, items: list[Any], selection: str, limit: Any) -> list[Any]:
        """Narrow a matched list using viewing-order semantics.

        ``next_unit`` means the earliest category-declared unit and defaults
        to one item.  ``next_episode``/``latest_episode`` remain accepted aliases
        for older prompts but use the same descriptor-first order.
        """
        if selection in {"next_unit", "next_episode"}:
            items = sorted(items, key=self.unit_order_key)
            limit = 1 if limit is None else limit
        elif selection in {"latest_unit", "latest_episode"}:
            items = sorted(items, key=self.unit_order_key, reverse=True)
            limit = 1 if limit is None else limit
        elif selection == "first":
            limit = 1 if limit is None else limit
        return self.apply_limit(items, limit)

    def confirmation_needed(self, action: str, filters: dict[str, Any], matched: list[Any], confirmed: bool) -> str:
        """Return a human confirmation reason for risky operations.

        Empty string means execution can continue.  Non-empty text should be
        shown to the user before retrying with ``confirmed=true``.
        """
        if confirmed:
            return ""
        if action == "cancel":
            return "Cancelling downloads removes them from the active queue and may clean up partial files."
        if bool(filters.get("all")) and len(matched) > 1 and action in {"pause", "resume", "set_priority", "move_top", "health_test"}:
            return f"This is a broad {action} operation affecting {len(matched)} downloads."
        return ""

    def filter_ids(self, filters: dict[str, Any]) -> set[str]:
        """Extract explicit download IDs from a filter object.

        Supports both ``download_ids`` and legacy ``id`` fields while ignoring
        empty values.
        """
        ids = set(str(i) for i in (filters.get("download_ids") or []) if i)
        if filters.get("id"):
            ids.add(str(filters.get("id")))
        return ids

    def normalized_statuses(self, filters: dict[str, Any]) -> set[str]:
        """Return status filters after applying user-friendly aliases."""
        statuses: set[str] = set()
        if filters.get("status"):
            statuses.add(self.normalize_status(filters.get("status")))
        for status in filters.get("statuses") or []:
            statuses.add(self.normalize_status(status))
        statuses.discard("")
        return statuses

    def normalize_status(self, value: Any) -> str:
        """Normalize natural status aliases to downloader status values."""
        status = str(value or "").strip().lower()
        aliases = {
            "active": "downloading",
            "running": "downloading",
            "parked": "stalled",
            "held": "paused",
            "waiting": "queued",
        }
        return aliases.get(status, status)

    def unit_order_key(self, item: Any) -> tuple:
        """Return descriptor-first ordering for category-unit selection."""
        return (
            tuple(getattr(item, "unit_sort_key", ()) or ()),
            DownloadSnapshotPresenter.created_at_value(item),
        )

    @staticmethod
    def apply_limit(items: list[Any], limit: Any) -> list[Any]:
        """Apply an optional numeric limit to a resolved download list.

        Invalid limit values are ignored rather than treated as errors because
        target resolution should be forgiving after the LLM has already matched
        specific downloads.
        """
        if limit is None:
            return items
        try:
            return items[:max(0, int(limit))]
        except (TypeError, ValueError):
            return items


class DownloadItemActionService:
    """Execute item-level download actions such as pause or cancel.

    This service owns status guards for mutating calls.  Keep guard clauses near
    the ``DownloadManager`` invocation so new download statuses do not
    accidentally become valid for destructive operations.
    """

    def __init__(self, downloader: DownloadManager) -> None:
        """Create an item action service for one download manager."""
        self._downloader = downloader

    async def apply_many(
        self,
        action: str,
        matched: list[Any],
        arguments: dict[str, Any],
        succeeded: list[dict[str, Any]],
        failed: list[dict[str, Any]],
        skipped: list[dict[str, Any]],
    ) -> None:
        """Apply an item-level action to each target and append outcomes."""
        for item in matched:
            await self.apply_one(action, item, arguments, succeeded, failed, skipped)

    async def apply_one(
        self,
        action: str,
        item: Any,
        arguments: dict[str, Any],
        succeeded: list[dict[str, Any]],
        failed: list[dict[str, Any]],
        skipped: list[dict[str, Any]],
    ) -> None:
        """Apply one item-level operation and record the result envelope."""
        download_id = getattr(item, "id", "")
        status = DownloadSnapshotPresenter.enum_value(getattr(item, "status", None)).lower()
        try:
            updated = await self.dispatch(action, download_id, status, arguments, skipped, failed)
            if updated == "cancelled":
                succeeded.append({"id": download_id, "status": "cancelled"})
            elif updated is None:
                failed.append({"id": download_id, "error": "operation returned no updated download"})
            else:
                succeeded.append(DownloadSnapshotPresenter.serialize(updated))
        except Exception as exc:
            failed.append({"id": download_id, "error": str(exc)})

    async def dispatch(
        self,
        action: str,
        download_id: str,
        status: str,
        arguments: dict[str, Any],
        skipped: list[dict[str, Any]],
        failed: list[dict[str, Any]],
    ) -> Any:
        """Dispatch a validated item-level action to ``DownloadManager``."""
        if action == "pause":
            return await self.pause(download_id, status, skipped)
        if action == "resume":
            return await self.resume(download_id, status, skipped)
        if action == "health_test":
            return await self.health_test(download_id, failed)
        if action == "cancel":
            cleanup = arguments.get("cleanup_files")
            await self._downloader.cancel_download(download_id, cleanup_files=True if cleanup is None else bool(cleanup))
            return "cancelled"
        if action == "set_priority":
            priority = DownloadSnapshotPresenter.coerce_priority(arguments.get("priority"))
            if priority is None:
                raise ValueError("set_priority requires priority: high, normal, or low")
            return await self._downloader.set_priority(download_id, priority)
        raise ValueError(f"Unsupported download item action: {action}")

    async def pause(self, download_id: str, status: str, skipped: list[dict[str, Any]]) -> Any:
        """Pause a queued or actively downloading item."""
        if status not in {"downloading", "queued"}:
            skipped.append({"id": download_id, "reason": f"status {status} cannot be paused"})
            return None
        return await self._downloader.pause_download(download_id)

    async def resume(self, download_id: str, status: str, skipped: list[dict[str, Any]]) -> Any:
        """Resume a paused or stalled item."""
        if status not in {"paused", "stalled"}:
            skipped.append({"id": download_id, "reason": f"status {status} cannot be resumed"})
            return None
        return await self._downloader.resume_download(download_id)

    async def health_test(self, download_id: str, failed: list[dict[str, Any]]) -> Any:
        """Start a temporary high-priority health test for one download."""
        if not hasattr(self._downloader, "start_health_test"):
            failed.append({"id": download_id, "error": "Downloader does not expose start_health_test"})
            return None
        return await self._downloader.start_health_test(download_id, temporary_priority=DownloadPriority.HIGH)


class DownloadQueueMoveService:
    """Execute queue-order mutations for chat-controlled downloads.

    Queue movement is represented by adjusted ``created_at`` timestamps while
    preserving the existing priority-based ordering model.  This avoids a risky
    schema migration while keeping the behavior deterministic.
    """

    def __init__(self, downloader: DownloadManager) -> None:
        """Create a queue movement service for one download manager."""
        self._downloader = downloader

    async def move_top(
        self,
        matched: list[Any],
        arguments: dict[str, Any],
        succeeded: list[dict[str, Any]],
        failed: list[dict[str, Any]],
        skipped: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Move downloads ahead of the current queue by timestamp and priority."""
        priority = DownloadSnapshotPresenter.coerce_priority(arguments.get("priority")) or DownloadPriority.HIGH
        active = await self._downloader.get_active_downloads()
        queued = [i for i in active if DownloadSnapshotPresenter.enum_value(getattr(i, "status", None)).lower() == "queued"]
        earliest = min([DownloadSnapshotPresenter.created_at_value(i) for i in queued] or [datetime.now()])
        for offset, item in enumerate(matched):
            try:
                item.created_at = earliest - timedelta(seconds=len(matched) - offset + 1)
                item.priority = priority
                await self.persist_download(item)
                updated = await self._downloader.set_priority(getattr(item, "id"), priority)
                succeeded.append(DownloadSnapshotPresenter.serialize(updated or item, queue_position=offset + 1))
            except Exception as exc:
                failed.append({"id": getattr(item, "id", ""), "error": str(exc)})
        return DownloadControlActionService.result_payload(succeeded, failed, skipped)

    async def move_relative(
        self,
        action: str,
        matched: list[Any],
        anchor: Any,
        succeeded: list[dict[str, Any]],
        failed: list[dict[str, Any]],
        skipped: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Move downloads immediately before or after another queue item."""
        if anchor is None:
            return {"error": f"{action} requires exactly one anchor download"}
        anchor_time = DownloadSnapshotPresenter.created_at_value(anchor)
        if anchor_time == datetime.max:
            anchor_time = datetime.now()
        anchor_priority = DownloadSnapshotPresenter.coerce_priority(getattr(anchor, "priority", None)) or DownloadPriority.NORMAL
        direction = -1 if action == "move_before" else 1
        ordered = list(reversed(matched)) if action == "move_before" else matched
        for offset, item in enumerate(ordered, start=1):
            try:
                item.created_at = anchor_time + timedelta(seconds=direction * offset)
                item.priority = anchor_priority
                await self.persist_download(item)
                updated = await self._downloader.set_priority(getattr(item, "id"), anchor_priority)
                succeeded.append(DownloadSnapshotPresenter.serialize(updated or item))
            except Exception as exc:
                failed.append({"id": getattr(item, "id", ""), "error": str(exc)})
        if action == "move_before":
            succeeded.reverse()
        payload = DownloadControlActionService.result_payload(succeeded, failed, skipped)
        payload["anchor"] = DownloadSnapshotPresenter.serialize(anchor)
        return payload

    async def persist_download(self, item: Any) -> None:
        """Persist queue-order changes through the downloader public API."""
        if hasattr(self._downloader, "update_download"):
            await self._downloader.update_download(item)
            return
        raise RuntimeError("Downloader cannot persist queue-order changes through its public API")


class DownloadControlActionService:
    """Orchestrate safe download-control mutations.

    This facade delegates item actions and queue moves to focused services while
    preserving one result envelope for the LLM and UI.  Add new operations by
    creating a focused collaborator instead of expanding this facade.
    """

    def __init__(self, downloader: DownloadManager) -> None:
        """Create an action service for one concrete download manager."""
        self._item_actions = DownloadItemActionService(downloader)
        self._queue_moves = DownloadQueueMoveService(downloader)

    async def apply(self, action: str, matched: list[Any], arguments: dict[str, Any], anchor: Any = None) -> dict[str, Any]:
        """Apply one action to an already-resolved target list."""
        succeeded: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        if action == "move_top":
            return await self._queue_moves.move_top(matched, arguments, succeeded, failed, skipped)
        if action in {"move_before", "move_after"}:
            return await self._queue_moves.move_relative(action, matched, anchor, succeeded, failed, skipped)
        await self._item_actions.apply_many(action, matched, arguments, succeeded, failed, skipped)
        payload = self.result_payload(succeeded, failed, skipped)
        if action == "health_test" and arguments.get("health_test_minutes"):
            payload["health_test_minutes"] = int(arguments.get("health_test_minutes"))
            payload["note"] = "Health test was started now; stopping after the requested duration requires the health supervisor/scheduler."
        return payload

    @staticmethod
    def result_payload(
        succeeded: list[dict[str, Any]],
        failed: list[dict[str, Any]],
        skipped: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Return the standard mutation-result envelope.

        This shape lets the LLM and UI summarize what happened and decide
        whether a retry or user clarification is needed.
        """
        return {
            "succeeded": succeeded,
            "failed": failed,
            "skipped": skipped,
            "updated_count": len(succeeded),
            "failed_count": len(failed),
            "skipped_count": len(skipped),
        }


class ManageDownloadsTool:
    """Resolve natural download targets and perform safe queue actions.

    The tool is a thin orchestration facade.  Schema changes belong in
    ``DownloadControlSchema`` and downloader mutations belong in action
    services.  Destructive or broad actions must continue to flow through this
    class's confirmation gate before mutation.
    """

    name = "manage_downloads"
    description = (
        "Control existing downloads from chat after inspecting list_downloads. "
        "Supports pause, resume, cancel, set_priority, move_top, move_before, "
        "move_after, and health_test. Targets can be resolved by download id, "
        "media name, torrent title text, season/episode, status, priority, "
        "category, stalled/slow health, or explicit all=true. Cancellation and "
        "broad operations return confirmation_required unless confirmed=true."
    )
    intents = {Intent.DOWNLOAD}
    allow_direct = True
    requires_confirmation = False
    destructive = True
    required_dependencies = ["downloader"]

    def __init__(self, downloader: DownloadManager | None = None) -> None:
        """Create the tool with its runtime download dependency."""
        self._downloader = downloader
        self._resolver = DownloadFilterResolver()

    def parameters(self) -> dict[str, Any]:
        """Return the public tool schema advertised to the LLM runtime."""
        return DownloadControlSchema.parameters()

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> object:
        """Resolve targets, enforce confirmation rules, and apply the action."""
        if not self._downloader:
            return {"error": "Download manager not available"}
        action = str(arguments.get("action") or "").strip().lower()
        if action not in set(DownloadControlSchema.ACTIONS):
            return {"error": f"Unsupported download action: {action or '(missing)'}"}
        filters = arguments.get("filters") or {}
        if not isinstance(filters, dict):
            return {"error": "filters must be an object"}
        active = await self.active_downloads()
        if isinstance(active, dict):
            return active
        matched = self.selected_matches(active, filters, arguments)
        if not matched:
            return self.no_matches_payload(action)
        anchor_payload = self.resolve_anchor(action, active, matched, arguments)
        if isinstance(anchor_payload, dict):
            return anchor_payload
        confirmation = self._resolver.confirmation_needed(action, filters, matched, bool(arguments.get("confirmed")))
        if confirmation:
            return self.confirmation_payload(action, confirmation, matched)
        if arguments.get("dry_run"):
            return self.dry_run_payload(action, matched, anchor_payload)
        return await self.apply_action(action, matched, arguments, anchor_payload)

    async def active_downloads(self) -> list[Any] | dict[str, str]:
        """Return active downloads or an error payload when listing fails."""
        try:
            return await self._downloader.get_active_downloads()
        except Exception as exc:
            logger.error(f"manage_downloads could not list downloads: {exc}")
            return {"error": str(exc)}

    def selected_matches(self, active: list[Any], filters: dict[str, Any], arguments: dict[str, Any]) -> list[Any]:
        """Resolve filters and apply optional selection/limit semantics."""
        matched = self._resolver.resolve(active, filters)
        selection = str(arguments.get("selection") or "all").lower()
        return self._resolver.apply_selection(matched, selection, arguments.get("limit"))

    def resolve_anchor(self, action: str, active: list[Any], matched: list[Any], arguments: dict[str, Any]) -> Any:
        """Resolve the anchor download for relative queue moves."""
        if action not in {"move_before", "move_after"}:
            return None
        anchor_filters = arguments.get("anchor_filters") or {}
        if not isinstance(anchor_filters, dict) or not anchor_filters:
            return {"error": f"{action} requires anchor_filters to identify the queue item to move around."}
        matched_ids = {getattr(m, "id", None) for m in matched}
        anchors = [a for a in self._resolver.resolve(active, anchor_filters) if getattr(a, "id", None) not in matched_ids]
        if len(anchors) != 1:
            return {
                "status": "ambiguous_anchor",
                "action": action,
                "anchor_count": len(anchors),
                "anchors": [DownloadSnapshotPresenter.serialize(a) for a in anchors[:10]],
                "message": "Move-before/after needs exactly one anchor download.",
            }
        return anchors[0]

    async def apply_action(self, action: str, matched: list[Any], arguments: dict[str, Any], anchor: Any) -> dict[str, Any]:
        """Apply a confirmed action and wrap service errors for tool callers."""
        try:
            result = await DownloadControlActionService(self._downloader).apply(action, matched, arguments, anchor=anchor)
        except Exception as exc:
            logger.error(f"manage_downloads action {action} failed: {exc}")
            return {"error": str(exc)}
        return {"status": "ok", "action": action, "matched_count": len(matched), **result}

    @staticmethod
    def no_matches_payload(action: str) -> dict[str, Any]:
        """Return a standard no-match payload for unmatched filters."""
        return {
            "status": "no_matches",
            "action": action,
            "matched_count": 0,
            "message": "No current downloads matched the requested filters.",
        }

    @staticmethod
    def confirmation_payload(action: str, reason: str, matched: list[Any]) -> dict[str, Any]:
        """Return a confirmation-required payload for risky operations."""
        return {
            "status": "confirmation_required",
            "confirmation_required": True,
            "action": action,
            "reason": reason,
            "matched_count": len(matched),
            "matched": [DownloadSnapshotPresenter.serialize(item) for item in matched[:20]],
            "confirmation_hint": "Ask the user to confirm, then call manage_downloads again with confirmed=true and the same filters.",
        }

    @staticmethod
    def dry_run_payload(action: str, matched: list[Any], anchor: Any) -> dict[str, Any]:
        """Return a non-mutating preview of the resolved operation."""
        return {
            "status": "dry_run",
            "action": action,
            "matched_count": len(matched),
            "matched": [DownloadSnapshotPresenter.serialize(item) for item in matched[:20]],
            "anchor": DownloadSnapshotPresenter.serialize(anchor) if anchor else None,
        }


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
