"""
LLM task runtime for LJS.

Resolves per-task LLM configuration (model, API base, API key,
generation options) from Settings and creates async completion
callables wrapped in a circuit breaker.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from src.ai.tool_registry import ToolRegistry
from src.core.models import Intent, Settings
from src.ai.token_budget import TokenBudgetManager
from src.llm_providers.context_limits import (
    FALLBACK_CONTEXT_LIMIT,
    MAX_MANUAL_CONTEXT_LIMIT,
    MIN_USER_CONTEXT_LIMIT,
)
from src.llm_providers.activity import LLMActivityContext
from src.utils.circuit_breaker import CircuitBreaker


class LLMPayloadBudgetError(RuntimeError):
    """Raised before provider I/O when the measured payload cannot fit."""


class LLMTaskRuntime:
    """LLM configuration resolution and completion function factory.

    Resolves per-task model routing through the Settings hierarchy,
    creates async completion callables wrapped in the circuit breaker,
    and resolves tool definitions filtered by intent.
    """

    # Automatic interactive limits are **soft assembly targets**, not provider
    # or user context-window caps.  They keep routine turns compact while the
    # provider/user-selected ceiling remains available when irreducible system
    # instructions and tool schemas legitimately need more room.
    _AUTO_CONTEXT_TARGETS = {
        "intent_routing": 8192,
        "routing_fast": 8192,
        "progress_message": 8192,
        "chat": 32768,
        "search": 32768,
        "download": 32768,
        "tool_agent_reliable": 32768,
        "planning_strict": 24576,
        "torrent_ranker": 24576,
        "final_response": 24576,
        "research_web": 49152,
    }

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

    async def ensure_context_metadata_for_task(self, task: str, *, force_refresh: bool = False) -> None:
        """Warm provider/model metadata before building prompt history budgets.

        The completion wrapper already warms metadata immediately before a call,
        but the assistant builds conversation/category context earlier.  Without
        this pre-warm, the first turn after a model/provider/context-settings
        change can size history against the 16k fallback even when the actual
        call later uses a larger endpoint/user cap.
        """
        if self._llm_client and hasattr(self._llm_client, "ensure_model_metadata_for_task"):
            await self._llm_client.ensure_model_metadata_for_task(task, force_refresh=force_refresh)


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

        # The UI constrains this value to 20..100.  Clamp configuration-file or
        # legacy values to the same usable range so 0 cannot accidentally mean
        # "fall back to the full endpoint" or produce a non-functional budget.
        configured_pct = getattr(llm, "context_budget_percent", None)
        pct = max(20, min(100, int(85 if configured_pct is None else configured_pct)))
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
        provider_call_context_tokens = max(0, effective_limit)
        auto_target = int(self._AUTO_CONTEXT_TARGETS.get(str(task or ""), 32768))
        target_context_tokens = min(provider_call_context_tokens, auto_target) if provider_call_context_tokens > 0 else 0
        context_target_source = (
            "task_auto_target"
            if target_context_tokens and target_context_tokens < provider_call_context_tokens
            else "selected_context_limit"
        )
        available_prompt_tokens = max(0, target_context_tokens - int(reserved_output))
        hard_available_prompt_tokens = max(0, provider_call_context_tokens - int(reserved_output))
        raw_recent_percent = max(0, min(100, int(getattr(llm, "raw_recent_context_percent", 30) or 0)))
        conversation_tokens = 0 if available_prompt_tokens <= 0 else max(512, int(available_prompt_tokens * 0.45))
        raw_recent_conversation_tokens = 0 if conversation_tokens <= 0 else int(conversation_tokens * (raw_recent_percent / 100.0))
        compressed_history_tokens = 0 if conversation_tokens <= 0 else max(0, conversation_tokens - raw_recent_conversation_tokens)
        max_recent_turns = 0 if conversation_tokens <= 0 else int(getattr(llm, "max_recent_conversation_turns", 24) or 24)
        budget = {
            "endpoint_context_tokens": endpoint_limit,
            "endpoint_context_source": endpoint_source,
            "endpoint_context_reported": endpoint_reported,
            "manual_max_context_tokens": manual_max,
            "model_context_tokens": int(selected_limit),
            "context_cap_source": context_cap_source,
            "effective_context_tokens": effective_limit,
            "provider_call_context_tokens": provider_call_context_tokens,
            "target_context_tokens": target_context_tokens,
            "context_target_source": context_target_source,
            "reserved_output_tokens": int(reserved_output),
            "available_prompt_tokens": available_prompt_tokens,
            "hard_available_prompt_tokens": hard_available_prompt_tokens,
            "raw_recent_context_percent": raw_recent_percent,
            "conversation_tokens": conversation_tokens,
            "raw_recent_conversation_tokens": raw_recent_conversation_tokens,
            "compressed_history_tokens": compressed_history_tokens,
            "max_recent_turns": max_recent_turns,
        }
        logger.debug(
            "LLM context budget: task={} model_context={} effective={} target={} target_prompt={} hard_prompt={} "
            "cap_source={} target_source={} endpoint_reported={} user_cap={} reserved_output={}",
            task,
            budget["model_context_tokens"],
            budget["effective_context_tokens"],
            budget["target_context_tokens"],
            budget["available_prompt_tokens"],
            budget["hard_available_prompt_tokens"],
            budget["context_cap_source"],
            budget["context_target_source"],
            budget["endpoint_context_reported"],
            user_cap if user_cap is not None else "endpoint_default",
            budget["reserved_output_tokens"],
        )
        return budget

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
                context_limit=budget.get("target_context_tokens", budget["effective_context_tokens"]),
                reserved_output_tokens=budget["reserved_output_tokens"],
                raw_recent_context_percent=budget.get("raw_recent_context_percent", 30),
                tools=tools,
                allow_target_overflow=True,
            )
            payload_audit = self._audit_payload_budget(task, trimmed_messages, tools, budget)
            with LLMActivityContext.bind_budget(self._activity_budget_payload(budget, payload_audit)):
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
                context_limit=budget.get("target_context_tokens", budget["effective_context_tokens"]),
                reserved_output_tokens=budget["reserved_output_tokens"],
                raw_recent_context_percent=budget.get("raw_recent_context_percent", 30),
                tools=tools,
                allow_target_overflow=True,
            )
            payload_audit = self._audit_payload_budget(task, trimmed_messages, tools, budget)
            with LLMActivityContext.bind_budget(self._activity_budget_payload(budget, payload_audit)):
                return await llm_breaker.call(
                    llm_client.completion,
                    task=task, messages=trimmed_messages, tools=tools,
                    stream=True, **gen_options,
                )
        return stream_completion_fn


    def _audit_payload_budget(
        self,
        task: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        budget: dict[str, Any],
    ) -> dict[str, Any]:
        """Log the payload actually handed to the provider, including schemas.

        The old diagnostic reported the selected context allowance as though it
        described the sent prompt, and ignored function schemas entirely.  This
        audit distinguishes the configured ceiling from the measured request so
        a 100k-token runaway is immediately visible and attributable.
        """
        message_tokens = self._token_budget.estimate_messages(messages)
        tool_tokens = self._token_budget.estimate_tools(tools)
        prompt_tokens = message_tokens + tool_tokens
        reserve = int(budget.get("reserved_output_tokens") or 0)
        provider_limit = int(
            budget.get("provider_call_context_tokens")
            or budget.get("effective_context_tokens")
            or budget.get("model_context_tokens")
            or 0
        )
        total_with_output = prompt_tokens + reserve
        target_limit = int(budget.get("target_context_tokens") or provider_limit or 0)
        over_target = bool(target_limit and total_with_output > target_limit)
        over_limit = bool(provider_limit and total_with_output > provider_limit)
        log = logger.error if over_limit else (logger.warning if over_target else logger.info)
        log(
            "LLM_PAYLOAD_BUDGET task={} messages={} tools={} message_tokens_est={} "
            "tool_tokens_est={} prompt_tokens_est={} output_reserve={} total_est={} "
            "target_limit={} provider_limit={} cap_source={} target_source={} "
            "over_target={} over_limit={}",
            task,
            len(messages or []),
            len(tools or []),
            message_tokens,
            tool_tokens,
            prompt_tokens,
            reserve,
            total_with_output,
            target_limit,
            provider_limit,
            budget.get("context_cap_source"),
            budget.get("context_target_source"),
            over_target,
            over_limit,
        )
        audit = {
            "message_tokens_estimated": message_tokens,
            "tool_tokens_estimated": tool_tokens,
            "prompt_tokens_estimated": prompt_tokens,
            "output_reserve_tokens": reserve,
            "total_tokens_estimated": total_with_output,
            "target_context_tokens": target_limit,
            "provider_context_tokens": provider_limit,
            "over_target": over_target,
            "over_hard_limit": over_limit,
        }
        if over_limit:
            error = LLMPayloadBudgetError(
                "The measured LLM request cannot fit the selected context window "
                f"({total_with_output} estimated tokens including output reserve vs "
                f"{provider_limit}). Tool schemas account for {tool_tokens} tokens. "
                "The request was stopped before provider I/O."
            )
            self._record_budget_rejection(task, messages, tools, budget, audit, error)
            raise error
        return audit

    @staticmethod
    def _activity_budget_payload(budget: dict[str, Any], audit: dict[str, Any]) -> dict[str, Any]:
        """Return the compact budget contract shown in the activity inspector."""
        keys = (
            "endpoint_context_tokens",
            "endpoint_context_source",
            "endpoint_context_reported",
            "model_context_tokens",
            "context_cap_source",
            "effective_context_tokens",
            "provider_call_context_tokens",
            "target_context_tokens",
            "context_target_source",
            "reserved_output_tokens",
            "available_prompt_tokens",
            "hard_available_prompt_tokens",
        )
        payload = {key: budget.get(key) for key in keys}
        payload["payload"] = dict(audit)
        return payload

    def _record_budget_rejection(
        self,
        task: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        budget: dict[str, Any],
        audit: dict[str, Any],
        error: BaseException,
    ) -> None:
        """Expose pre-provider budget failures through the normal activity UI."""
        monitor = getattr(self._llm_client, "activity_monitor", None)
        if monitor is None:
            return
        provider = "default"
        model = ""
        try:
            resolved = self._llm_client.resolve_task(task)
            provider = str(getattr(resolved, "provider_id", None) or "default")
            model = str(getattr(resolved, "model", None) or "")
        except Exception:
            pass
        call_id = monitor.start_call(
            task=task,
            provider=provider,
            model=model,
            messages=messages,
            tools=tools,
            stream=False,
            generation={"pre_provider_rejection": True},
            budget=self._activity_budget_payload(budget, audit),
        )
        monitor.finish_call(call_id, status="failed", error=error)

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
