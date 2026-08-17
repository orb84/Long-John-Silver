"""LLM task routing and provider configuration models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_serializer, model_validator


# --- LLM config ---


class TaskModelConfig(BaseModel):
    """Model configuration for a specific task type.

    Allows routing different tasks to different LLM models/endpoints.
    For example, summarization can use a cheaper/faster model while
    complex research uses a more capable one. All fields fall back to
    the default LLMConfig values if not specified.
    """

    model: Optional[str] = None
    api_base: Optional[str] = None
    api_key: Optional[str] = None
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    provider: Optional[str] = None
    max_context_tokens: Optional[int] = None

    def has_values(self) -> bool:
        """Check if any field in this config is explicitly set."""
        return any(v is not None for v in (
            self.model, self.api_base, self.api_key,
            self.max_tokens, self.temperature, self.provider, self.max_context_tokens,
        ))


# LLM task tier definitions: each tier maps tasks to capability requirements.
# Tasks inherit their tier's model config unless explicitly overridden in LLMConfig.
LLMTaskTier = {
    "lightweight": {
        "description": (
            "Simple classification, name matching, summarization, short-output tasks. "
            "A small/cheap model is sufficient: intent routing, conversation compression, "
            "and short JSON responses like quality index selection. "
            "Embedding is explicitly excluded — it requires a dedicated embedding model."
        ),
        "tasks": ["summarization", "intent_routing", "routing_fast", "planning_strict", "taste_extraction"],
    },
    "standard": {
        "description": (
            "General conversation, search with tools, moderate reasoning. "
            "A mid-range model with tool calling support: chat interactions, "
            "show information lookup, and basic multi-step queries."
        ),
        "tasks": ["chat", "search", "final_response"],
    },
    "heavy": {
        "description": (
            "Complex multi-step reasoning, nuanced quality judgment across many "
            "candidates, research requiring cross-referencing multiple data sources. "
            "The most capable model available: deep show analysis, multi-provider "
            "search with quality evaluation, and recommendation reasoning."
        ),
        "tasks": ["research", "download", "tool_agent_reliable", "torrent_ranker", "research_web"],
    },
}


class LLMConfig(BaseModel):
    """Configuration for the LLM provider.

    Supports two levels of model routing:
    1. Tier-based: Set a model for all tasks in a tier (lightweight/standard/heavy).
       Tasks with no explicit override inherit from their tier.
    2. Per-task: Override any individual task (summarization, search, etc.)
       with a specific model/endpoint. Per-task overrides take priority over tiers.

    Resolution order: per-task override -> tier default -> global default.
    """
    model: str = "gpt-3.5-turbo"
    api_base: Optional[str] = None
    api_key: Optional[str] = None
    active_provider: str = "openrouter"
    max_tool_iterations: int = 4
    search_tool_iterations: int = 10
    chat_tool_iterations: int = 4
    intent_routing_model: Optional[str] = None
    # Prompt/context budgeting. max_context_tokens is an optional user cap;
    # if unset, the provider/model catalog context window is used when known.
    max_context_tokens: Optional[int] = None
    context_budget_percent: int = 85
    reserved_output_tokens: Optional[int] = None
    # Share of the conversation-history budget kept as raw recent turns.
    # Older context is compressed into the remaining history budget.
    raw_recent_context_percent: int = 30
    max_recent_conversation_turns: int = 24
    auto_compress_context: bool = True
    conversation_summary_max_tokens: int = 500
    # Tier defaults: set one model for all tasks in a capability tier.
    # Falls back to the global model/api_base/api_key if not set.
    lightweight: TaskModelConfig = Field(default_factory=TaskModelConfig)
    standard: TaskModelConfig = Field(default_factory=TaskModelConfig)
    heavy: TaskModelConfig = Field(default_factory=TaskModelConfig)
    # Per-task overrides: if set, takes priority over tier default.
    summarization: TaskModelConfig = Field(default_factory=TaskModelConfig)
    intent_routing: TaskModelConfig = Field(default_factory=TaskModelConfig)
    routing_fast: TaskModelConfig = Field(default_factory=TaskModelConfig)
    planning_strict: TaskModelConfig = Field(default_factory=TaskModelConfig)
    search: TaskModelConfig = Field(default_factory=TaskModelConfig)
    download: TaskModelConfig = Field(default_factory=TaskModelConfig)
    torrent_ranker: TaskModelConfig = Field(default_factory=TaskModelConfig)
    tool_agent_reliable: TaskModelConfig = Field(default_factory=TaskModelConfig)
    final_response: TaskModelConfig = Field(default_factory=TaskModelConfig)
    research_web: TaskModelConfig = Field(default_factory=TaskModelConfig)
    chat: TaskModelConfig = Field(default_factory=TaskModelConfig)
    embedding: TaskModelConfig = Field(default_factory=TaskModelConfig)
    research: TaskModelConfig = Field(default_factory=TaskModelConfig)
    taste_extraction: TaskModelConfig = Field(default_factory=TaskModelConfig)

    def tier_for_task(self, task: str) -> str | None:
        """Return the public capability tier for a task, when one exists."""
        return self._tier_for_task(task)

    def _tier_for_task(self, task: str) -> str | None:
        """Look up which tier a task belongs to.

        Embedding is never mapped to a tier — it requires an explicit
        per-task model config, otherwise hash fallback is used. This
        prevents accidentally using a chat model for embeddings.

        Args:
            task: Task name (e.g., 'summarization', 'search', 'research').

        Returns:
            Tier name ('lightweight', 'standard', 'heavy') or None.
        """
        # Embedding must never inherit a tier's chat model
        if task == "embedding":
            return None
        for tier_name, tier_def in LLMTaskTier.items():
            if task in tier_def["tasks"]:
                return tier_name
        return None

    def resolve_config(self, task: str) -> TaskModelConfig:
        """Return the effective config for a task through the priority chain.

        Public seam for runtime code.  The underscored implementation remains
        for backward compatibility with older internal callers.
        """
        return self._resolve_config(task)

    def _resolve_config(self, task: str) -> TaskModelConfig:
        """Resolve task/tier values field by field.

        A per-task object that sets only ``max_tokens`` must not erase the
        tier's model/provider. The previous all-or-nothing selection made the
        documented fallback contract untrue and hid which model actually won.

        Global values remain the final fallback in ``TaskLLMClient`` because
        their field names are not identical (for example ``active_provider``).
        """
        tier_name = self._tier_for_task(task)
        tier_config = getattr(self, tier_name, None) if tier_name else None
        per_task = getattr(self, task, None)
        values: dict[str, Any] = {}
        for field_name in TaskModelConfig.model_fields:
            task_value = getattr(per_task, field_name, None) if per_task else None
            tier_value = getattr(tier_config, field_name, None) if tier_config else None
            values[field_name] = task_value if task_value is not None else tier_value
        return TaskModelConfig(**values)

    def route_source(self, task: str, field_name: str) -> str:
        """Return the configuration layer that owns one effective route field."""
        if field_name not in TaskModelConfig.model_fields:
            raise ValueError(f"Unknown LLM route field: {field_name}")
        per_task = getattr(self, task, None)
        if per_task and getattr(per_task, field_name, None) is not None:
            return f"task:{task}"
        tier_name = self._tier_for_task(task)
        tier_config = getattr(self, tier_name, None) if tier_name else None
        if tier_config and getattr(tier_config, field_name, None) is not None:
            return f"tier:{tier_name}"
        return "global"

    def routing_tasks(self) -> list[str]:
        """Return every configured task route in stable UI order."""
        tasks: list[str] = []
        for tier in ("lightweight", "standard", "heavy"):
            for task in LLMTaskTier[tier]["tasks"]:
                if task not in tasks:
                    tasks.append(task)
        if "embedding" not in tasks:
            tasks.append("embedding")
        return tasks

    def clear_route_overrides(self) -> None:
        """Make the visible base provider/model authoritative for all tasks.

        Generation and context tuning remain intact. Only endpoint identity
        fields are cleared, so applying a base route does not destroy a user's
        task-specific temperature, token, or context preferences.
        """
        route_fields = ("model", "provider", "api_base", "api_key")
        chat_tasks = [task for task in self.routing_tasks() if task != "embedding"]
        for config_name in ("lightweight", "standard", "heavy", *chat_tasks):
            route_config = getattr(self, config_name, None)
            if not isinstance(route_config, TaskModelConfig):
                continue
            for field_name in route_fields:
                setattr(route_config, field_name, None)

    def get_model_for_task(self, task: str) -> str:
        """Get the model name for a specific task.

        Resolution: per-task override -> tier default -> global default model.

        Args:
            task: One of 'summarization', 'intent_routing', 'routing_fast',
                'planning_strict', 'search', 'download', 'torrent_ranker',
                'tool_agent_reliable', 'final_response', 'research_web',
                'chat', 'embedding', 'research'.

        Returns:
            The model string to use for that task.
        """
        config = self._resolve_config(task)
        return config.model if config.model else self.model

    def get_api_base_for_task(self, task: str) -> Optional[str]:
        """Get a task endpoint without crossing provider ownership boundaries.

        A tier/task that selects another provider must not inherit the global
        provider endpoint. Provider presets are resolved later by the provider
        manager/runtime when no explicit endpoint is returned here.
        """
        config = self._resolve_config(task)
        if config.api_base:
            return config.api_base
        provider = config.provider or self.active_provider
        return self.api_base if provider == self.active_provider else None

    def get_api_key_for_task(self, task: str) -> Optional[str]:
        """Get an explicitly compatible task/global secret.

        Global secrets are owned by the global provider+endpoint route. They are
        never inherited by a task/tier that overrides either provider or API
        base. Provider-key-store fallback is resolved later by TaskLLMClient.
        """
        config = self._resolve_config(task)
        if config.api_key:
            return config.api_key
        provider = config.provider or self.active_provider
        if provider != self.active_provider or config.api_base is not None:
            return None
        return self.api_key

    def get_max_tokens_for_task(self, task: str) -> Optional[int]:
        """Get the max_tokens for a specific task, if configured at any level."""
        config = self._resolve_config(task)
        return config.max_tokens

    def get_context_tokens_for_task(self, task: str) -> Optional[int]:
        """Get the user-configured context-window cap for a task, if set.

        The UI/backend enforce a minimum usable cap for normal operation;
        ``None`` means follow the endpoint/model maximum.
        """
        config = self._resolve_config(task)
        return config.max_context_tokens if config.max_context_tokens is not None else self.max_context_tokens

    def get_temperature_for_task(self, task: str) -> Optional[float]:
        """Get the temperature for a specific task, if configured at any level."""
        config = self._resolve_config(task)
        return config.temperature

    def has_explicit_task_config(self, task: str) -> bool:
        """Return True when a task config has explicit values set.

        A task has explicit config if its per-task override, its tier
        config, or both have at least one field populated. This is
        distinct from relying on global defaults alone.

        Args:
            task: Task name to check.

        Returns:
            True if there is an explicit per-task or tier config for this task.
        """
        per_task = getattr(self, task, None)
        if per_task and per_task.has_values():
            return True
        tier_name = self._tier_for_task(task)
        if tier_name:
            tier_config = getattr(self, tier_name, None)
            if tier_config and tier_config.has_values():
                return True
        return False

