"""
Tests for the declarative AgentTool system.

Verifies tool metadata completeness, intent-based filtering,
and execution of declarative tools with fake dependencies.
"""

import pytest

from src.ai.tools.base import AgentTool
from src.core.models import ToolExecutionContext
from src.ai.tools.downloads import (
    ListDownloadsTool,
    QueueDownloadTool,
    SetDownloadPriorityTool,
    ManageDownloadsTool,
)
from src.ai.tools.preferences import GetPreferencesTool, GetRecentActivityTool
from src.ai.tool_registry import ToolRegistry
from src.core.models import Intent


class TestAgentToolMetadata:
    """Every declarative tool must declare complete metadata."""

    @pytest.fixture
    def tools(self) -> list[AgentTool]:
        """Return all declarative tool instances."""
        return [
            ListDownloadsTool(downloader=object()),
            GetPreferencesTool(preference_manager=object()),
            GetRecentActivityTool(downloader=object()),
            QueueDownloadTool(scheduler=object()),
            SetDownloadPriorityTool(scheduler=object()),
            ManageDownloadsTool(downloader=object()),
        ]

    def test_every_tool_has_name(self, tools: list[AgentTool]) -> None:
        """Each tool must have a non-empty name string."""
        for tool in tools:
            assert hasattr(tool, "name"), f"{type(tool).__name__} missing name"
            assert isinstance(tool.name, str), f"{type(tool).__name__} name not str"
            assert len(tool.name) > 0, f"{type(tool).__name__} name is empty"

    def test_every_tool_has_description(self, tools: list[AgentTool]) -> None:
        """Each tool must have a non-empty description string."""
        for tool in tools:
            assert hasattr(tool, "description"), f"{type(tool).__name__} missing description"
            assert isinstance(tool.description, str), f"{type(tool).__name__} description not str"
            assert len(tool.description) > 0, f"{type(tool).__name__} description is empty"

    def test_every_tool_has_intents(self, tools: list[AgentTool]) -> None:
        """Each tool must declare a non-empty set of intents."""
        for tool in tools:
            assert hasattr(tool, "intents"), f"{type(tool).__name__} missing intents"
            assert isinstance(tool.intents, set), f"{type(tool).__name__} intents not set"
            assert len(tool.intents) > 0, f"{type(tool).__name__} intents is empty"

    def test_every_tool_has_parameters(self, tools: list[AgentTool]) -> None:
        """Each tool must return a valid OpenAI-format parameters dict."""
        for tool in tools:
            params = tool.parameters()
            assert isinstance(params, dict), f"{type(tool).__name__} parameters not dict"
            assert "type" in params, f"{type(tool).__name__} parameters missing 'type'"
            assert "properties" in params, f"{type(tool).__name__} parameters missing 'properties'"

    def test_every_tool_has_execute(self, tools: list[AgentTool]) -> None:
        """Each tool must have an async execute method."""
        for tool in tools:
            assert hasattr(tool, "execute"), f"{type(tool).__name__} missing execute"
            assert callable(tool.execute), f"{type(tool).__name__} execute not callable"

    def test_every_tool_matches_agent_tool_protocol(self, tools: list[AgentTool]) -> None:
        """Each tool must satisfy the AgentTool protocol at runtime."""
        for tool in tools:
            assert isinstance(tool, AgentTool), (
                f"{type(tool).__name__} does not satisfy AgentTool protocol"
            )

    def test_every_tool_has_action_metadata(self, tools: list[AgentTool]) -> None:
        """Each tool must declare allow_direct, requires_confirmation, destructive."""
        for tool in tools:
            assert hasattr(tool, "allow_direct"), f"{type(tool).__name__} missing allow_direct"
            assert isinstance(tool.allow_direct, bool), f"{type(tool).__name__} allow_direct not bool"
            assert hasattr(tool, "requires_confirmation"), f"{type(tool).__name__} missing requires_confirmation"
            assert isinstance(tool.requires_confirmation, bool), f"{type(tool).__name__} requires_confirmation not bool"
            assert hasattr(tool, "destructive"), f"{type(tool).__name__} missing destructive"
            assert isinstance(tool.destructive, bool), f"{type(tool).__name__} destructive not bool"


