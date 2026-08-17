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
from src.llm_providers.credential_policy import ProviderCredentialPolicy
from src.llm_providers.context_limits import (
    FALLBACK_CONTEXT_LIMIT,
    MIN_USER_CONTEXT_LIMIT,
    probe_endpoint_context_limit,
)
from src.utils.detailed_logger import LLMLogger
from src.utils.runtime_prompt_context import RuntimePromptContext
from src.llm_providers.activity import LLMActivityMonitor
from src.llm_providers.call_policy import (
    LLMCallEnvelope,
    LLMCallPolicy,
    LLMGenerationPolicy,
    LLMTaskPromptPolicy,
)


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
        config_revision: int = 0,
        route_sources: dict[str, str] | None = None,
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
        self.config_revision = int(config_revision)
        self.route_sources = dict(route_sources or {})


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

    def __init__(
        self,
        manager: LLMProviderManager,
        llm_config: LLMConfig,
        llm_logger: Optional[LLMLogger] = None,
        activity_monitor: LLMActivityMonitor | None = None,
    ):
        """Initialize with provider manager and LLM config.

        Args:
            manager: The LLM provider manager (owns registry, keys, catalog).
            llm_config: The application LLM configuration with task/tier routing.
            llm_logger: Optional LLMLogger instance.
        """
        self._manager = manager
        self._llm_config = llm_config
        self._llm_logger = llm_logger
        self._activity_monitor = activity_monitor or LLMActivityMonitor()
        self._endpoint_context_cache: dict[tuple[str, str, str], int] = {}
        self._endpoint_context_sources: dict[tuple[str, str, str], str] = {}
        self._endpoint_context_reported: dict[tuple[str, str, str], bool] = {}
        self._config_revision = 1
        self._active_completion_tasks: dict[asyncio.Task[Any], str] = {}
        self._cancellation_reasons: dict[str, str] = {}
        self._last_reload_cancelled_calls = 0


    @property
    def llm_config(self) -> LLMConfig:
        """Return the current task-routing LLM configuration."""
        return self._llm_config

    @property
    def activity_monitor(self) -> LLMActivityMonitor:
        """Return the shared user-facing LLM activity monitor."""
        return self._activity_monitor


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
            config_revision=self._config_revision,
            route_sources={
                "model": config.route_source(task, "model"),
                "provider": config.route_source(task, "provider"),
                "api_base": config.route_source(task, "api_base"),
                "api_key": config.route_source(task, "api_key"),
                "max_tokens": config.route_source(task, "max_tokens"),
                "temperature": config.route_source(task, "temperature"),
                "max_context_tokens": config.route_source(task, "max_context_tokens"),
            },
        )

    async def completion(
        self,
        task: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        stream: bool = False,
        **overrides: Any,
    ) -> Any:
        """Run a task-aware completion through one observable provider boundary.

        Per-call generation overrides are merged once and then used identically by
        LiteLLM and direct NVIDIA NIM calls. Transport controls such as timeout and
        retry count are kept separate from model generation parameters. In
        particular, routing does not receive an artificial output-token cap.
        """
        resolved = self.resolve_task(task)
        messages = RuntimePromptContext.ensure_messages(messages)
        messages = LLMTaskPromptPolicy.apply(
            task=task,
            model=resolved.model,
            messages=messages,
        )
        generation_options = LLMGenerationPolicy.effective_options(resolved, overrides)
        call_envelope = LLMCallPolicy.resolve(task, overrides)
        telemetry_generation = {
            **generation_options,
            "request_timeout_seconds": call_envelope.timeout_seconds,
            "max_attempts": call_envelope.max_attempts,
            "config_revision": resolved.config_revision,
            "route_sources": dict(resolved.route_sources),
        }
        activity_id = self._activity_monitor.start_call(
            task=task,
            provider=resolved.provider_id or "default",
            model=resolved.model,
            messages=messages,
            tools=tools,
            stream=stream,
            generation=telemetry_generation,
        )
        current_task = asyncio.current_task()
        if current_task is not None:
            self._active_completion_tasks[current_task] = activity_id

        if self._llm_logger:
            try:
                await self._llm_logger.log_context(
                    task=task,
                    messages=messages,
                    tools=tools,
                    model=resolved.model,
                    temperature=generation_options.get("temperature"),
                    max_tokens=generation_options.get("max_tokens"),
                )
            except Exception as le:
                logger.warning(f"Failed to log LLM context: {le}")

        try:
            return await self._execute_provider_completion(
                resolved=resolved,
                task=task,
                messages=messages,
                tools=tools,
                stream=stream,
                generation_options=generation_options,
                call_envelope=call_envelope,
                activity_id=activity_id,
            )
        finally:
            if current_task is not None and self._active_completion_tasks.get(current_task) == activity_id:
                self._active_completion_tasks.pop(current_task, None)
            if self._activity_monitor.status(activity_id) not in {None, "running"}:
                self._cancellation_reasons.pop(activity_id, None)

    async def _execute_provider_completion(
        self,
        *,
        resolved: ResolvedLLMTask,
        task: str,
        messages: list[dict],
        tools: list[dict] | None,
        stream: bool,
        generation_options: dict[str, Any],
        call_envelope: LLMCallEnvelope,
        activity_id: str,
    ) -> Any:
        """Dispatch one resolved call while preserving a single activity record."""
        if resolved.provider_id == "nvidia_nim":
            try:
                response = await self._completion_nvidia(
                    resolved,
                    messages,
                    tools,
                    stream,
                    activity_id=activity_id,
                    generation_options=generation_options,
                    call_envelope=call_envelope,
                )
                if stream and hasattr(response, "__aiter__"):
                    return self._wrap_activity_stream(activity_id, response)
                self._activity_monitor.finish_call(activity_id, response=response)
                return response
            except asyncio.CancelledError:
                reason = self._cancellation_reason(activity_id)
                self._activity_monitor.finish_call(
                    activity_id, status="cancelled", error=reason
                )
                raise
            except BaseException as exc:
                self._activity_monitor.finish_call(activity_id, status="failed", error=exc)
                raise

        return await self._completion_litellm(
            resolved=resolved,
            task=task,
            messages=messages,
            tools=tools,
            stream=stream,
            generation_options=generation_options,
            call_envelope=call_envelope,
            activity_id=activity_id,
        )

    async def _completion_litellm(
        self,
        *,
        resolved: ResolvedLLMTask,
        task: str,
        messages: list[dict],
        tools: list[dict] | None,
        stream: bool,
        generation_options: dict[str, Any],
        call_envelope: LLMCallEnvelope,
        activity_id: str,
    ) -> Any:
        """Execute one LiteLLM call through the bounded observable retry loop."""
        kwargs: dict[str, Any] = {
            "model": resolved.model,
            "messages": messages,
            **generation_options,
        }
        if resolved.api_base:
            kwargs["api_base"] = resolved.api_base
        if resolved.api_key:
            kwargs["api_key"] = resolved.api_key
        if tools:
            kwargs["tools"] = tools
        if stream:
            kwargs["stream"] = True

        max_attempts = call_envelope.max_attempts
        timeout_seconds = call_envelope.timeout_seconds
        # LiteLLM has its own retry layer. Disable it so this observable policy
        # remains the single source of truth.
        kwargs["num_retries"] = 0
        kwargs["timeout"] = timeout_seconds
        logger.debug(
            f"TaskLLMClient.completion(task={task}, model={resolved.model}, "
            f"provider={resolved.provider_id or 'default'}, stream={stream}, "
            f"tools={len(tools) if tools else 0}, timeout={timeout_seconds}s, "
            f"attempts={max_attempts})"
        )

        backoff = 1.0
        for attempt in range(max_attempts):
            attempt_number = attempt + 1
            self._activity_monitor.record_attempt(
                activity_id,
                attempt=attempt_number,
                max_attempts=max_attempts,
                status="started",
            )
            try:
                response = await litellm.acompletion(**kwargs)
                self._activity_monitor.record_attempt(
                    activity_id,
                    attempt=attempt_number,
                    max_attempts=max_attempts,
                    status="completed",
                )
                await self._log_nonstream_response(task, resolved.model, response, stream=stream)
                if stream and hasattr(response, "__aiter__"):
                    return self._wrap_activity_stream(activity_id, response)
                self._activity_monitor.finish_call(activity_id, response=response)
                return response
            except asyncio.CancelledError:
                reason = self._cancellation_reason(activity_id)
                self._activity_monitor.record_attempt(
                    activity_id,
                    attempt=attempt_number,
                    max_attempts=max_attempts,
                    status="cancelled",
                    error=reason,
                )
                self._activity_monitor.finish_call(
                    activity_id, status="cancelled", error=reason
                )
                raise
            except Exception as exc:
                self._activity_monitor.record_attempt(
                    activity_id,
                    attempt=attempt_number,
                    max_attempts=max_attempts,
                    status="failed",
                    error=f"{type(exc).__name__}: {str(exc) or '<no message>'}",
                )
                if self._should_retry(exc) and attempt < max_attempts - 1:
                    logger.warning(
                        f"Completion call failed: {exc}. Retrying in {backoff}s "
                        f"(attempt {attempt_number}/{max_attempts})..."
                    )
                    await asyncio.sleep(backoff)
                    backoff *= 2.0
                    continue
                self._activity_monitor.finish_call(activity_id, status="failed", error=exc)
                raise
        raise RuntimeError("LiteLLM completion retry loop exhausted without a result")

    async def _log_nonstream_response(
        self, task: str, model: str, response: Any, *, stream: bool,
    ) -> None:
        """Persist a raw non-streaming model response without affecting the call."""
        if not self._llm_logger or stream:
            return
        try:
            from src.utils.json_parser import LLMResponseParser
            raw_text = LLMResponseParser.safe_extract_content(response)
            await self._llm_logger.log_raw_response(task=task, raw_text=raw_text, model=model)
        except Exception as exc:
            logger.warning(f"Failed to log LLM response: {exc}")

    @staticmethod
    def _should_retry(exc: Exception) -> bool:
        """Return whether a provider exception is specifically transient.

        Generic words such as ``API`` or ``HTTP`` are deliberately insufficient:
        validation/authentication errors often contain those words and retrying
        them only multiplies latency. Prefer a concrete transient status, a
        transport/timeout exception class, or a narrow transport marker.
        """
        transient_statuses = {408, 429, 500, 502, 503, 504}
        for candidate in (
            getattr(exc, "status_code", None),
            getattr(exc, "http_status", None),
            getattr(getattr(exc, "response", None), "status_code", None),
        ):
            try:
                if candidate is not None and int(candidate) in transient_statuses:
                    return True
            except (TypeError, ValueError):
                pass

        class_name = type(exc).__name__.lower()
        transient_classes = (
            "timeout", "ratelimit", "connection", "transport",
            "serviceunavailable", "internalserver",
        )
        if any(marker in class_name for marker in transient_classes):
            return True

        error_text = str(exc).lower()
        transient_markers = (
            "408 request timeout", "429", "500 internal server error",
            "502", "503", "504", "timed out", "timeout",
            "rate limit", "bad gateway", "service unavailable",
            "gateway timeout", "connection reset", "connection refused",
            "connection aborted", "temporary failure", "temporarily unavailable",
        )
        return any(marker in error_text for marker in transient_markers)

    def _wrap_activity_stream(self, activity_id: str, stream: Any) -> Any:
        """Finish telemetry when a provider stream ends, fails, or is cancelled."""
        monitor = self._activity_monitor

        async def _tracked_stream():
            consumer_task = asyncio.current_task()
            if consumer_task is not None:
                self._active_completion_tasks[consumer_task] = activity_id
            try:
                async for chunk in stream:
                    yield chunk
                monitor.finish_call(activity_id, status="completed")
            except asyncio.CancelledError:
                monitor.finish_call(
                    activity_id,
                    status="cancelled",
                    error=self._cancellation_reason(activity_id),
                )
                raise
            except BaseException as exc:
                monitor.finish_call(activity_id, status="failed", error=exc)
                raise
            finally:
                if consumer_task is not None and self._active_completion_tasks.get(consumer_task) == activity_id:
                    self._active_completion_tasks.pop(consumer_task, None)
                self._cancellation_reasons.pop(activity_id, None)

        return _tracked_stream()

    @staticmethod
    def _retry_policy(task: str) -> tuple[int, float]:
        """Compatibility seam returning the current task call envelope."""
        envelope = LLMCallPolicy.resolve(task)
        return envelope.max_attempts, envelope.timeout_seconds

    async def _completion_nvidia(
        self,
        resolved: ResolvedLLMTask,
        messages: list[dict],
        tools: list[dict] | None,
        stream: bool,
        *,
        activity_id: str,
        generation_options: dict[str, Any] | None = None,
        call_envelope: LLMCallEnvelope | None = None,
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
        effective_generation = (
            generation_options
            if generation_options is not None
            else LLMGenerationPolicy.effective_options(resolved, {})
        )
        payload.update(LLMGenerationPolicy.nvidia_payload_options(effective_generation))
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

        envelope = call_envelope or LLMCallPolicy.resolve(resolved.task)
        max_attempts = envelope.max_attempts
        timeout_seconds = envelope.timeout_seconds
        backoff = 1.0
        data = None

        for attempt in range(max_attempts):
            attempt_number = attempt + 1
            self._activity_monitor.record_attempt(
                activity_id, attempt=attempt_number, max_attempts=max_attempts, status="started",
            )
            try:
                async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                    response = await client.post(url, json=payload, headers=headers)
                    if response.status_code == 200:
                        candidate_data = response.json()
                        if not isinstance(candidate_data, dict) or not candidate_data.get("choices"):
                            raise ValueError(
                                "NVIDIA NIM response missing choices: "
                                f"{str(candidate_data.get('error') if isinstance(candidate_data, dict) else candidate_data)[:500]}"
                            )
                        data = candidate_data
                        self._activity_monitor.record_attempt(
                            activity_id, attempt=attempt_number, max_attempts=max_attempts, status="completed",
                        )
                        break
                    
                    is_transient = response.status_code in (408, 429, 500, 502, 503, 504)
                    err_msg = f"NVIDIA NIM returned status code {response.status_code}: {response.text[:500]}"
                    if is_transient and attempt < max_attempts - 1:
                        logger.warning(
                            f"NVIDIA NIM failed with transient error: {err_msg}. "
                            f"Retrying in {backoff}s (attempt {attempt + 1}/{max_attempts})..."
                        )
                        self._activity_monitor.record_attempt(
                            activity_id, attempt=attempt_number, max_attempts=max_attempts,
                            status="failed", error=err_msg,
                        )
                        await asyncio.sleep(backoff)
                        backoff *= 2.0
                        continue
                    else:
                        raise RuntimeError(err_msg)
            except asyncio.CancelledError:
                reason = self._cancellation_reason(activity_id)
                self._activity_monitor.record_attempt(
                    activity_id, attempt=attempt_number, max_attempts=max_attempts,
                    status="cancelled", error=reason,
                )
                raise
            except Exception as exc:
                error_text = f"{type(exc).__name__}: {str(exc) or '<no message>'}"
                self._activity_monitor.record_attempt(
                    activity_id, attempt=attempt_number, max_attempts=max_attempts,
                    status="failed", error=error_text,
                )
                retryable = isinstance(exc, (httpx.TimeoutException, httpx.TransportError))
                if retryable and attempt < max_attempts - 1:
                    logger.warning(
                        f"NVIDIA NIM transient call failure: {error_text}. "
                        f"Retrying in {backoff}s (attempt {attempt_number}/{max_attempts})..."
                    )
                    await asyncio.sleep(backoff)
                    backoff *= 2.0
                    continue
                raise

        if not data:
            raise RuntimeError("NVIDIA NIM call failed: No data retrieved.")

        from litellm.types.utils import ModelResponse, Choices, Message

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
        usage = data.get("usage") if isinstance(data, dict) else None
        if usage:
            try:
                resp.usage = usage
            except Exception:
                pass

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

        provider_id = self._resolve_provider(task, resolved_task)
        api_base = self._resolve_api_base(task, resolved_task, provider_id)
        api_key = self._resolve_api_key(task, resolved_task, provider_id)

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
        old_revision = self._config_revision
        self._llm_config = llm_config
        self._config_revision += 1
        self._last_reload_cancelled_calls = self._cancel_active_old_routes(
            old_revision=old_revision,
            new_revision=self._config_revision,
        )
        self._endpoint_context_cache.clear()
        self._endpoint_context_sources.clear()
        self._endpoint_context_reported.clear()
        logger.info(
            "TaskLLMClient config hot-reloaded at revision {} (cancelled_old_route_calls={}).",
            self._config_revision,
            self._last_reload_cancelled_calls,
        )

    def _cancel_active_old_routes(self, *, old_revision: int, new_revision: int) -> int:
        """Cancel provider work that captured a superseded route revision."""
        try:
            current = asyncio.current_task()
        except RuntimeError:
            current = None
        cancelled = 0
        for task, call_id in list(self._active_completion_tasks.items()):
            if task.done() or task is current:
                continue
            self._activity_monitor.record_configuration_change(
                call_id,
                old_revision=old_revision,
                new_revision=new_revision,
            )
            self._cancellation_reasons[call_id] = (
                f"LLM route configuration changed from revision {old_revision} "
                f"to {new_revision} while the call was active."
            )
            task.cancel()
            cancelled += 1
        return cancelled

    def _cancellation_reason(self, call_id: str) -> str:
        """Return the truthful cancellation cause for one observable call."""
        return self._cancellation_reasons.get(call_id, "Cancelled by user")

    @property
    def last_reload_cancelled_calls(self) -> int:
        """Return how many active calls were stopped by the latest settings save."""
        return self._last_reload_cancelled_calls

    @property
    def config_revision(self) -> int:
        """Return the active routing configuration revision."""
        return self._config_revision

    def effective_routes(self) -> list[dict[str, Any]]:
        """Return the exact provider/model route used by each task.

        API keys are intentionally excluded. This public diagnostic seam lets
        settings and chat UI show which override layer actually owns a task.
        """
        rows: list[dict[str, Any]] = []
        for task in self._llm_config.routing_tasks():
            if task == "embedding" and not self._llm_config.has_explicit_task_config(task):
                rows.append({
                    "task": task,
                    "tier": None,
                    "provider": None,
                    "model": None,
                    "source": "disabled_without_explicit_route",
                    "config_revision": self._config_revision,
                })
                continue
            resolved = self.resolve_task(task)
            rows.append({
                "task": task,
                "tier": self._llm_config.tier_for_task(task),
                "provider": resolved.provider_id,
                "model": resolved.model,
                "source": resolved.route_sources.get("model", "global"),
                "provider_source": resolved.route_sources.get("provider", "global"),
                "config_revision": self._config_revision,
            })
        return rows

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
        if provider_id == config.active_provider and config.api_base:
            return config.api_base

        # Provider registry resolves an operator override first and otherwise
        # falls back to the provider preset.  Credential attachment is decided
        # separately by ProviderCredentialPolicy, so honoring a custom endpoint
        # here cannot make a stored provider key follow it.
        if provider_id:
            resolved = self._manager.registry.get_resolved_api_base(provider_id)
            if resolved:
                return resolved

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

        # The global secret is coupled to the global provider+endpoint route.
        # Never send it to a task/tier that overrides provider or API base.
        if (
            config.api_key
            and provider_id == config.active_provider
            and resolved_task.api_base is None
        ):
            return config.api_key

        # Provider key-store secrets may only be auto-attached to a provider-owned
        # endpoint (the provider preset), never to an operator override or arbitrary custom URL.
        api_base = self._resolve_api_base(task, resolved_task, provider_id)
        if provider_id and ProviderCredentialPolicy.is_provider_owned_endpoint(self._manager.registry, provider_id, api_base):
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