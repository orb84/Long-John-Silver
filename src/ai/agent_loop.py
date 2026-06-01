"""
Agent loop executor for LJS.

Runs the non-streaming agentic tool loop previously embedded in
AIAssistant.run(). Delegates tool execution to ToolCallExecutor,
reflection to ReasoningPlanner, and deterministic plan execution
to PlanExecutor.
"""

from typing import Any, Optional, Protocol

from loguru import logger

from src.ai.agent_loop_state import (
    MIN_TOOL_RESULTS_BEFORE_REFLECT,
    MIN_ITERATIONS_BETWEEN_REFLECTIONS,
)
from src.ai.tool_executor import ToolCallExecutor
from src.ai.reasoning import ReasoningPlanner
from src.ai.plan_executor import PlanExecutor
from src.core.models import AgentPlan, PlanExecutionResult, AgentLoopState, PlanExecutionStep, ToolExecutionContext
from src.utils.circuit_breaker import CircuitOpenError
from src.ai.error_presenter import AgentErrorPresenter
from src.ai.bare_tool_call import BareToolCallDetector


class LLMCompletionFn(Protocol):
    """Protocol for LLM completion calls through the circuit breaker."""

    async def __call__(self, *, task: str, messages: list,
                       tools: Any, **kwargs: Any) -> Any: ...


class AgentLoopResult:
    """Result of a non-streaming agent loop execution.

    Attributes:
        response: The final response string from the LLM.
        loop_state: The internal loop state (tool results, reflection flag).
        tool_results_count: Number of tool results accumulated during the loop.
    """

    def __init__(self, response: str, loop_state: AgentLoopState,
                 tool_results_count: int) -> None:
        self.response = response
        self.loop_state = loop_state
        self.tool_results_count = tool_results_count