@pytest.fixture
def registry() -> ToolRegistry:
    """Create a registry with fake declarative tools for filtering tests."""
    reg = ToolRegistry()

    class FakeDownloadTool:
        name = "fake_download"
        description = "Fake download tool"
        intents = {Intent.DOWNLOAD}
        allow_direct = True
        requires_confirmation = False
        destructive = False

        def parameters(self) -> dict:
            return {"type": "object", "properties": {}, "required": []}

        async def execute(self, arguments: dict, context: ToolExecutionContext) -> object:
            return {"downloaded": True}

    class FakeSearchTool:
        name = "fake_search"
        description = "Fake search tool"
        intents = {Intent.SEARCH}
        allow_direct = False
        requires_confirmation = False
        destructive = False

        def parameters(self) -> dict:
            return {"type": "object", "properties": {}, "required": []}

        async def execute(self, arguments: dict, context: ToolExecutionContext) -> object:
            return {"results": []}

    class FakeConfigTool:
        name = "fake_config"
        description = "Fake config tool"
        intents = {Intent.CONFIG}
        allow_direct = False
        requires_confirmation = False
        destructive = False

        def parameters(self) -> dict:
            return {"type": "object", "properties": {}, "required": []}

        async def execute(self, arguments: dict, context: ToolExecutionContext) -> object:
            return {"configured": True}

    class FakeCrossCuttingTool:
        name = "fake_cross"
        description = "Fake cross-cutting tool"
        intents = {Intent.SEARCH, Intent.DOWNLOAD, Intent.CONFIG}
        allow_direct = True
        requires_confirmation = False
        destructive = False

        def parameters(self) -> dict:
            return {"type": "object", "properties": {}, "required": []}

        async def execute(self, arguments: dict, context: ToolExecutionContext) -> object:
            return {"ok": True}

    reg.register_tool(FakeDownloadTool())
    reg.register_tool(FakeSearchTool())
    reg.register_tool(FakeConfigTool())
    reg.register_tool(FakeCrossCuttingTool())

    return reg


class TestIntentFiltering:
    """ToolRegistry must filter tools correctly by intent."""

    def test_download_intent_includes_download_tools(self, registry: ToolRegistry) -> None:
        """DOWNLOAD intent must include download-scoped tools."""
        names = registry.get_tool_names_for_intent(Intent.DOWNLOAD)
        assert "fake_download" in names
        assert "fake_cross" in names

    def test_download_intent_excludes_search_only_tools(self, registry: ToolRegistry) -> None:
        """DOWNLOAD intent must NOT include SEARCH-only tools."""
        names = registry.get_tool_names_for_intent(Intent.DOWNLOAD)
        assert "fake_search" not in names
        assert "fake_config" not in names

    def test_search_intent_includes_search_tools(self, registry: ToolRegistry) -> None:
        """SEARCH intent must include search-scoped tools."""
        names = registry.get_tool_names_for_intent(Intent.SEARCH)
        assert "fake_search" in names
        assert "fake_cross" in names

    def test_search_intent_excludes_config_only_tools(self, registry: ToolRegistry) -> None:
        """SEARCH intent must NOT include CONFIG-only tools."""
        names = registry.get_tool_names_for_intent(Intent.SEARCH)
        assert "fake_config" not in names

    def test_config_intent_includes_config_tools(self, registry: ToolRegistry) -> None:
        """CONFIG intent must include config-scoped tools."""
        names = registry.get_tool_names_for_intent(Intent.CONFIG)
        assert "fake_config" in names
        assert "fake_cross" in names

    def test_config_intent_excludes_download_only_tools(self, registry: ToolRegistry) -> None:
        """CONFIG intent must NOT include DOWNLOAD-only tools."""
        names = registry.get_tool_names_for_intent(Intent.CONFIG)
        assert "fake_download" not in names

    def test_chat_intent_returns_no_tools(self, registry: ToolRegistry) -> None:
        """CHAT intent should have no tools."""
        names = registry.get_tool_names_for_intent(Intent.CHAT)
        assert len(names) == 0

    def test_definitions_for_intent_returns_openai_format(self, registry: ToolRegistry) -> None:
        """Definitions for DOWNLOAD should be in OpenAI format."""
        defs = registry.get_definitions_for_intent(Intent.DOWNLOAD)
        assert len(defs) == 2  # fake_download + fake_cross
        names = {d["function"]["name"] for d in defs}
        assert "fake_download" in names
        assert "fake_cross" in names

    def test_legacy_tools_without_intents_not_in_filtered_results(self, registry: ToolRegistry) -> None:
        """Tools registered via register() without intents should NOT appear in filtered results."""
        reg = ToolRegistry()
        reg.register(
            name="legacy_tool",
            description="No intents set",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=lambda: {},
        )
        names = reg.get_tool_names_for_intent(Intent.DOWNLOAD)
        assert "legacy_tool" not in names


class FakeDownloader:
    """Fake download manager for testing declarative tool execution."""

    def __init__(self) -> None:
        self._downloads = [
            FakeDownload(id="1", item_name="Test Show", status="downloading",
                        priority="high", progress=0.5, reason="new_episode",
                        eta_seconds=120),
        ]

    async def get_active_downloads(self) -> list:
        return self._downloads

    async def get_recent_downloads(self, limit: int = 10) -> list:
        return self._downloads

    async def pause_download(self, download_id: str, requeue: bool = False, keep_start_allowed: bool = False):
        dl = self._find(download_id)
        if dl is None:
            return None
        dl.status = FakeEnum('paused')
        return dl

    async def resume_download(self, download_id: str):
        dl = self._find(download_id)
        if dl is None:
            return None
        dl.status = FakeEnum('downloading')
        return dl

    async def cancel_download(self, download_id: str, cleanup_files: bool = True):
        dl = self._find(download_id)
        if dl is not None:
            dl.status = FakeEnum('cancelled')

    async def set_priority(self, download_id: str, priority):
        dl = self._find(download_id)
        if dl is None:
            return None
        dl.priority = FakeEnum(getattr(priority, 'value', priority))
        return dl

    async def update_download(self, item):
        return None

    def active_count(self) -> int:
        return sum(1 for d in self._downloads if d.status.value == 'downloading')

    def max_concurrent(self) -> int:
        return 3

    def _find(self, download_id: str):
        return next((d for d in self._downloads if d.id == download_id), None)


