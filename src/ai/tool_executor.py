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
    # Historical/LLM-drift aliases kept out of tool definitions but accepted at
    # execution time when the canonical tool is allowed for the current intent.
    "find_browser_links": "browser_find_links",
    "WebSearch": "web_search",
    "webSearch": "web_search",
    "SearchWeb": "web_search",
    "WebResearch": "web_research",
    "ResearchWeb": "web_research",
    "MetadataLookup": "metadata_lookup",
    "TMDBLookup": "metadata_lookup",
    "ExtractMetadata": "browser_extract",
    "ReadWebPage": "read_web_page",
}

_READ_ONLY_RETRYABLE_TOOLS = {
    "web_search",
    "web_research",
    "read_web_page",
    "browse_page",
    "browser_open",
    "browser_read_selected",
    "browser_find_links",
    "browser_evidence_report",
    "browser_extract",
    "metadata_lookup",
    "enquire_about_media",
    "get_library_status",
    "list_library_files",
    "list_downloads",
    "inspect_torrent_candidate",
    "search_torrents",
    "search_media_torrents",
    "search_soulseek",
}
_MAX_TOOL_EXECUTION_ATTEMPTS = 2


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

    async def _execute_with_bounded_retry(
        self,
        executable_name: str,
        arguments: dict[str, Any],
        *,
        tool_context: ToolExecutionContext | None,
    ) -> Any:
        """Execute a tool with a small retry budget for safe read/search tools.

        Retries are intentionally limited to read/search style tools to avoid
        duplicating side effects such as queueing downloads, deleting rows, or
        changing settings. Validation failures are not retried here; the LLM
        receives the typed error and may choose corrected arguments in the next
        loop iteration.
        """
        attempts = _MAX_TOOL_EXECUTION_ATTEMPTS if executable_name in _READ_ONLY_RETRYABLE_TOOLS else 1
        last_result: Any = None
        for attempt in range(1, attempts + 1):
            result = await self._tool_registry.execute(
                executable_name, arguments, context=tool_context,
            )
            last_result = result
            if attempt >= attempts or not self._is_retryable_tool_result(result):
                return result
            logger.warning(
                "Retrying read-only tool '{}' after recoverable result on attempt {}/{}: {}",
                executable_name,
                attempt,
                attempts,
                result.get("error_code") or result.get("error"),
            )
        return last_result

    @staticmethod
    def _is_retryable_tool_result(result: Any) -> bool:
        if not isinstance(result, dict):
            return False
        if result.get("ok") is not False and not result.get("error"):
            return False
        if result.get("recoverable") is False:
            return False
        code = str(result.get("error_code") or "").upper()
        if any(token in code for token in ("TIMEOUT", "UNREACHABLE", "TEMPORARY", "HTTP_ERROR", "RATE_LIMIT", "NETWORK")):
            return True
        error_text = str(result.get("error") or "").casefold()
        return any(token in error_text for token in ("timeout", "temporar", "unreachable", "connection", "network"))

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
                result = await self._execute_with_bounded_retry(
                    executable_name, validation.arguments, tool_context=tool_context,
                )

        if isinstance(result, dict) and (result.get("ok") is False or result.get("error")):
            result.setdefault("agent_instruction", "This tool failure is recoverable evidence. Do not stop unless all sensible available sources have been tried or the user must clarify.")

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
