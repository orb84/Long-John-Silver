"""
Task-aware LLM runtime for LJS.

Provides a single source of truth for LLM completions and embeddings,
resolving provider, model, API key, and endpoint through the priority
chain: per-task override -> tier default -> global default -> active
provider preset.
"""

import asyncio
import litellm
from loguru import logger
from typing import Any, Optional

from src.core.models import LLMConfig, TaskModelConfig
from src.llm_providers.manager import LLMProviderManager
from src.llm_providers.context_limits import (
    FALLBACK_CONTEXT_LIMIT,
    MIN_USER_CONTEXT_LIMIT,
    probe_endpoint_context_limit,
)
from src.utils.detailed_logger import LLMLogger
from src.utils.runtime_prompt_context import RuntimePromptContext


class ResolvedLLMTask:
    """A fully resolved LLM task route.

    Contains every parameter needed to make an LLM call, resolved
    from the task/tier/global/active-provider priority chain. No
    field should be None unless it is genuinely optional for the
    provider being used.
    """

    def __init__(
        self,
        task: str,
        model: str,
        provider_id: str = "",
        api_base: Optional[str] = None,
        api_key: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        context_limit: Optional[int] = None,
        context_limit_source: str = "unknown",
        context_limit_reported: bool = False,
        supports_tools: bool = True,
        supports_streaming: bool = True,
    ):
        self.task = task
        self.model = model
        self.provider_id = provider_id
        self.api_base = api_base
        self.api_key = api_key
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.context_limit = context_limit
        self.context_limit_source = context_limit_source
        self.context_limit_reported = context_limit_reported
        self.supports_tools = supports_tools
        self.supports_streaming = supports_streaming


