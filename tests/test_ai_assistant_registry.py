"""
Tests for AIAssistant registry replacement.

Verifies that set_tool_registry() rebuilds the ToolCallExecutor so
it uses the new registry, preventing stale-executor bugs.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.ai.assistant import AIAssistant, AgentDependencies
from src.ai.tool_registry import ToolRegistry
from src.core.models import Settings, LLMConfig


class TestAssistantRegistryReplacement:
    """AIAssistant.set_tool_registry() rebuilds the executor."""

    @pytest.mark.asyncio
    async def test_registry_replacement_rebuilds_executor(self):
        """Executor uses the new registry after replacement."""
        settings = Settings(llm=LLMConfig(), tracked_items__items=[])
        prefs = MagicMock()
        prefs.get_summary = AsyncMock(return_value="")
        deps = AgentDependencies(settings=settings, preference_manager=prefs)
        assistant = AIAssistant(deps)

        # Capture the original executor
        old_executor = assistant._tool_executor

        # Build a new registry with a test tool
        new_registry = ToolRegistry()
        test_handler = AsyncMock(return_value={"result": "from_new_registry"})
        new_registry.register(
            name="test_tool",
            description="A test tool",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=test_handler,
        )

        # Replace the registry
        assistant.set_tool_registry(new_registry)

        # The executor should be a new instance pointing at the new registry
        assert assistant._tool_executor is not old_executor

        # Executing through the new executor should reach the test handler
        result = await assistant._tool_executor.execute_direct_call("test_tool", {})
        assert result == {"result": "from_new_registry"}
        test_handler.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_old_registry_not_used_after_replacement(self):
        """Executor does not fall back to the old registry."""
        settings = Settings(llm=LLMConfig(), tracked_items__items=[])
        prefs = MagicMock()
        prefs.get_summary = AsyncMock(return_value="")
        deps = AgentDependencies(settings=settings, preference_manager=prefs)
        assistant = AIAssistant(deps)

        old_registry = assistant._tool_registry

        # Register a tool in the old registry (simulating prior state)
        old_handler = AsyncMock(return_value={"from": "old"})
        old_registry.register(
            name="stale_tool",
            description="Old tool",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=old_handler,
        )

        # Replace with a fresh empty registry
        new_registry = ToolRegistry()
        assistant.set_tool_registry(new_registry)

        # The old tool should NOT be reachable through the new executor
        result = await assistant._tool_executor.execute_direct_call("stale_tool", {})
        assert isinstance(result, dict)
        assert "error" in result
        assert "not found" in result["error"].lower()
