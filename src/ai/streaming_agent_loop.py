"""
Streaming agent loop executor for LJS.

Runs the streaming agentic tool loop previously embedded in
AIAssistant.run_stream(). Uses StreamingToolCallAssembler for
correct multi-tool assembly and ToolCallExecutor for shared
tool execution with the non-streaming path.
"""

from typing import Any, AsyncIterator

from loguru import logger

from src.ai.streaming_tool_calls import StreamingToolCallAssembler
from src.ai.tool_executor import ToolCallExecutor
from src.ai.plan_executor import PlanExecutor
from src.core.models import AgentPlan, PlanExecutionResult, PlanExecutionStep, PlanStep, Intent, ToolExecutionContext
from src.utils.circuit_breaker import CircuitOpenError
from src.ai.error_presenter import AgentErrorPresenter
from src.ai.chat_presenter import AgentChatPresenter
from src.ai.bare_tool_call import BareToolCallDetector


class StreamingAgentLoopExecutor:
    """Executes the streaming agentic tool loop.

    Responsibilities:
    - Call LLM completion with stream=True, yielding tokens as they arrive.
    - Assemble streaming tool calls via StreamingToolCallAssembler.
    - Execute tool calls via ToolCallExecutor (shared with non-streaming path).
    - Handle CircuitOpenError and other exceptions gracefully.
    - Track last_content for conversation recording after streaming completes.

    The executor is stateless between calls — all mutable state
    is managed within a single execute() invocation. The last_content
    attribute is available after execute() completes for the caller
    to record in conversation memory.
    """

    def __init__(
        self,
        tool_executor: ToolCallExecutor,
        stream_completion: Any,
        error_presenter: AgentErrorPresenter | None = None,
        chat_presenter: AgentChatPresenter | None = None,
    ) -> None:
        """Initialize the streaming agent loop executor.

        Args:
            tool_executor: Executor for tool calls (shared with non-streaming path).
            stream_completion: Async callable that accepts task, messages, tools
                and kwargs, and returns an async iterable of LLM streaming chunks.
                Wraps the circuit breaker and handles both TaskLLMClient and
                litellm paths.
            error_presenter: Persona-aware formatter for deterministic streamed errors.
            chat_presenter: Persona-aware formatter for deterministic success/progress messages.
        """
        self._tool_executor = tool_executor
        self._stream_completion = stream_completion
        self._error_presenter = error_presenter or AgentErrorPresenter()
        self._chat_presenter = chat_presenter or AgentChatPresenter()
        self.last_content: str = ""
        """The last complete response content. Available after execute()
        finishes — used by the caller for conversation recording."""

    async def execute(
        self,
        messages: list,
        tool_definitions: list | None,
        allowed_tool_names: set[str],
        max_iterations: int,
        task: str,
        generation_options: dict | None = None,
        plan: AgentPlan | None = None,
        plan_executor: PlanExecutor | None = None,
        plan_trace_store: Any | None = None,
        session_id: str | None = None,
    ) -> AsyncIterator[str]:
        """Execute the streaming agent tool loop, yielding tokens.

        Args:
            messages: The message list (mutated in place with assistant
                responses and tool results appended during execution).
            tool_definitions: OpenAI-format tool definitions, or None
                when tools should be hidden from the model.
            allowed_tool_names: Set of tool names allowed for this intent.
            max_iterations: Maximum loop iterations before forced fallback.
            task: Task name for LLM config routing (e.g. 'chat', 'search').
            generation_options: Optional max_tokens/temperature overrides.
            plan: Optional structured AgentPlan to execute before the
                agentic loop. Steps are run deterministically and their
                results injected into the message context.
            plan_executor: Optional PlanExecutor for executing the
                structured plan. Required if plan is provided.
            plan_trace_store: Optional PlanTraceStore to persist
                plan execution traces after execution.
            session_id: Optional session ID for trace attribution.

        Yields:
            String chunks (tokens) as they arrive from the LLM, including
            error messages when exceptions occur.
        """
        if generation_options is None:
            generation_options = {}

        # Execute structured plan steps before the agentic loop if provided
        if plan and plan.steps and plan_executor:
            plan_error = await self._execute_plan_steps(
                plan, plan_executor, messages,
                plan_trace_store=plan_trace_store,
                session_id=session_id,
            )
            if plan_error is not None:
                self.last_content = plan_error
                yield plan_error
                return

        for i in range(max_iterations):
            try:
                stream_response = await self._stream_completion(
                    task=task,
                    messages=messages,
                    tools=tool_definitions,
                    **generation_options,
                )

                # Use StreamingToolCallAssembler for correct multi-tool handling
                assembler = StreamingToolCallAssembler()
                collected_content = ""
                pending_jsonish_content = ""
                emitted_content = False

                async for chunk in stream_response:
                    delta = chunk.choices[0].delta

                    # Yield content tokens as they arrive, except for a leading
                    # JSON object that may be a malformed tool call.  Buffering
                    # prevents raw objects like {"query": "..."} from flashing
                    # in chat before we can recover them as real tool calls.
                    # Some providers return dict-style chunks, others object-style.
                    content_text = (
                        delta.content if hasattr(delta, "content")
                        else delta.get("content", "")
                    )
                    if content_text:
                        if not emitted_content and BareToolCallDetector.looks_like_json_prefix(pending_jsonish_content + content_text):
                            pending_jsonish_content += content_text
                        else:
                            if pending_jsonish_content:
                                collected_content += pending_jsonish_content
                                emitted_content = True
                                yield pending_jsonish_content
                                pending_jsonish_content = ""
                            collected_content += content_text
                            emitted_content = True
                            yield content_text

                    # Accumulate tool call deltas using the assembler
                    tool_deltas = None
                    if hasattr(delta, "tool_calls"):
                        tool_deltas = delta.tool_calls
                    elif isinstance(delta, dict) and "tool_calls" in delta:
                        tool_deltas = delta["tool_calls"]
                    if tool_deltas:
                        assembler.add_delta(tool_deltas)

                # Complete any leading buffered content now that we know whether
                # the model actually used the tool-call channel.
                tool_calls = assembler.complete_calls()
                recovered = None
                if pending_jsonish_content and not tool_calls and not collected_content:
                    recovered = BareToolCallDetector.from_text(pending_jsonish_content, allowed_tool_names)
                    if recovered is None:
                        collected_content += pending_jsonish_content
                        emitted_content = True
                        yield pending_jsonish_content
                    pending_jsonish_content = ""

                # Store the complete text content from this iteration
                # so the caller can record the final response.  Recovered bare
                # tool calls intentionally do not become assistant text.
                self.last_content = collected_content

                if recovered is not None:
                    logger.warning(
                        "Recovered bare JSON streaming output as tool call: {}",
                        recovered.name,
                    )
                    messages.append({
                        "role": "assistant",
                        "tool_calls": [{
                            "id": recovered.call_id,
                            "type": "function",
                            "function": {
                                "name": recovered.name,
                                "arguments": recovered.arguments,
                            },
                        }],
                    })
                    result_message, _ = await self._tool_executor.execute_tool_call(
                        name=recovered.name,
                        arguments_raw=recovered.arguments,
                        tool_call_id=recovered.call_id,
                        allowed_tool_names=allowed_tool_names,
                        tool_context=self._tool_context(session_id),
                    )
                    messages.append(result_message)
                    continue

                if not tool_calls:
                    # Pure text response — already streamed to the user
                    logger.debug(
                        f"Agent iteration {i}: no tool calls, streamed "
                        f"{len(collected_content)} chars"
                    )
                    break

                logger.info(
                    f"Agent iteration {i}: executing {len(tool_calls)} tool call(s)"
                )

                # Process assembled tool calls
                for tc in tool_calls:
                    messages.append({
                        "role": "assistant",
                        "tool_calls": [{
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": tc.arguments,
                            },
                        }],
                    })
                    result_message, _ = await self._tool_executor.execute_tool_call(
                        name=tc.name,
                        arguments_raw=tc.arguments,
                        tool_call_id=tc.id,
                        allowed_tool_names=allowed_tool_names,
                        tool_context=self._tool_context(session_id),
                    )
                    messages.append(result_message)

                # Next iteration will stream the final answer

            except CircuitOpenError:
                logger.warning("LLM circuit breaker is OPEN — service unavailable")
                error_msg = self._error_presenter.circuit_open("AI streaming completion")
                self.last_content = error_msg
                yield error_msg
                return

            except Exception as e:
                logger.error(f"Agent execution error (iteration {i}): {e}")
                error_msg = self._error_presenter.exception("streaming agent execution", e)
                self.last_content = error_msg
                yield error_msg
                return

        else:
            # Max iterations exhausted without a conclusive response
            fallback = self._error_presenter.iteration_limit()
            self.last_content = fallback
            yield fallback


    @staticmethod
    def _download_goal_requests_batch(plan: AgentPlan) -> bool:
        """Return True when the original DOWNLOAD goal is explicitly multi-unit."""
        text = f"{plan.user_goal or ''} {getattr(plan, 'constraints', {})}".lower()
        markers = (
            "remaining", "missing", "all ", "all_", "every", "batch",
            "season", "episodes", "episodi", "mancanti", "rimanenti",
        )
        return any(marker in text for marker in markers)

    @classmethod
    async def _maybe_auto_queue_batch_recommendation(
        cls,
        plan: AgentPlan,
        plan_executor: PlanExecutor,
        plan_result: PlanExecutionResult,
        messages: list,
        error_presenter: AgentErrorPresenter,
        chat_presenter: AgentChatPresenter | None = None,
    ) -> str | None:
        """Leave batch recommendations for LLM evaluation instead of auto-queueing.

        Search tools may expose ``batch_recommendation.queue_download_arguments``
        as strong evidence.  The chat model must still compare that evidence
        against category context, language policy, quality, ambiguity, and the
        user's wording before choosing ``queue_download``.  The unused
        parameters remain for backward-compatible audit callers.
        """
        _ = (cls, plan, plan_executor, plan_result, messages, error_presenter, chat_presenter)
        return None


    @staticmethod
    def _tool_context(session_id: str | None) -> ToolExecutionContext:
        """Build lightweight invocation context for declarative tools."""
        source = "web"
        if session_id and ":" in session_id:
            source = session_id.split(":", 1)[0] or "web"
        elif session_id and "_" in session_id:
            source = session_id.split("_", 1)[0] or "web"
        return ToolExecutionContext(session_id=session_id, source=source)

    async def _execute_plan_steps(
        self=None,
        plan: AgentPlan | None = None,
        plan_executor: PlanExecutor | None = None,
        messages: list | None = None,
        plan_trace_store: Any | None = None,
        session_id: str | None = None,
    ) -> str | None:
        """Execute structured plan steps, injecting results into messages.

        Args:
            plan: The structured plan to execute.
            plan_executor: Executor for running plan steps.
            messages: Message list (mutated in place with step results).
            plan_trace_store: Optional PlanTraceStore for persistence.
            session_id: Optional session ID for trace attribution.

        Returns:
            An error string if execution failed, or None if successful.
        """
        if plan is None or plan_executor is None:
            return AgentErrorPresenter().plan_failure("plan execution", "Missing plan or plan executor.")
        if messages is None:
            messages = []

        error_presenter = getattr(self, "_error_presenter", AgentErrorPresenter())
        chat_presenter = getattr(self, "_chat_presenter", AgentChatPresenter())

        plan_result: PlanExecutionResult = await plan_executor.execute(plan)

        # Persist trace if store is available
        if plan_trace_store is not None:
            try:
                await plan_trace_store.save_trace(plan, plan_result, session_id=session_id)
            except Exception as exc:
                logger.warning(f"Failed to save plan trace: {exc}")

        first_failure: PlanExecutionStep | None = None
        for step_result in plan_result.steps:
            if step_result.success:
                messages.append(step_result.result)
            else:
                if first_failure is None:
                    first_failure = step_result
                if step_result.result and isinstance(step_result.result, dict) and "role" in step_result.result:
                    result_msg = step_result.result
                else:
                    import json as _json
                    result_msg = {
                        "role": "tool",
                        "tool_call_id": f"plan_{step_result.step.id}",
                        "name": step_result.step.tool_name,
                        "content": _json.dumps({"error": step_result.error or "Unknown error"}),
                    }
                messages.append(result_msg)

        if first_failure is not None:
            tool_name = first_failure.step.tool_name
            error = first_failure.error or "Unknown error"
            if tool_name == "queue_download":
                return error_presenter.queue_failure(error)
            return error_presenter.plan_failure(tool_name, error)

        # Do not auto-queue batch recommendations here. The search tool may expose
        # batch_recommendation.queue_download_arguments as strong evidence, but the
        # LLM must compare it against the category context, language policy, and
        # user request before choosing queue_download. Keeping the plan executor
        # search-only preserves the small generic toolchain without deterministic
        # hidden downloads.
        return None
