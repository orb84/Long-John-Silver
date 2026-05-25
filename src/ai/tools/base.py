"""
Tool base abstractions for LJS.

Defines the AgentTool protocol that all declarative tools implement,
and ToolExecutionContext for runtime execution context.
"""

from typing import Any, Protocol, runtime_checkable

from src.core.models import Intent, ToolExecutionContext


@runtime_checkable
class AgentTool(Protocol):
    """Protocol for declarative agentic tools.

    Each tool declares its own name, description, intents, parameters,
    and execute method. Tools are self-documenting and can be registered
    directly with ToolRegistry without separate definition objects.
    """

    name: str
    description: str
    intents: set[Intent]
    allow_direct: bool
    requires_confirmation: bool
    destructive: bool
    required_dependencies: list[str]

    def parameters(self) -> dict:
        """Return the JSON Schema for this tool's arguments.

        Returns:
            OpenAI-compatible parameters dict.
        """
        ...

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Execute the tool with the given arguments and context.

        Args:
            arguments: Parsed JSON arguments matching parameters() schema.
            context: Execution context (user, session, source).

        Returns:
            Tool result (dict, list, str, etc.).
        """
        ...
