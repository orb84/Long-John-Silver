"""Capability metadata for the private LJS agent tool catalog.

This maps stable application tool contracts to application capabilities. It is
not user-language routing logic and must never inspect prompts or category words.
Unknown tools fail closed for constrained external principals.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.core.models import InvocationCapability


@dataclass(frozen=True)
class ToolCapabilityMetadata:
    """Required application capabilities and mutation classification."""

    required: frozenset[InvocationCapability]
    mutating: bool


class AgentToolCapabilityResolver:
    """Resolve capability requirements for registered private agent tools."""

    _DOWNLOAD_WRITES = {
        "queue_download",
        "enqueue_soulseek_download",
        "manage_downloads",
        "set_download_priority",
    }
    _TRACKING_WRITES = {
        "add_preference",
        "remove_preference",
        "create_scheduled_task",
        "remove_scheduled_task",
        "create_web_information_watch",
        "disable_web_information_watch",
        "run_web_information_watch",
        "track_category_item",
        "record_category_taste_signal",
    }
    _CONFIG_WRITES = {
        "configure_category_property",
        "apply_category_scaffold",
    }
    _KNOWN_READS = {
        "browse_page",
        "browser_evidence_report",
        "browser_extract",
        "browser_find_links",
        "browser_open",
        "browser_read_selected",
        "category_web_research",
        "check_storage_capacity",
        "compare_date_to_now",
        "enquire_about_media",
        "get_category_creation_guide",
        "get_category_definitions",
        "get_category_manifest",
        "get_imdb_details",
        "get_library_status",
        "get_plex_watched",
        "get_preferences",
        "get_recent_activity",
        "get_soulseek_share_plan",
        "get_storage_status",
        "get_upgrades",
        "inspect_torrent_candidate",
        "list_downloads",
        "list_library_files",
        "list_library_shares",
        "list_media",
        "list_media_items",
        "list_scheduled_tasks",
        "list_web_information_watches",
        "metadata_lookup",
        "plan_category_creation",
        "preview_category_scaffold",
        "read_web_page",
        "research_category_download_profile",
        "research_category_services",
        "research_release_info",
        "research_reviews",
        "search_media_torrents",
        "search_soulseek",
        "search_torrents",
        "suggestions_list",
        "web_research",
        "web_search",
    }

    @classmethod
    def for_tool(cls, tool: object) -> ToolCapabilityMetadata:
        """Resolve metadata from a declarative tool instance."""
        name = str(getattr(tool, "name", "") or "")
        explicit = getattr(tool, "required_capabilities", None)
        if explicit:
            required = cls._normalize(explicit)
            return ToolCapabilityMetadata(required=frozenset(required), mutating=cls._is_write_set(required))

        risk = str(getattr(tool, "risk_level", "") or "").strip().lower()
        if risk in {"write", "destructive"}:
            # Risk/confirmation level is not an authorization domain. Category-owned
            # mutating tools must declare their application capability explicitly;
            # otherwise constrained external principals fail closed.
            return ToolCapabilityMetadata(
                required=frozenset({InvocationCapability.ADMIN}),
                mutating=True,
            )
        if risk == "read":
            return cls.read_only()
        return cls.for_name(name)

    @classmethod
    def for_name(cls, name: str) -> ToolCapabilityMetadata:
        """Resolve metadata for a stable tool name; unknown names fail closed."""
        normalized = str(name or "").strip()
        if normalized in cls._DOWNLOAD_WRITES:
            return ToolCapabilityMetadata(
                required=frozenset({InvocationCapability.DOWNLOADS_WRITE}),
                mutating=True,
            )
        if normalized in cls._TRACKING_WRITES:
            return ToolCapabilityMetadata(
                required=frozenset({InvocationCapability.TRACKING_WRITE}),
                mutating=True,
            )
        if normalized in cls._CONFIG_WRITES:
            return ToolCapabilityMetadata(
                required=frozenset({InvocationCapability.CONFIG_WRITE}),
                mutating=True,
            )
        if normalized in cls._KNOWN_READS:
            return cls.read_only()
        return ToolCapabilityMetadata(
            required=frozenset({InvocationCapability.ADMIN}),
            mutating=True,
        )

    @staticmethod
    def read_only() -> ToolCapabilityMetadata:
        """Return the common private-agent read capability contract."""
        return ToolCapabilityMetadata(
            required=frozenset({InvocationCapability.AGENT_READ}),
            mutating=False,
        )

    @staticmethod
    def _normalize(values: object) -> set[InvocationCapability]:
        result: set[InvocationCapability] = set()
        for value in values if isinstance(values, (set, frozenset, list, tuple)) else [values]:
            try:
                result.add(InvocationCapability(str(getattr(value, "value", value))))
            except ValueError:
                result.add(InvocationCapability.ADMIN)
        return result or {InvocationCapability.ADMIN}

    @staticmethod
    def _is_write_set(values: set[InvocationCapability]) -> bool:
        return bool(values & {
            InvocationCapability.DOWNLOADS_WRITE,
            InvocationCapability.LIBRARY_WRITE,
            InvocationCapability.LIBRARY_FILES_DELETE,
            InvocationCapability.TRACKING_WRITE,
            InvocationCapability.CONFIG_WRITE,
            InvocationCapability.CONFIG_LLM_WRITE,
            InvocationCapability.CONFIG_LLM_ENDPOINT_WRITE,
            InvocationCapability.ADMIN,
        })
