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
    selected category. Category YAML may further allow/deny generic tools, but
    YAML never creates executable plumbing: a tool must still be registered in
    ToolRegistry and pass the intent/risk gate.
    """

    def __init__(self, settings: object | None = None) -> None:
        """Create a policy evaluator with optional live settings."""
        self._settings = settings

    # Tools that must remain visible independently of the selected category.
    # These are application-control and inspection primitives, not TV/Movie/etc.
    # semantics.  Category YAML can narrow category/search tools, but it must not
    # hide the user's ability to inspect storage/download state or control an
    # existing queue/download from any conversation context.
    _GLOBAL_ALWAYS_TOOLS = {
        "list_downloads",
        "manage_downloads",
        "set_download_priority",
        "download_set_priority",
        "pause_downloads",
        "resume_downloads",
        "cancel_downloads",
        "download_upload",
        "get_storage_status",
        "check_storage_capacity",
        "inspect_torrent_candidate",
        "suggestions_list",
        "list_library_shares",
    }

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
        "web_research",
        "category_web_research",
        "create_web_information_watch",
        "list_web_information_watches",
        "track_category_item",
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
        "search_soulseek",
        "queue_download",
        "enqueue_soulseek_download",
        "get_soulseek_share_plan",
        "inspect_torrent_candidate",
        "suggestions_list",
        "list_downloads",
        "list_library_shares",
        "set_download_priority",
        "manage_downloads",
        "check_storage_capacity",
        "record_category_taste_signal",
        "track_category_item",
        "create_web_information_watch",
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
        "create_scheduled_task",
        "list_scheduled_tasks",
        "remove_scheduled_task",
        "create_web_information_watch",
        "list_web_information_watches",
        "disable_web_information_watch",
        "track_category_item",
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

        tool_policy = category.category_tool_policy(self._settings) if hasattr(category, "category_tool_policy") else {}

        if intent == Intent.SEARCH:
            # Category search workflows are now part of the category contract.
            # Expose only the selected category's read-risk SEARCH workflows/actions,
            # avoiding the old global pile of TV/movie-specific tools while still
            # letting a category teach the LLM its own domain operations.
            return self._apply_category_yaml_tool_policy(
                generic_tools | self._category_tools_for_intent(category, {"read"}, confirmed, intent),
                tool_policy,
            )
        if intent == Intent.DOWNLOAD:
            # Download turns need the generic candidate workspace plus the selected
            # category's declared read/write download workflows. Destructive tools
            # remain gated by confirmation and are not exposed by default.
            return self._apply_category_yaml_tool_policy(
                generic_tools | self._category_tools_for_intent(category, {"read", "write"}, confirmed, intent),
                tool_policy,
            )
        if intent == Intent.CONFIG:
            return self._apply_category_yaml_tool_policy(
                generic_tools | self._category_action_tools(category, {"read", "write", "destructive"}, confirmed),
                tool_policy,
            )
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

    def _apply_category_yaml_tool_policy(self, allowed: set[str], policy: dict | None) -> set[str]:
        """Apply declarative category allow/deny hints to registered tool names.

        ``tools.allowed_generic`` and ``tools.category_workflows`` can only keep
        names that were already allowed by intent/risk rules. This makes the
        category config authoritative about what the category wants to expose
        without letting a YAML file invent or escalate tool access.
        """
        if not isinstance(policy, dict):
            return allowed
        deny = {str(name) for name in policy.get("deny", []) if name}
        requested: set[str] = set()
        for key in ("allowed_generic", "category_workflows", "tools"):
            values = policy.get(key)
            if isinstance(values, list):
                requested.update(str(name) for name in values if name)
        result = set(allowed) - deny
        if requested:
            # Preserve always-safe manifest/status helpers while narrowing the
            # noisy domain/search surface to what the category declared.
            always = set(self._GLOBAL_ALWAYS_TOOLS) | {
                "get_category_definitions",
                "get_category_manifest",
                "get_library_status",
                # Public web/source-discovery tools stay available even when a
                # category narrows its ordinary media/download tool surface.
                # Category YAML should not hide the user's explicit ability to
                # ask for current public news, rumours, patch notes, or fetched
                # source evidence.
                "web_search",
                "web_research",
                "category_web_research",
                "read_web_page",
                "browse_page",
                "browser_extract",
                "create_web_information_watch",
                "list_web_information_watches",
                "track_category_item",
                # Source companion tools stay globally available for downloadable categories.
                # Logs from Round 135 showed category YAML narrowing hid search_soulseek
                # from Music even though the global download policy allowed it.
                "search_soulseek",
                "enqueue_soulseek_download",
                "get_soulseek_share_plan",
            }
            result = (result & requested) | (result & always)
        return result

    def _generic_tools_for_intent(self, intent: Intent) -> set[str]:
        """Return generic tool names for an intent."""
        always = set(self._GLOBAL_ALWAYS_TOOLS)
        if intent == Intent.SEARCH:
            return set(self._GENERIC_READ_TOOLS) | always
        if intent == Intent.DOWNLOAD:
            # DOWNLOAD used to expose every generic read/research/browser tool.
            # Logs showed this inflated the function schema surface to 30+ tools
            # and encouraged repeated searches after a structured plan already
            # produced candidate IDs. Keep the ordinary download loop on the
            # declared small chain plus compact status/storage context.
            return set(self._DOWNLOAD_CONTEXT_TOOLS | self._GENERIC_DOWNLOAD_TOOLS) | always
        if intent == Intent.CONFIG:
            return set(self._GENERIC_CONFIG_TOOLS) | always
        if intent == Intent.CHAT:
            return set(self._GENERIC_READ_TOOLS) | always
        return {"get_category_definitions", "get_category_manifest"} | always

    def _category_tools_for_intent(
        self,
        category: MediaCategory,
        allowed_risks: set[str],
        confirmed: bool,
        intent: Intent | None = None,
    ) -> set[str]:
        """Return declared category action and workflow tools matching risk/intent."""
        return (
            self._category_action_tools(category, allowed_risks, confirmed)
            | self._category_workflow_tools(category, allowed_risks, confirmed, intent)
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
        intent: Intent | None = None,
    ) -> set[str]:
        """Return category workflow tool names filtered by risk and routed intent."""
        allowed: set[str] = set()
        for workflow in category.declare_workflows():
            if intent is not None and workflow.intent != intent:
                continue
            risk = workflow.risk_level
            if risk not in allowed_risks:
                continue
            if (risk == "destructive" or workflow.requires_confirmation) and not confirmed:
                continue
            if workflow.tool_name:
                allowed.add(workflow.tool_name)
        return allowed