class FakeDownload:
    """Fake download item for testing."""
    def __init__(self, id: str, item_name: str, status: str,
                 priority: str, progress: float, reason: str,
                 eta_seconds: int | None = None) -> None:
        self.id = id
        self.item_name = item_name
        self.status = FakeEnum(status)
        self.priority = FakeEnum(priority)
        self.progress = progress
        self.reason = reason
        self.eta_seconds = eta_seconds
        self.created_at = None


class FakeEnum:
    """Fake enum that returns its value."""
    def __init__(self, value: str) -> None:
        self.value = value


class FakePreferenceManager:
    """Fake preference manager for testing."""

    async def get_summary(self) -> str:
        return "Likes: Sci-Fi, Action | Dislikes: Horror"


class TestDeclarativeToolExecution:
    """Declarative tools must execute correctly with fake dependencies."""

    @pytest.mark.asyncio
    async def test_list_downloads_returns_active_list(self) -> None:
        """list_downloads should return formatted active downloads."""
        tool = ListDownloadsTool(downloader=FakeDownloader())
        ctx = ToolExecutionContext()
        result = await tool.execute({}, ctx)
        assert result["count"] == 1
        assert result["active"][0]["item_name"] == "Test Show"
        assert result["active"][0]["priority"] == "high"
        assert result["active"][0]["progress"] == 50

    @pytest.mark.asyncio
    async def test_list_downloads_error_without_downloader(self) -> None:
        """list_downloads should error gracefully without downloader."""
        tool = ListDownloadsTool(downloader=None)
        ctx = ToolExecutionContext()
        result = await tool.execute({}, ctx)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_manage_downloads_pauses_matched_download(self) -> None:
        """manage_downloads should resolve natural filters and pause a target."""
        downloader = FakeDownloader()
        tool = ManageDownloadsTool(downloader=downloader)
        ctx = ToolExecutionContext()
        result = await tool.execute({
            "action": "pause",
            "filters": {"name": "Test Show", "status": "downloading"},
        }, ctx)
        assert result["status"] == "ok"
        assert result["updated_count"] == 1
        assert result["succeeded"][0]["status"] == "paused"

    @pytest.mark.asyncio
    async def test_manage_downloads_cancel_requires_confirmation(self) -> None:
        """manage_downloads should protect cancellation with confirmation."""
        tool = ManageDownloadsTool(downloader=FakeDownloader())
        ctx = ToolExecutionContext()
        result = await tool.execute({
            "action": "cancel",
            "filters": {"name": "Test Show"},
        }, ctx)
        assert result["confirmation_required"] is True
        assert result["matched_count"] == 1

    @pytest.mark.asyncio
    async def test_get_preferences_returns_summary(self) -> None:
        """get_preferences should return formatted preference summary."""
        tool = GetPreferencesTool(preference_manager=FakePreferenceManager())
        ctx = ToolExecutionContext()
        result = await tool.execute({}, ctx)
        assert "preferences" in result
        assert "Sci-Fi" in result["preferences"]

    @pytest.mark.asyncio
    async def test_get_preferences_error_without_pref_manager(self) -> None:
        """get_preferences should error gracefully without pref manager."""
        tool = GetPreferencesTool(preference_manager=None)
        ctx = ToolExecutionContext()
        result = await tool.execute({}, ctx)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_get_recent_activity_returns_recent(self) -> None:
        """get_recent_activity should return recent downloads list."""
        tool = GetRecentActivityTool(downloader=FakeDownloader())
        ctx = ToolExecutionContext()
        result = await tool.execute({"limit": 5}, ctx)
        assert result["count"] == 1
        assert result["recent"][0]["item_name"] == "Test Show"

    @pytest.mark.asyncio
    async def test_get_recent_activity_error_without_downloader(self) -> None:
        """get_recent_activity should error gracefully without downloader."""
        tool = GetRecentActivityTool(downloader=None)
        ctx = ToolExecutionContext()
        result = await tool.execute({}, ctx)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_registry_execute_wraps_tool(self) -> None:
        """Registry.execute should route to the declarative tool handler."""
        reg = ToolRegistry()
        reg.register_tool(ListDownloadsTool(downloader=FakeDownloader()))
        result = await reg.execute("list_downloads", {})
        assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_registry_execute_tool_not_found(self) -> None:
        """Registry.execute should return error for unknown tool."""
        reg = ToolRegistry()
        result = await reg.execute("nonexistent", {})
        assert "error" in result
