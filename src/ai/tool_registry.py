"""
Tool registry for LJS.

Central registry for agentic tools that the AI can call.
Each tool registers its definition and async handler.
Supports both legacy imperative tools (via register) and
declarative AgentTool protocol objects (via register_tool).
"""

import json
from loguru import logger
from typing import Callable, Any

from src.core.models import Intent, SearchResult
from src.ai.tools.base import AgentTool
from src.core.models import ToolExecutionContext


class ToolDefinition:
    """A single tool definition with its handler and optional intent scoping.

    Carries action metadata for the unified action pipeline:
    - allow_direct: can be invoked directly from UI (no LLM needed).
    - requires_confirmation: should prompt user before executing.
    - destructive: can delete or remove data.
    """

    def __init__(self, name: str, description: str,
                 parameters: dict, handler: Callable,
                 intents: set[Intent] | None = None,
                 allow_direct: bool = False,
                 requires_confirmation: bool = False,
                 destructive: bool = False):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.handler = handler
        self.intents = intents
        self.allow_direct = allow_direct
        self.requires_confirmation = requires_confirmation
        self.destructive = destructive

    def to_openai_format(self) -> dict:
        """Convert to OpenAI function-calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            }
        }


class ToolRegistry:
    """Registry and executor for AI tools.

    Supports two registration modes:
    - register(): legacy imperative registration (no intent filtering).
    - register_tool(): declarative AgentTool registration with intent metadata.
    """

    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, name: str, description: str,
                 parameters: dict, handler: Callable,
                 intents: set[Intent] | None = None,
                 allow_direct: bool = False,
                 requires_confirmation: bool = False,
                 destructive: bool = False) -> None:
        """Register a tool with its OpenAI-format definition and handler.

        Args:
            name: Tool name (unique identifier).
            description: Natural-language description of what the tool does.
            parameters: JSON Schema for the tool's arguments.
            handler: Async callable that accepts **kwargs matching parameters.
            intents: Optional set of intents this tool is available for.
                     Tools without intents are not returned by
                     get_definitions_for_intent().
            allow_direct: Whether the tool can be invoked directly from UI.
            requires_confirmation: Whether the tool needs user confirmation.
            destructive: Whether the tool can delete or remove data.
        """
        self._tools[name] = ToolDefinition(
            name=name, description=description,
            parameters=parameters, handler=handler,
            intents=intents,
            allow_direct=allow_direct,
            requires_confirmation=requires_confirmation,
            destructive=destructive,
        )
        logger.trace(f"Registered tool: {name}")

    def register_tool(self, tool: AgentTool) -> None:
        """Register a declarative AgentTool object.

        Extracts name, description, parameters, intents, and action metadata
        from the tool implementation. Compatibility shims marked
        ``compatibility_only`` are skipped unless explicitly opted in so legacy
        aliases cannot silently return to the active LLM surface.

        Args:
            tool: An AgentTool-compatible declarative tool instance.
        """
        if self._should_skip_compatibility_tool(tool):
            return

        async def _handler(**kwargs: Any) -> Any:
            ctx = ToolExecutionContext()
            return await tool.execute(kwargs, ctx)

        self._tools[tool.name] = ToolDefinition(
            name=tool.name,
            description=tool.description,
            parameters=tool.parameters(),
            handler=_handler,
            intents=tool.intents,
            allow_direct=tool.allow_direct,
            requires_confirmation=tool.requires_confirmation,
            destructive=tool.destructive,
        )
        logger.trace(f"Registered declarative tool: {tool.name}")

    def _should_skip_compatibility_tool(self, tool: AgentTool) -> bool:
        """Return whether a compatibility-only tool should be skipped."""
        if not getattr(tool, "compatibility_only", False):
            return False
        if getattr(tool, "register_compatibility_tool", False):
            return False
        tool_name = getattr(tool, "name", "<unknown>")
        replacement = getattr(tool, "replacement_tool_name", "category-owned tools")
        logger.warning(
            f"Skipped compatibility-only tool '{tool_name}'; use replacement '{replacement}' instead."
        )
        return True

    def get_definitions(self, names: set[str] | None = None) -> list[dict]:
        """Return selected tool definitions in OpenAI format.

        Args:
            names: Optional allow-list of tool names to expose to the model.

        Returns:
            OpenAI-compatible function tool definitions for registered tools.
        """
        if names is None:
            return [t.to_openai_format() for t in self._tools.values()]
        return [t.to_openai_format() for name, t in self._tools.items() if name in names]

    def get_definitions_for_intent(self, intent: Intent) -> list[dict]:
        """Return tool definitions for tools scoped to the given intent.

        Only returns tools that have an explicit intents set containing
        the requested intent. Tools registered without intents (legacy)
        are not returned — they must be accessed via get_definitions(names).

        Args:
            intent: The user intent to filter tools by.

        Returns:
            OpenAI-compatible function tool definitions for matching tools.
        """
        return [
            t.to_openai_format()
            for t in self._tools.values()
            if t.intents is not None and intent in t.intents
        ]

    def get_tool_names_for_intent(self, intent: Intent) -> set[str]:
        """Return tool names for tools scoped to the given intent.

        Args:
            intent: The user intent to filter tool names by.

        Returns:
            Set of tool names available for this intent.
        """
        return {
            name for name, t in self._tools.items()
            if t.intents is not None and intent in t.intents
        }

    def get_tool_names(self) -> list[str]:
        """Return the names of all registered tools."""
        return list(self._tools.keys())

    def get_tool_definition(self, name: str) -> ToolDefinition | None:
        """Return a registered tool definition by name, if present."""
        return self._tools.get(name)

    def has_tool(self, name: str) -> bool:
        """Return whether a tool name is registered."""
        return name in self._tools

    async def execute(self, name: str, arguments: dict) -> Any:
        """Execute a tool by name with the given arguments."""
        tool = self._tools.get(name)
        if not tool:
            return {"error": f"Tool '{name}' not found."}

        try:
            result = await tool.handler(**arguments)
            return result
        except Exception as e:
            logger.error(f"Tool '{name}' execution error: {e}")
            return {"error": str(e)}