class AgentLoopExecutor:
    """Executes the non-streaming agentic tool loop.

    Responsibilities:
    - Call LLM completion repeatedly until final response or iteration limit.
    - Process tool calls via ToolCallExecutor.
    - Handle reflection (plan-execute-reflect cycle).
    - Return the final response string with execution metadata.

    The executor is stateless between calls — all mutable state
    is managed within a single execute() invocation.
    """

    def __init__(
        self,
        tool_executor: ToolCallExecutor,
        llm_completion: LLMCompletionFn,
        config: Optional[dict] = None,
        error_presenter: AgentErrorPresenter | None = None,
    ) -> None:
        """Initialize the agent loop executor.

        Args:
            tool_executor: Executor for tool calls (validate, parse, execute).
            llm_completion: Async callable wrapping LLM completion plus
                circuit breaker. Signature matches LLMCompletionFn protocol.
            config: Optional overrides for loop behavior constants. Keys:
                - min_tool_results_before_reflect (default: 1)
                - min_iterations_between_reflections (default: 2)
            error_presenter: Persona-aware formatter for deterministic user-visible errors.
        """
        self._tool_executor = tool_executor
        self._llm_completion = llm_completion
        self._config = config or {}
        self._error_presenter = error_presenter or AgentErrorPresenter()

    async def execute(
        self,
        messages: list,
        tool_definitions: list | None,
        allowed_tool_names: set[str],
        max_iterations: int,
        task: str,
        generation_options: dict | None = None,
        planner: Optional[ReasoningPlanner] = None,
        user_prompt: str = "",
        should_reflect: bool = False,
        fallback_message: str = "",
        plan: Optional[AgentPlan] = None,
        plan_executor: Optional[PlanExecutor] = None,
        plan_trace_store: Optional[Any] = None,
        session_id: str | None = None,
    ) -> AgentLoopResult:
        """Execute the agentic tool loop.

        Args:
            messages: The message list (mutated in place with assistant
                responses and tool results appended during execution).
            tool_definitions: OpenAI-format tool definitions, or None
                when tools should be hidden from the model.
            allowed_tool_names: Set of tool names allowed for this intent.
            max_iterations: Maximum loop iterations before forced fallback.
            task: Task name for LLM config routing (e.g. 'chat', 'search').
            generation_options: Optional max_tokens/temperature overrides.
            planner: Optional ReasoningPlanner for the reflect step.
            user_prompt: Original user prompt (passed to reflection).
            should_reflect: Whether to run reflection after tool results.
            fallback_message: Message returned when max iterations reached
                without a conclusive response.
            plan: Optional structured AgentPlan to execute before the
                agentic loop. Steps are run deterministically and their
                results injected into the message context.
            plan_executor: Optional PlanExecutor for executing the
                structured plan. Required if plan is provided.
            plan_trace_store: Optional PlanTraceStore to persist
                plan execution traces after execution.
            session_id: Optional session ID for trace attribution.

        Returns:
            AgentLoopResult with final response and execution metadata.
        """
        if generation_options is None:
            generation_options = {}

        loop_state = AgentLoopState()

        # Execute structured plan steps before the agentic loop if provided
        if plan and plan.steps and plan_executor:
            plan_result = await self._execute_plan_steps(
                plan, plan_executor, messages, loop_state,
                plan_trace_store=plan_trace_store,
                session_id=session_id,
            )
            if plan_result is not None:
                return plan_result

        last_reflection_iteration = -self._config.get(
            "min_iterations_between_reflections",
            MIN_ITERATIONS_BETWEEN_REFLECTIONS,
        )
        final_response = fallback_message or self._error_presenter.iteration_limit()

        for i in range(max_iterations):
            try:
                # Suppress tools when reflection says SUFFICIENT
                current_tools = tool_definitions
                if loop_state.force_final_response:
                    current_tools = None

                response = await self._llm_completion(
                    task=task,
                    messages=messages,
                    tools=current_tools,
                    **generation_options,
                )

                # Handle both object-style and dict-style LLM responses
                tool_calls, content_text = self._parse_llm_response(response)

                if not tool_calls:
                    recovered = BareToolCallDetector.from_text(content_text, allowed_tool_names)
                    if recovered is not None:
                        logger.warning(
                            "Recovered bare JSON assistant output as tool call: {}",
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
                        result_message, result_summary = await self._tool_executor.execute_tool_call(
                            name=recovered.name,
                            arguments_raw=recovered.arguments,
                            tool_call_id=recovered.call_id,
                            allowed_tool_names=allowed_tool_names,
                            tool_context=self._tool_context(session_id),
                        )
                        loop_state.tool_results.append(result_summary)
                        messages.append(result_message)
                        continue
                    final_response = content_text
                    break

                # Append assistant message with tool calls
                self._append_assistant_message(messages, response)

                # Execute each tool call
                for tool_call in tool_calls:
                    function_name = tool_call.function.name
                    function_args = tool_call.function.arguments
                    tool_call_id = tool_call.id

                    result_message, result_summary = (
                        await self._tool_executor.execute_tool_call(
                            name=function_name,
                            arguments_raw=function_args,
                            tool_call_id=tool_call_id,
                            allowed_tool_names=allowed_tool_names,
                            tool_context=self._tool_context(session_id),
                        )
                    )
                    loop_state.tool_results.append(result_summary)
                    messages.append(result_message)

                # Reflect after tool results for eligible intents
                if should_reflect and self._should_reflect_now(
                    loop_state, i, last_reflection_iteration,
                ) and planner is not None:
                    reflection = await planner.reflect(
                        user_prompt, loop_state.tool_results, task=task,
                    )
                    last_reflection_iteration = i

                    if reflection and reflection.upper().startswith("SUFFICIENT"):
                        logger.info(
                            f"Reflection determined SUFFICIENT after "
                            f"{len(loop_state.tool_results)} tool results"
                        )
                        loop_state.force_final_response = True

            except CircuitOpenError:
                logger.warning("LLM circuit breaker is OPEN — service unavailable")
                final_response = self._error_presenter.circuit_open("AI completion")
                break
            except Exception as e:
                logger.error(f"Agent execution error (iteration {i}): {e}")
                final_response = self._error_presenter.exception("agent execution", e)
                break

        return AgentLoopResult(
            response=final_response,
            loop_state=loop_state,
            tool_results_count=len(loop_state.tool_results),
        )

    @staticmethod
    def _tool_context(session_id: str | None, *, active_category_id: str | None = None) -> ToolExecutionContext:
        """Build lightweight invocation context for declarative tools."""
        source = "web"
        if session_id and ":" in session_id:
            source = session_id.split(":", 1)[0] or "web"
        elif session_id and "_" in session_id:
            source = session_id.split("_", 1)[0] or "web"
        return ToolExecutionContext(session_id=session_id, source=source, category_id=active_category_id)

    async def _execute_plan_steps(
        self,
        plan: AgentPlan,
        plan_executor: PlanExecutor,
        messages: list,
        loop_state: AgentLoopState,
        plan_trace_store: Optional[Any] = None,
        session_id: str | None = None,
    ) -> Optional[AgentLoopResult]:
        """Execute structured plan steps, injecting results into messages.

        Args:
            plan: The structured plan to execute.
            plan_executor: Executor for running plan steps.
            messages: Message list (mutated in place with step results).
            loop_state: Loop state (mutated with step summaries).
            plan_trace_store: Optional PlanTraceStore for persistence.
            session_id: Optional session ID for trace attribution.

        Returns:
            An AgentLoopResult if execution failed, or None if successful.
        """
        plan_result: PlanExecutionResult = await plan_executor.execute(plan)

        # Persist trace if store is available
        if plan_trace_store is not None:
            try:
                await plan_trace_store.save_trace(plan, plan_result, session_id=session_id)
            except Exception as exc:
                logger.warning(f"Failed to save plan trace: {exc}")

        # Inject step results into messages
        first_failure: PlanExecutionStep | None = None
        for step_result in plan_result.steps:
            if step_result.success:
                messages.append(step_result.result)
                if step_result.summary:
                    loop_state.tool_results.append(step_result.summary)
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
                summary = step_result.summary or f"{step_result.step.tool_name} failed: {step_result.error}"
                loop_state.tool_results.append(summary)

        if first_failure is not None:
            tool_name = first_failure.step.tool_name
            error = first_failure.error or "Unknown error"
            logger.warning(
                "Structured plan step '{}' failed before the agent loop: {}. "
                "Continuing with normal tool calling instead of ending the turn.",
                tool_name,
                error,
            )
            messages.append({
                "role": "system",
                "content": (
                    "The pre-generated structured plan failed on tool "
                    f"{tool_name!r}: {error}. Do not repeat the same invalid call. "
                    "Continue using the currently available canonical tools and try the next sensible source."
                ),
            })
            return None

        return None

    @staticmethod
    def _parse_llm_response(response: Any) -> tuple[list, str]:
        """Extract tool_calls and text content from an LLM response.

        Handles both object-style (Pydantic/litellm) and dict-style
        responses for compatibility with different LLM providers.

        Args:
            response: The raw LLM completion response.

        Returns:
            Tuple of (tool_calls_list, content_text_string).
        """
        if hasattr(response, "choices"):
            response_message = response.choices[0].message
            tool_calls = getattr(response_message, "tool_calls", None) or []
            content_text = (
                response_message.content
                if hasattr(response_message, "content")
                else response_message.get("content", "")
            )
            return tool_calls, content_text

        if isinstance(response, dict):
            response_message = response["choices"][0]["message"]
            tool_calls = response_message.get("tool_calls") or []
            content_text = response_message.get("content") or ""
            return tool_calls, content_text

        return [], ""

    @staticmethod
    def _append_assistant_message(messages: list, response: Any) -> None:
        """Append the assistant's response message to the messages list.

        Handles both object-style (model_dump) and dict-style messages.

        Args:
            messages: The message list being built.
            response: The raw LLM completion response.
        """
        if hasattr(response, "choices"):
            response_message = response.choices[0].message
            if hasattr(response_message, "model_dump"):
                messages.append(response_message.model_dump())
        elif isinstance(response, dict):
            response_message = response["choices"][0]["message"]
            # Directly append the message dict if response is dict-style
            messages.append(response_message)

    @staticmethod
    def _should_reflect_now(
        loop_state: AgentLoopState,
        iteration: int,
        last_reflection_iteration: int,
        min_results: int = MIN_TOOL_RESULTS_BEFORE_REFLECT,
        min_iterations: int = MIN_ITERATIONS_BETWEEN_REFLECTIONS,
    ) -> bool:
        """Determine whether reflection should run at this iteration.

        Args:
            loop_state: Current loop state with tool results.
            iteration: Current loop iteration index.
            last_reflection_iteration: Iteration of the last reflection.
            min_results: Minimum tool results before reflection.
            min_iterations: Minimum iterations between reflections.

        Returns:
            True if reflection should run.
        """
        if loop_state.force_final_response:
            return False
        if len(loop_state.tool_results) < min_results:
            return False
        if (iteration - last_reflection_iteration) < min_iterations:
            return False
        return True