class TaskLLMClient:
    """Task-aware LLM runtime for completions and embeddings.

    Routes every LLM call through a unified resolution chain:
    1. Per-task TaskModelConfig (highest priority)
    2. Tier TaskModelConfig
    3. Global LLMConfig defaults
    4. Active provider preset from LLMProviderManager

    This ensures the settings UI, key store, and runtime execution
    all use the same source of truth for provider, model, and key
    selection.
    """

    DEFAULT_CONTEXT_LIMIT = FALLBACK_CONTEXT_LIMIT

    def __init__(self, manager: LLMProviderManager, llm_config: LLMConfig, llm_logger: Optional[LLMLogger] = None):
        """Initialize with provider manager and LLM config.

        Args:
            manager: The LLM provider manager (owns registry, keys, catalog).
            llm_config: The application LLM configuration with task/tier routing.
            llm_logger: Optional LLMLogger instance.
        """
        self._manager = manager
        self._llm_config = llm_config
        self._llm_logger = llm_logger
        self._endpoint_context_cache: dict[tuple[str, str, str], int] = {}
        self._endpoint_context_sources: dict[tuple[str, str, str], str] = {}
        self._endpoint_context_reported: dict[tuple[str, str, str], bool] = {}


    @property
    def llm_config(self) -> LLMConfig:
        """Return the current task-routing LLM configuration."""
        return self._llm_config


    async def ensure_model_metadata_for_task(self, task: str, force_refresh: bool = False) -> None:
        """Best-effort warm-up of provider model metadata for a task.

        Context budgeting depends on provider/model catalog metadata when the
        endpoint exposes it (for example OpenRouter-style ``context_length``).
        Runtime trimming happens before the actual completion call, so callers
        invoke this warm-up before resolving the token budget.  The provider
        manager cache keeps this cheap after the first call.
        """
        try:
            resolved_task = self._llm_config.resolve_config(task)
            provider_id = self._resolve_provider(task, resolved_task)
            model = self._resolve_model(task, resolved_task)
            if not provider_id or not model:
                return
            await self._manager.get_models_for_provider(provider_id, force_refresh=force_refresh)
            api_base = self._resolve_api_base(task, resolved_task, provider_id)
            api_key = self._resolve_api_key(task, resolved_task, provider_id)
            cache_key = self._context_cache_key(provider_id, model, api_base)
            if force_refresh or cache_key not in self._endpoint_context_cache:
                probe = await probe_endpoint_context_limit(
                    base_url=api_base,
                    model_id=model,
                    api_key=api_key,
                    provider_id=provider_id,
                    fallback_tokens=self.DEFAULT_CONTEXT_LIMIT,
                )
                self._endpoint_context_cache[cache_key] = int(probe.usable_context_tokens)
                self._endpoint_context_sources[cache_key] = probe.source
                self._endpoint_context_reported[cache_key] = bool(probe.endpoint_reported)
                logger.info(
                    "Resolved context window for task={} provider={} model={} -> {} tokens via {}",
                    task, provider_id, model, probe.usable_context_tokens, probe.source,
                )
        except Exception as exc:
            logger.debug(f"Model metadata warm-up skipped for task {task}: {exc}")

    def endpoint_context_limit_for_task(self, task: str) -> Optional[int]:
        """Return cached endpoint/model context limit for a task, if known."""
        try:
            return self.resolve_task(task).context_limit
        except Exception:
            return None

    def resolve_task(self, task: str) -> ResolvedLLMTask:
        """Resolve provider, model, key, endpoint, and generation options for a task.

        Resolution order for each field:
        - Provider: per-task provider -> tier provider -> LLMConfig.active_provider
          -> LLMProviderManager registry active provider
        - Model: per-task model -> tier model -> global LLMConfig.model
          (must resolve to a value; raises ValueError if missing)
        - API base: per-task api_base -> tier api_base -> global api_base
          -> provider preset api_base
        - API key: per-task api_key -> tier api_key -> global api_key
          -> active key from KeyStore
        - Temperature/max_tokens: per-task -> tier -> None (not passed if unset)

        Args:
            task: Task name (e.g., 'search', 'download', 'chat', 'summarization',
                'intent_routing', 'routing_fast', 'planning_strict',
                'torrent_ranker', 'tool_agent_reliable', 'final_response',
                'research_web', 'embedding', 'research').

        Returns:
            A ResolvedLLMTask with all fields populated.

        Raises:
            ValueError: If no model can be resolved for the task.
        """
        config = self._llm_config
        resolved_task = config.resolve_config(task)

        # --- Provider ---
        provider_id = self._resolve_provider(task, resolved_task)

        # --- Model (required) ---
        model = self._resolve_model(task, resolved_task)
        if not model:
            raise ValueError(
                f"No model configured for task '{task}'. "
                f"Set a global model, tier model, or per-task model."
            )

        # --- API base ---
        api_base = self._resolve_api_base(task, resolved_task, provider_id)

        # --- API key ---
        api_key = self._resolve_api_key(task, resolved_task, provider_id)

        # --- Generation options ---
        max_tokens = resolved_task.max_tokens if resolved_task.max_tokens is not None else None
        temperature = resolved_task.temperature if resolved_task.temperature is not None else None

        # --- Endpoint/model context limit (best-effort from provider catalog) ---
        # User caps are applied later by LLMTaskRuntime.  When the provider does
        # not report metadata, the fallback is a default, not a hard maximum.
        context_limit, context_limit_source, context_limit_reported = self._resolve_context_limit_info(provider_id, model)

        # --- Feature support (best-effort from catalog or preset) ---
        supports_tools, supports_streaming = self._resolve_feature_support(
            provider_id, model,
        )

        return ResolvedLLMTask(
            task=task,
            model=model,
            provider_id=provider_id,
            api_base=api_base,
            api_key=api_key,
            max_tokens=max_tokens,
            temperature=temperature,
            context_limit=context_limit,
            context_limit_source=context_limit_source,
            context_limit_reported=context_limit_reported,
            supports_tools=supports_tools,
            supports_streaming=supports_streaming,
        )

    async def completion(
        self,
        task: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        stream: bool = False,
        **overrides: Any,
    ) -> Any:
        """Run a chat completion for a configured task.

        For nvidia_nim, uses httpx directly because litellm hijacks the
        'openai/' model prefix and routes to api.openai.com regardless
        of our custom api_base.
        """
        resolved = self.resolve_task(task)
        messages = RuntimePromptContext.ensure_messages(messages)

        if self._llm_logger:
            try:
                await self._llm_logger.log_context(
                    task=task,
                    messages=messages,
                    tools=tools,
                    model=resolved.model,
                    temperature=resolved.temperature,
                    max_tokens=resolved.max_tokens,
                )
            except Exception as le:
                logger.warning(f"Failed to log LLM context: {le}")

        if resolved.provider_id == "nvidia_nim":
            return await self._completion_nvidia(resolved, messages, tools, stream)

        kwargs: dict[str, Any] = {
            "model": resolved.model,
            "messages": messages,
        }
        if resolved.api_base:
            kwargs["api_base"] = resolved.api_base
        if resolved.api_key:
            kwargs["api_key"] = resolved.api_key
        if resolved.temperature is not None:
            kwargs["temperature"] = resolved.temperature
        if resolved.max_tokens is not None:
            kwargs["max_tokens"] = resolved.max_tokens
        if tools:
            kwargs["tools"] = tools
        if stream:
            kwargs["stream"] = True
        kwargs.update(overrides)

        logger.debug(
            f"TaskLLMClient.completion(task={task}, model={resolved.model}, "
            f"provider={resolved.provider_id or 'default'}, "
            f"stream={stream}, tools={len(tools) if tools else 0})"
        )
        
        max_attempts = 4
        backoff = 1.0
        for attempt in range(max_attempts):
            try:
                res = await litellm.acompletion(**kwargs)
                if self._llm_logger and not stream:
                    try:
                        from src.utils.json_parser import LLMResponseParser
                        raw_text = LLMResponseParser.safe_extract_content(res)
                        await self._llm_logger.log_raw_response(task=task, raw_text=raw_text, model=resolved.model)
                    except Exception as le:
                        logger.warning(f"Failed to log LLM response: {le}")
                return res
            except Exception as e:
                err_str = str(e).lower()
                is_transient = any(
                    x in err_str
                    for x in ("502", "503", "504", "429", "timeout", "rate limit", "bad gateway", "connection error")
                )
                if (is_transient or "api" in err_str or "http" in err_str) and attempt < max_attempts - 1:
                    logger.warning(
                        f"Completion call failed: {e}. "
                        f"Retrying in {backoff}s (attempt {attempt + 1}/{max_attempts})..."
                    )
                    await asyncio.sleep(backoff)
                    backoff *= 2.0
                    continue
                else:
                    raise e

    async def _completion_nvidia(
        self, resolved: ResolvedLLMTask, messages: list[dict],
        tools: list[dict] | None, stream: bool,
    ) -> Any:
        """Call NVIDIA NIM API directly via httpx, bypassing litellm routing.

        NVIDIA NIM uses an OpenAI-compatible API but litellm hijacks the
        'openai/' model prefix and routes to api.openai.com regardless
        of api_base. We call the API directly with httpx.

        For streaming, makes a non-streaming request (NIM SSE output is
        simpler to handle) and wraps the result in an async generator
        compatible with the assistant's token-by-token iteration.
        """
        import httpx

        base = (resolved.api_base or "").rstrip("/")
        url = f"{base}/chat/completions"

        payload: dict[str, Any] = {
            "model": resolved.model,
            "messages": messages,
            "stream": False,
        }
        if resolved.temperature is not None:
            payload["temperature"] = resolved.temperature
        if resolved.max_tokens is not None:
            payload["max_tokens"] = resolved.max_tokens
        if tools:
            payload["tools"] = tools

        headers = {
            "Authorization": f"Bearer {resolved.api_key}",
            "Content-Type": "application/json",
        }

        logger.debug(
            f"TaskLLMClient.nvidia_nim(task={resolved.task}, model={resolved.model}, "
            f"tools={len(tools) if tools else 0})"
        )

        max_attempts = 4
        backoff = 1.0
        data = None

        for attempt in range(max_attempts):
            try:
                async with httpx.AsyncClient(timeout=300.0) as client:
                    response = await client.post(url, json=payload, headers=headers)
                    if response.status_code == 200:
                        candidate_data = response.json()
                        if not isinstance(candidate_data, dict) or not candidate_data.get("choices"):
                            raise ValueError(
                                "NVIDIA NIM response missing choices: "
                                f"{str(candidate_data.get('error') if isinstance(candidate_data, dict) else candidate_data)[:500]}"
                            )
                        data = candidate_data
                        break
                    
                    is_transient = response.status_code in (502, 503, 504, 429)
                    err_msg = f"NVIDIA NIM returned status code {response.status_code}: {response.text[:500]}"
                    if is_transient and attempt < max_attempts - 1:
                        logger.warning(
                            f"NVIDIA NIM failed with transient error: {err_msg}. "
                            f"Retrying in {backoff}s (attempt {attempt + 1}/{max_attempts})..."
                        )
                        await asyncio.sleep(backoff)
                        backoff *= 2.0
                        continue
                    else:
                        raise Exception(err_msg)
            except (httpx.HTTPError, Exception) as e:
                if attempt < max_attempts - 1:
                    logger.warning(
                        f"NVIDIA NIM call failed with exception: {e}. "
                        f"Retrying in {backoff}s (attempt {attempt + 1}/{max_attempts})..."
                    )
                    await asyncio.sleep(backoff)
                    backoff *= 2.0
                    continue
                else:
                    raise e

        if not data:
            raise Exception("NVIDIA NIM call failed: No data retrieved.")

        from litellm.types.utils import ModelResponse, Choices, Message, Delta, StreamingChoices
        from litellm.types.llms.openai import ChatCompletionResponseMessage

        msg = data["choices"][0]["message"]
        content = msg.get("content") or ""
        finish_reason = data["choices"][0].get("finish_reason", "stop")
        raw_tool_calls = msg.get("tool_calls") or []

        resp = ModelResponse(
            id=data.get("id", ""),
            choices=[
                Choices(
                    finish_reason=finish_reason,
                    index=0,
                    message=Message(
                        content=content,
                        role="assistant",
                        tool_calls=raw_tool_calls,
                    ),
                )
            ],
            created=data.get("created", 0),
            model=data.get("model", resolved.model),
            object="chat.completion",
        )

        if not stream:
            if self._llm_logger:
                try:
                    await self._llm_logger.log_raw_response(
                        task=resolved.task, raw_text=content, model=resolved.model
                    )
                except Exception as le:
                    logger.warning(f"Failed to log LLM response: {le}")
            return resp

        tokens = content.split(" ") if content else []
        from litellm.types.utils import StreamingChoices, Delta

        async def _stream_gen():
            if raw_tool_calls:
                yield ModelResponse(
                    id=data.get("id", ""),
                    choices=[
                        StreamingChoices(
                            finish_reason=finish_reason,
                            index=0,
                            delta=Delta(
                                content=None,
                                role="assistant",
                                tool_calls=raw_tool_calls,
                            ),
                        )
                    ],
                    created=data.get("created", 0),
                    model=data.get("model", resolved.model),
                    object="chat.completion.chunk",
                )
                return

            for i, token in enumerate(tokens):
                sep = " " if i < len(tokens) - 1 else ""
                yield ModelResponse(
                    id=data.get("id", ""),
                    choices=[
                        StreamingChoices(
                            finish_reason=finish_reason if i == len(tokens) - 1 else None,
                            index=0,
                            delta=Delta(content=token + sep, role="assistant"),
                        )
                    ],
                    created=data.get("created", 0),
                    model=data.get("model", resolved.model),
                    object="chat.completion.chunk",
                )

        return _stream_gen()

    async def embedding(self, task: str, text: str) -> list[float] | None:
        """Run an embedding request for a configured task.

        Returns None if the embedding task has no explicit model configured,
        so callers can fall back to hash-based or other alternatives.

        Args:
            task: Task name (typically 'embedding').
            text: The text to embed.

        Returns:
            Embedding vector, or None if no embedding model is configured.
        """
        config = self._llm_config
        resolved_task = config.resolve_config(task)

        # Only proceed if there is an explicit embedding model configured.
        # Do not fall back to a chat model for embeddings.
        model = resolved_task.model or config.model
        if not resolved_task.has_values():
            # No explicit task config — check if the default model looks
            # like an embedding model. If not, refuse.
            logger.warning(
                f"No explicit embedding model configured for task '{task}'. "
                f"Returning None so caller can use fallback."
            )
            return None

        api_base = resolved_task.api_base if resolved_task.api_base else config.api_base
        api_key = resolved_task.api_key if resolved_task.api_key else config.api_key

        # Try resolving key from provider manager if still missing
        if not api_key:
            provider_id = resolved_task.provider if resolved_task.provider else config.active_provider
            if provider_id:
                api_key = self._manager.keys.get_active_key(provider_id)
                api_key = api_key.key if api_key else None

        kwargs: dict[str, Any] = {
            "model": model,
            "input": [text],
        }
        if api_base:
            kwargs["api_base"] = api_base
        if api_key:
            kwargs["api_key"] = api_key

        try:
            response = await litellm.aembedding(**kwargs)
            return response.data[0]["embedding"]
        except Exception as e:
            logger.error(f"Embedding call failed for task '{task}': {e}")
            return None

    def update_config(self, llm_config: LLMConfig) -> None:
        """Hot-reload task routing settings.

        Args:
            llm_config: The new LLM configuration to use.
        """
        self._llm_config = llm_config
        self._endpoint_context_cache.clear()
        self._endpoint_context_sources.clear()
        self._endpoint_context_reported.clear()
        logger.info("TaskLLMClient config hot-reloaded.")

    def _resolve_provider(self, task: str, resolved_task: TaskModelConfig) -> str:
        """Resolve the provider ID for a task.

        Priority: per-task provider -> tier provider -> active_provider
        -> registry active provider.
        """
        config = self._llm_config

        # Per-task provider
        if resolved_task.provider:
            return resolved_task.provider

        # Global active_provider
        if config.active_provider:
            return config.active_provider

        # Registry active provider
        registry_active = self._manager.registry.get_active_provider_id()
        if registry_active:
            return registry_active

        logger.warning(f"No provider resolved for task '{task}', using empty string")
        return ""

    def _resolve_model(self, task: str, resolved_task: TaskModelConfig) -> str:
        """Resolve the model for a task.

        Priority: per-task model -> tier model -> global model.
        """
        config = self._llm_config

        if resolved_task.model:
            return resolved_task.model

        # Global default model
        if config.model:
            return config.model

        return ""

    def _resolve_api_base(
        self, task: str, resolved_task: TaskModelConfig, provider_id: str,
    ) -> Optional[str]:
        """Resolve the API base URL for a task.

        Priority: per-task api_base -> tier api_base -> global api_base
        -> provider preset api_base.
        """
        config = self._llm_config

        if resolved_task.api_base:
            return resolved_task.api_base
        if config.api_base:
            return config.api_base

        # Provider preset API base
        if provider_id:
            preset = self._manager.registry.get_preset(provider_id)
            if preset and preset.api_base:
                return preset.api_base
            # Check for API base override
            override = self._manager.registry.get_resolved_api_base(provider_id)
            if override:
                return override

        return None

    def _resolve_api_key(
        self, task: str, resolved_task: TaskModelConfig, provider_id: str,
    ) -> Optional[str]:
        """Resolve the API key for a task.

        Priority: per-task api_key -> tier api_key -> global api_key
        -> active key from KeyStore.
        """
        config = self._llm_config

        if resolved_task.api_key:
            return resolved_task.api_key
        if config.api_key:
            return config.api_key

        # Active key from KeyStore
        if provider_id:
            active_key = self._manager.keys.get_active_key(provider_id)
            if active_key:
                return active_key.key

        return None

    def _resolve_context_limit(self, provider_id: str, model: str) -> int:
        """Best-effort context limit lookup from runtime probe, catalog, or fallback."""
        return self._resolve_context_limit_info(provider_id, model)[0]

    def _resolve_context_limit_info(self, provider_id: str, model: str) -> tuple[int, str, bool]:
        """Return context limit plus source and whether it was endpoint-reported.

        The numeric fallback keeps unknown endpoints usable, but it must not be
        treated as a real provider maximum.  Runtime budgeting uses the boolean
        to decide whether an explicit user cap may exceed the fallback.
        """
        default_limit = max(MIN_USER_CONTEXT_LIMIT, self.DEFAULT_CONTEXT_LIMIT)

        if not provider_id:
            return default_limit, "configured fallback", False

        # Runtime probes know provider API-base overrides and LM Studio loaded
        # instance context. They are warmed immediately before completion calls.
        for cache_key, value in self._endpoint_context_cache.items():
            cached_provider, cached_model, _base = cache_key
            if cached_provider != provider_id:
                continue
            if cached_model == model or cached_model.endswith(f"/{model}") or model.endswith(f"/{cached_model}"):
                return (
                    int(value),
                    self._endpoint_context_sources.get(cache_key, "runtime_probe"),
                    bool(self._endpoint_context_reported.get(cache_key, False)),
                )

        # Try catalog cache.  This covers endpoints that report context metadata
        # through OpenAI-compatible /models.
        cached = self._manager.catalog.cached_models(provider_id)
        for m in cached:
            if m.id == model or m.id.endswith(f"/{model}") or model.endswith(f"/{m.id}"):
                if m.context and m.context.max_context_tokens:
                    return int(m.context.max_context_tokens), "provider_model_endpoint", True

        return default_limit, "configured fallback", False

    @staticmethod
    def _context_cache_key(provider_id: str, model: str, api_base: str | None) -> tuple[str, str, str]:
        return (str(provider_id or ""), str(model or ""), str(api_base or ""))

    def _resolve_feature_support(
        self, provider_id: str, model: str,
    ) -> tuple[bool, bool]:
        """Best-effort feature support lookup from model catalog.

        Returns:
            Tuple of (supports_tools, supports_streaming).
        """
        supports_tools = True
        supports_streaming = True

        if not provider_id:
            return supports_tools, supports_streaming

        cached = self._manager.catalog.cached_models(provider_id)
        for m in cached:
            if m.id == model or m.id.endswith(f"/{model}"):
                return m.context.supports_tools, m.context.supports_streaming

        return supports_tools, supports_streaming