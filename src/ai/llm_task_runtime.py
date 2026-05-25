"""
LLM task runtime for LJS.

Resolves per-task LLM configuration (model, API base, API key,
generation options) from Settings and creates async completion
callables wrapped in a circuit breaker.
"""

from __future__ import annotations

from typing import Any

from src.ai.tool_registry import ToolRegistry
from src.core.models import Intent, Settings
from src.ai.token_budget import TokenBudgetManager
from src.llm_providers.context_limits import (
    FALLBACK_CONTEXT_LIMIT,
    MAX_MANUAL_CONTEXT_LIMIT,
    MIN_USER_CONTEXT_LIMIT,
)
from src.utils.circuit_breaker import CircuitBreaker


class LLMTaskRuntime:
    """LLM configuration resolution and completion function factory.

    Resolves per-task model routing through the Settings hierarchy,
    creates async completion callables wrapped in the circuit breaker,
    and resolves tool definitions filtered by intent.
    """

    def __init__(self, settings: Settings, llm_client: Any,
                 tool_registry: ToolRegistry) -> None:
        """Initialize the LLM task runtime.

        Args:
            settings: Application settings for LLM configuration.
            llm_client: The LLM client for making completion calls.
            tool_registry: Registry of available tools.
        """
        self._settings = settings
        self._llm_client = llm_client
        self._tool_registry = tool_registry
        self._llm_breaker = CircuitBreaker("llm", failure_threshold=5, recovery_seconds=60)
        self._token_budget = TokenBudgetManager()

    def update_settings(self, settings: Settings) -> None:
        """Hot-reload settings without recreating the runtime.

        Args:
            settings: The new application settings.
        """
        self._settings = settings

    def update_tool_registry(self, tool_registry: ToolRegistry) -> None:
        """Replace the tool registry reference.

        Args:
            tool_registry: The new tool registry.
        """
        self._tool_registry = tool_registry


    def context_budget_for_task(self, task: str) -> dict:
        """Return task-aware prompt budget settings.

        Uses model catalog context-window metadata when available, then applies
        user-configurable caps/percentages so local models or weaker endpoints
        are not overfilled.
        """
        llm = self._settings.llm
        endpoint_limit = None
        endpoint_source = "configured fallback"
        endpoint_reported = False
        if self._llm_client and hasattr(self._llm_client, "resolve_task"):
            try:
                resolved = self._llm_client.resolve_task(task)
                endpoint_limit = getattr(resolved, "context_limit", None)
                endpoint_source = str(getattr(resolved, "context_limit_source", "configured fallback") or "configured fallback")
                endpoint_reported = bool(getattr(resolved, "context_limit_reported", False))
            except Exception:
                endpoint_limit = None
                endpoint_source = "configured fallback"
                endpoint_reported = False
        endpoint_limit = int(endpoint_limit or FALLBACK_CONTEXT_LIMIT)

        user_cap = llm.get_context_tokens_for_task(task)
        # If the provider/model reported a real maximum, a user cap must never
        # exceed it.  If the runtime only has the app's conservative fallback,
        # the fallback is a default, not a hard endpoint limit: local endpoints
        # often omit context metadata even when they support much larger windows.
        manual_max = endpoint_limit if endpoint_reported else MAX_MANUAL_CONTEXT_LIMIT
        min_selectable = min(MIN_USER_CONTEXT_LIMIT, manual_max) if manual_max > 0 else MIN_USER_CONTEXT_LIMIT
        if user_cap is None:
            selected_limit = endpoint_limit
            context_cap_source = "endpoint" if endpoint_reported else "fallback_default"
        else:
            selected_limit = min(max(min_selectable, int(user_cap)), manual_max)
            context_cap_source = "user_cap_clamped_to_endpoint" if endpoint_reported else "user_cap_unverified_endpoint"

        pct = max(0, min(100, int(getattr(llm, "context_budget_percent", 85) or 85)))
        effective_limit = int(selected_limit * (pct / 100.0)) if selected_limit > 0 else 0
        reserved_output = (
            getattr(llm, "reserved_output_tokens", None)
            or llm.get_max_tokens_for_task(task)
            or 1024
        )
        # A model context window includes both prompt and response.  The prompt
        # budget is therefore the selected effective context minus the output
        # reserve.  History is split into raw recent context and compressed older
        # context; the default preserves 30% of the prompt budget as raw recent
        # turns and uses the rest for compressed history/category/tool context.
        available_prompt_tokens = max(0, effective_limit - int(reserved_output))
        raw_recent_percent = max(0, min(100, int(getattr(llm, "raw_recent_context_percent", 30) or 0)))
        conversation_tokens = 0 if available_prompt_tokens <= 0 else max(512, int(available_prompt_tokens * 0.45))
        raw_recent_conversation_tokens = 0 if conversation_tokens <= 0 else int(conversation_tokens * (raw_recent_percent / 100.0))
        compressed_history_tokens = 0 if conversation_tokens <= 0 else max(0, conversation_tokens - raw_recent_conversation_tokens)
        max_recent_turns = 0 if conversation_tokens <= 0 else int(getattr(llm, "max_recent_conversation_turns", 24) or 24)
        return {
            "endpoint_context_tokens": endpoint_limit,
            "endpoint_context_source": endpoint_source,
            "endpoint_context_reported": endpoint_reported,
            "manual_max_context_tokens": manual_max,
            "model_context_tokens": int(selected_limit),
            "context_cap_source": context_cap_source,
            "effective_context_tokens": effective_limit,
            "provider_call_context_tokens": effective_limit if effective_limit > 0 else endpoint_limit,
            "reserved_output_tokens": int(reserved_output),
            "available_prompt_tokens": available_prompt_tokens,
            "raw_recent_context_percent": raw_recent_percent,
            "conversation_tokens": conversation_tokens,
            "raw_recent_conversation_tokens": raw_recent_conversation_tokens,
            "compressed_history_tokens": compressed_history_tokens,
            "max_recent_turns": max_recent_turns,
        }

    def get_llm_config(self, task: str) -> dict:
        """Get the LLM configuration for a specific task.

        Resolves through per-task override -> tier default -> global default.

        Args:
            task: One of 'search', 'download', 'chat', 'intent_routing',
                'routing_fast', 'planning_strict', 'torrent_ranker',
                'tool_agent_reliable', 'final_response', 'research_web',
                'summarization', 'embedding', 'research'.

        Returns:
            Dict with 'model', 'api_base', 'api_key', and optionally
            'max_tokens', 'temperature' keys resolved for the task.
        """
        llm = self._settings.llm
        config = {
            "model": llm.get_model_for_task(task),
            "api_base": llm.get_api_base_for_task(task),
            "api_key": llm.get_api_key_for_task(task),
        }
        max_tokens = llm.get_max_tokens_for_task(task)
        if max_tokens is not None:
            config["max_tokens"] = max_tokens
        temperature = llm.get_temperature_for_task(task)
        if temperature is not None:
            config["temperature"] = temperature
        return config

    @staticmethod
    def get_generation_options(task_config: dict) -> dict:
        """Return optional generation parameters without None values.

        Args:
            task_config: Resolved task LLM configuration.

        Returns:
            Keyword args for optional generation controls.
        """
        return {
            key: task_config[key]
            for key in ("max_tokens", "temperature")
            if task_config.get(key) is not None
        }

    def make_completion_fn(self) -> object:
        """Create an LLM completion callable wrapped in the circuit breaker.

        Returns a callable with the LLMCompletionFn protocol signature
        using the injected LLM client. The circuit breaker protects
        against repeated failures.

        Returns:
            Async callable accepting task, messages, tools, and gen_options.
        """
        llm_client = self._llm_client
        llm_breaker = self._llm_breaker

        async def completion_fn(*, task, messages, tools, **gen_options):
            """Trim messages to the task budget and run one protected completion."""
            if llm_client and hasattr(llm_client, "ensure_model_metadata_for_task"):
                await llm_client.ensure_model_metadata_for_task(task)
            budget = self.context_budget_for_task(task)
            trimmed_messages = self._token_budget.trim_messages(
                messages,
                context_limit=budget.get("provider_call_context_tokens", budget["effective_context_tokens"]),
                reserved_output_tokens=budget["reserved_output_tokens"],
                raw_recent_context_percent=budget.get("raw_recent_context_percent", 30),
            )
            return await llm_breaker.call(
                llm_client.completion,
                task=task, messages=trimmed_messages, tools=tools, **gen_options,
            )
        return completion_fn

    def make_stream_completion_fn(self) -> object:
        """Create a streaming LLM completion callable wrapped in the circuit breaker.

        Returns a callable that returns an async iterable of streaming
        chunks when called with stream=True.

        Returns:
            Async callable that returns an async iterable of streaming chunks.
        """
        llm_client = self._llm_client
        llm_breaker = self._llm_breaker

        async def stream_completion_fn(*, task, messages, tools, **gen_options):
            """Trim messages to the task budget and run one protected stream."""
            if llm_client and hasattr(llm_client, "ensure_model_metadata_for_task"):
                await llm_client.ensure_model_metadata_for_task(task)
            budget = self.context_budget_for_task(task)
            trimmed_messages = self._token_budget.trim_messages(
                messages,
                context_limit=budget.get("provider_call_context_tokens", budget["effective_context_tokens"]),
                reserved_output_tokens=budget["reserved_output_tokens"],
                raw_recent_context_percent=budget.get("raw_recent_context_percent", 30),
            )
            return await llm_breaker.call(
                llm_client.completion,
                task=task, messages=trimmed_messages, tools=tools,
                stream=True, **gen_options,
            )
        return stream_completion_fn

    def get_tool_definitions_for_intent(self, intent: Intent) -> list[dict] | None:
        """Return only the tool definitions appropriate for an intent.

        Delegates to ToolRegistry.get_definitions_for_intent() which
        filters tools by their declared intent metadata. Tools with no
        intent metadata are not returned.

        Args:
            intent: The routed user intent.

        Returns:
            Tool definitions for tool-capable intents, or None for plain chat.
        """
        definitions = self._tool_registry.get_definitions_for_intent(intent)
        return definitions or None
