"""
Tool policy for LJS.

Selects safe tool exposure by user intent and builds tool definitions
from the registry. Category-specific tools are derived from the active
category manifest instead of global movie/TV/show allow-lists.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.ai.tool_registry import ToolRegistry
from src.core.models import Intent

if TYPE_CHECKING:
    from src.core.categories.base import MediaCategory


class AgentToolPolicy:
    """Computes allowed LLM tools from intent, category manifest, and risk.

    The policy exposes only generic application tools plus tools declared by the
    selected category. Legacy global TMDB/TVMaze/show/delete tools are not
    allow-listed here; category workflows/actions own those behaviors.
    """

    _GENERIC_READ_TOOLS = {
        "get_category_definitions",
        "get_category_manifest",
        "get_library_status",
        "suggestions_list",
        "list_downloads",
        "list_library_shares",
        "inspect_torrent_candidate",
        "get_recent_activity",
        "metadata_lookup",
        "compare_date_to_now",
        "list_library_files",
        "get_plex_watched",
        "read_web_page",
        "browse_page",
        "web_search",
        "browser_open",
        "browser_read_selected",
        "browser_find_links",
        "browser_evidence_report",
        "browser_extract",
        "research_reviews",
        "research_release_info",
        "get_storage_status",
        "enquire_about_media",
        "record_category_taste_signal",
        "get_category_creation_guide",
        "plan_category_creation",
        "research_category_services",
        "research_category_download_profile",
    }


    _DOWNLOAD_CONTEXT_TOOLS = {
        "enquire_about_media",
        "metadata_lookup",
        "compare_date_to_now",
        "get_library_status",
        "suggestions_list",
        "list_downloads",
        "list_library_shares",
        "inspect_torrent_candidate",
        "get_storage_status",
    }

    _GENERIC_DOWNLOAD_TOOLS = {
        "search_torrents",
        "search_media_torrents",
        "queue_download",
        "inspect_torrent_candidate",
        "suggestions_list",
        "list_downloads",
        "list_library_shares",
        "set_download_priority",
        "manage_downloads",
        "check_storage_capacity",
        "record_category_taste_signal",
    }

    _GENERIC_CONFIG_TOOLS = {
        "get_category_definitions",
        "get_category_manifest",
        "configure_category_property",
        "execute_category_action",
        "list_downloads",
        "list_library_shares",
        "inspect_torrent_candidate",
        "get_library_status",
        "suggestions_list",
        "get_storage_status",
        "check_storage_capacity",
        "get_category_creation_guide",
        "plan_category_creation",
        "research_category_services",
        "research_category_download_profile",
        "preview_category_scaffold",
        "apply_category_scaffold",
    }

    def allowed_tool_names(
        self,
        intent: Intent,
        category: MediaCategory | None = None,
        confirmed: bool = False,
    ) -> set[str]:
        """Return LLM tool names allowed for one intent/category pair.

        Args:
            intent: The routed user intent.
            category: The resolved active category, if any.
            confirmed: Whether a pending destructive/confirmation action has
                already been explicitly confirmed by the user.

        Returns:
            Set of registered tool names that may be exposed to the model.
        """
        generic_tools = self._generic_tools_for_intent(intent)
        if category is None:
            return generic_tools

        if intent == Intent.SEARCH:
            # SEARCH also uses generic read tools. Category-specific state is
            # exposed through the category context packet, enquire_about_media,
            # metadata_lookup, and get_category_manifest instead of bespoke tools.
            return generic_tools
        if intent == Intent.DOWNLOAD:
            # DOWNLOAD is intentionally restricted to a small generic toolchain.
            # Categories provide context, descriptors, search/ranking hooks, and
            # UI actions, but the LLM must not see dozens of category-specific
            # micro-tools such as tv.find_missing_episodes or books.download_volume.
            return generic_tools
        if intent == Intent.CONFIG:
            return generic_tools | self._category_action_tools(category, {"read", "write", "destructive"}, confirmed)
        if intent == Intent.CHAT:
            return generic_tools
        return generic_tools

    def definitions_for_intent(
        self,
        registry: ToolRegistry,
        intent: Intent,
        category: MediaCategory | None = None,
        confirmed: bool = False,
    ) -> list[dict] | None:
        """Return registered tool definitions allowed for the intent/category.

        Args:
            registry: Tool registry containing OpenAI-format definitions.
            intent: The routed user intent.
            category: Optional active category used to expose scoped tools.
            confirmed: Whether destructive actions are explicitly confirmed.

        Returns:
            Tool definitions, or None if no allowed tools are registered.
        """
        tool_names = self.allowed_tool_names(intent, category=category, confirmed=confirmed)
        return registry.get_definitions(tool_names) or None

    def _generic_tools_for_intent(self, intent: Intent) -> set[str]:
        """Return generic tool names for an intent."""
        if intent == Intent.SEARCH:
            return set(self._GENERIC_READ_TOOLS)
        if intent == Intent.DOWNLOAD:
            # DOWNLOAD used to expose every generic read/research/browser tool.
            # Logs showed this inflated the function schema surface to 30+ tools
            # and encouraged repeated searches after a structured plan already
            # produced candidate IDs. Keep the ordinary download loop on the
            # declared small chain plus compact status/storage context.
            return set(self._DOWNLOAD_CONTEXT_TOOLS | self._GENERIC_DOWNLOAD_TOOLS)
        if intent == Intent.CONFIG:
            return set(self._GENERIC_CONFIG_TOOLS)
        if intent == Intent.CHAT:
            return set(self._GENERIC_READ_TOOLS)
        return {"get_category_definitions", "get_category_manifest"}

    def _category_tools_for_intent(
        self,
        category: MediaCategory,
        allowed_risks: set[str],
        confirmed: bool,
    ) -> set[str]:
        """Return declared category action and workflow tools matching risk."""
        return (
            self._category_action_tools(category, allowed_risks, confirmed)
            | self._category_workflow_tools(category, allowed_risks, confirmed)
        )

    def _category_action_tools(
        self,
        category: MediaCategory,
        allowed_risks: set[str],
        confirmed: bool,
    ) -> set[str]:
        """Return category action tool names filtered by visibility and risk."""
        allowed: set[str] = set()
        for action in category.declare_actions():
            if not action.llm_visible:
                continue
            risk = "destructive" if action.destructive else action.risk_level
            if risk not in allowed_risks:
                continue
            if (action.destructive or action.requires_confirmation) and not confirmed:
                continue
            allowed.add(action.exposed_tool_name)
        return allowed

    def _category_workflow_tools(
        self,
        category: MediaCategory,
        allowed_risks: set[str],
        confirmed: bool,
    ) -> set[str]:
        """Return category workflow tool names filtered by risk."""
        allowed: set[str] = set()
        for workflow in category.declare_workflows():
            risk = workflow.risk_level
            if risk not in allowed_risks:
                continue
            if (risk == "destructive" or workflow.requires_confirmation) and not confirmed:
                continue
            if workflow.tool_name:
                allowed.add(workflow.tool_name)
        return allowed
