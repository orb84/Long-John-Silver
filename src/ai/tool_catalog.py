"""
Tool catalog for LJS.

Builds the default assistant tool registry using domain-specific
ToolProviders. Each provider is instantiated with its dependencies
in main.py and passed to AgentToolCatalog which registers all tools.
"""

from typing import Any

from loguru import logger

from src.ai.tool_registry import ToolRegistry


class AgentToolCatalog:
    """Builds the default assistant tool registry from ToolProviders.

    Accepts a list of ToolProvider instances. Each provider's get_tools()
    method returns AgentTool instances that are registered with the registry.
    """

    def __init__(self, providers: list | None = None) -> None:
        """Initialize with a list of ToolProviders.

        Args:
            providers: List of ToolProvider instances. Each must have a
                get_tools() method returning a list of AgentTool instances.
        """
        self._providers = providers or []

    def build_registry(self) -> ToolRegistry:
        """Build and return a fully populated ToolRegistry.

        Iterates through all ToolProviders, collects their AgentTool
        instances via get_tools(), and registers each one.

        Returns:
            A ToolRegistry with all default tools registered.
        """
        registry = ToolRegistry()
        for provider in self._providers:
            for tool in provider.get_tools():
                registry.register_tool(tool)

        logger.info(f"Built tool registry with {len(registry.get_tool_names())} tools")
        return registry
