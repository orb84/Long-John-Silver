"""
Tests for AgentToolPolicy — category-scoped tool exposure.
"""

from src.ai.tool_policy import AgentToolPolicy
from src.core.categories.registry import CategoryRegistry
from src.core.models import Intent


class TestAgentToolPolicy:
    """Tests for intent-scoped and category-scoped tool allow-lists."""

    def setup_method(self):
        self.policy = AgentToolPolicy()
        self.registry = CategoryRegistry.with_defaults()

    def test_search_excludes_legacy_mutating_tools(self):
        """SEARCH should not include legacy show/TMDB mutating tools."""
        movie = self.registry.get("movie")
        allowed = self.policy.allowed_tool_names(Intent.SEARCH, category=movie)
        assert "category_item_add" not in allowed
        assert "category_item_remove" not in allowed
        assert "movie.resolve_metadata" not in allowed
        assert "tv.resolve_show" not in allowed
        assert "movie.delete_item" not in allowed

    def test_download_includes_generic_download_and_category_workflows(self):
        """DOWNLOAD should include generic torrent tools and category workflow tools."""
        tv = self.registry.get("tv")
        download_tools = self.policy.allowed_tool_names(Intent.DOWNLOAD, category=tv)
        assert "search_torrents" in download_tools
        assert "queue_download" in download_tools
        assert "tv.download_next_missing_episode" in download_tools
        assert "category_item_add" not in download_tools

    def test_config_includes_category_action_tools_without_unconfirmed_destructive(self):
        """CONFIG should include category action tools but hide unconfirmed destructive actions."""
        movie = self.registry.get("movie")
        config_tools = self.policy.allowed_tool_names(Intent.CONFIG, category=movie)
        assert "get_category_manifest" in config_tools
        assert "execute_category_action" in config_tools
        assert "get_category_creation_guide" in config_tools
        assert "plan_category_creation" in config_tools
        assert "research_category_services" in config_tools
        assert "preview_category_scaffold" in config_tools
        assert "apply_category_scaffold" in config_tools
        assert "movie.refresh_metadata" in config_tools
        assert "movie.delete_item" not in config_tools

    def test_destructive_category_tool_requires_confirmation(self):
        """Destructive category actions are exposed only after confirmation."""
        movie = self.registry.get("movie")
        tools = self.policy.allowed_tool_names(Intent.CONFIG, category=movie, confirmed=True)
        assert "movie.delete_item" in tools

    def test_chat_includes_safe_generic_tools(self):
        """CHAT should have informational tools only."""
        chat_tools = self.policy.allowed_tool_names(Intent.CHAT)
        assert "get_library_status" in chat_tools
        assert "list_downloads" in chat_tools
        assert "plan_category_creation" in chat_tools
        assert "research_category_services" in chat_tools
        assert "apply_category_scaffold" not in chat_tools
        assert "queue_download" not in chat_tools
        assert "tv.delete_item" not in chat_tools
