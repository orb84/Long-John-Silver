"""
Tool call executor for LJS.

Validates, parses, executes, and formats tool call results
for the agentic loop, isolated from prompt construction
and streaming concerns.

Used by both AgentLoopExecutor and StreamingAgentLoopExecutor
to ensure consistent tool execution behavior.
"""

import json
from typing import Any

from loguru import logger

from src.ai.tool_registry import ToolRegistry
from src.ai.tool_result_compactor import ToolResultCompactor
from src.ai.tool_contracts import ToolContractValidator
from src.core.models import ToolExecutionContext


_TOOL_NAME_ALIASES = {
    # Historical/LLM-drift alias kept out of tool definitions but accepted at
    # execution time when the canonical tool is allowed for the current intent.
    "find_browser_links": "browser_find_links",
}


class ToolCallExecutor:
    """Validates, executes, and formats tool calls.

    Isolated from prompt construction and streaming concerns.
    Used by both AgentLoopExecutor and StreamingAgentLoopExecutor
    to ensure consistent tool execution behavior.
    """

    def __init__(self, tool_registry: ToolRegistry) -> None:
        """Initialize with a tool registry.

        Args:
            tool_registry: Registry containing all registered tools.
        """
        self._tool_registry = tool_registry
        self._result_compactor = ToolResultCompactor()
        self._contract_validator = ToolContractValidator()

    def get_definitions(self, allowed_tool_names: set[str]) -> list[dict]:
        """Return OpenAI-format definitions for the allowed tool names."""
        return self._tool_registry.get_definitions(allowed_tool_names)

    async def execute_direct_call(self, name: str, arguments: dict) -> Any:
        """Execute a tool call directly without LLM-specific processing.

        Skips JSON parsing, intent-based name filtering, and result
        message formatting. Used by ActionGateway for UI-originated
        tool invocations so they share the exact same execution path
        as LLM-originated calls.

        Args:
            name: Tool name to execute.
            arguments: Keyword arguments dict (already parsed).

        Returns:
            Raw tool result (dict, list, str, etc.).
        """
        return await self._tool_registry.execute(name, arguments)

    async def execute_tool_call(
        self,
        name: str,
        arguments_raw: str | dict,
        tool_call_id: str,
        allowed_tool_names: set[str],
        tool_context: ToolExecutionContext | None = None,
    ) -> tuple[dict, str]:
        """Validate, parse, execute a tool call and return result artifacts.

        Args:
            name: Tool name requested by the model.
            arguments_raw: Raw JSON string or already-parsed dict of arguments.
            tool_call_id: The tool call ID from the LLM response.
            allowed_tool_names: Intent-specific tool allow-list.

        Returns:
            A tuple of (tool_result_message, result_summary_string).
        """
        # Step 1: Parse arguments
        if isinstance(arguments_raw, str):
            try:
                function_args = json.loads(arguments_raw)
            except json.JSONDecodeError:
                function_args = {}
        else:
            function_args = arguments_raw

        # Step 2: Validate tool is allowed for this intent.
        # Some models occasionally call a historical alias even when the prompt
        # exposes the canonical name.  Resolve the alias only if the canonical
        # tool is allowed; otherwise preserve the safety block.
        requested_name = name
        executable_name = _TOOL_NAME_ALIASES.get(name, name)
        if executable_name not in allowed_tool_names:
            logger.warning(f"Blocked tool '{requested_name}' for current intent")
            result = {
                "ok": False,
                "error_code": "TOOL_NOT_ALLOWED",
                "recoverable": True,
                "error": f"Tool '{requested_name}' is not available for this request.",
                "available_tools": sorted(allowed_tool_names),
            }
        else:
            if executable_name != requested_name:
                logger.info(f"Resolved tool alias '{requested_name}' -> '{executable_name}'")
            definition = self._tool_registry.get_tool_definition(executable_name)
            schema = definition.parameters if definition else None
            validation = self._contract_validator.validate(
                tool_name=executable_name,
                arguments=function_args,
                schema=schema,
            )
            if not validation.ok:
                logger.warning(
                    "Blocked invalid tool call {}: {}",
                    executable_name,
                    validation.message,
                )
                result = validation.error_payload(executable_name)
            else:
                # Step 3: Execute via registry using schema-normalized arguments.
                result = await self._tool_registry.execute(
                    executable_name, validation.arguments, context=tool_context,
                )

        # Step 4: Build a compact tool result message for LLM context.
        # Raw tool results can contain dozens of torrent candidates, full web
        # pages, or operational payloads. Stable IDs and queue arguments are
        # preserved, while bulky fields remain in caches/logs instead of being
        # replayed into every following model call.
        compact_content = self._result_compactor.compact_for_message(executable_name, result)
        result_message = {
            "tool_call_id": tool_call_id,
            "role": "tool",
            "name": executable_name,
            "content": compact_content,
        }

        # Step 5: Build compact result summary for loop state tracking
        result_str = compact_content
        if len(result_str) > 200:
            result_summary = f"{executable_name}: {result_str[:200]}..."
        else:
            result_summary = f"{executable_name}: {result_str}"

        return result_message, result_summary
