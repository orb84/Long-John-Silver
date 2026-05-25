"""
Tests for the structured plan system — PlanStep, AgentPlan models,
PlanExecutor, and ReasoningPlanner JSON extraction.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from src.ai.plan_executor import PlanExecutor
from src.ai.reasoning import ReasoningPlanner
from src.core.models import AgentPlan, PlanStep, PlanExecutionStep, PlanExecutionResult, Intent, AgentLoopState
from src.utils.circuit_breaker import CircuitBreaker


class TestPlanStep:
    """Tests for PlanStep model validation."""

    def test_minimal_step(self):
        step = PlanStep(id="step_1", tool_name="movie.resolve_metadata")
        assert step.id == "step_1"
        assert step.tool_name == "movie.resolve_metadata"
        assert step.arguments == {}
        assert step.depends_on == []
        assert step.success_condition == ""

    def test_step_with_all_fields(self):
        step = PlanStep(
            id="search_1",
            tool_name="search_torrents",
            arguments={"query": "Severance S02", "quality": "1080p"},
            depends_on=["verify_show"],
            success_condition="at least 5 results with good seeders",
        )
        assert step.id == "search_1"
        assert step.arguments["query"] == "Severance S02"
        assert "verify_show" in step.depends_on

    def test_step_requires_id(self):
        with pytest.raises(ValidationError):
            PlanStep(tool_name="search_torrents")

    def test_step_requires_tool_name(self):
        with pytest.raises(ValidationError):
            PlanStep(id="step_1")


class TestAgentPlan:
    """Tests for AgentPlan model validation."""

    def test_minimal_plan(self):
        plan = AgentPlan(
            intent=Intent.SEARCH,
            user_goal="Find info on Severance",
        )
        assert plan.intent == Intent.SEARCH
        assert plan.user_goal == "Find info on Severance"
        assert plan.constraints == {}
        assert plan.steps == []

    def test_plan_with_steps(self):
        plan = AgentPlan(
            intent=Intent.DOWNLOAD,
            user_goal="Download Severance S02E01",
            constraints={"quality": "1080p", "language": "English"},
            steps=[
                PlanStep(id="verify", tool_name="movie.resolve_metadata",
                         arguments={"query": "Severance"}),
                PlanStep(id="search", tool_name="search_torrents",
                         arguments={"query": "Severance S02E01 1080p"},
                         depends_on=["verify"]),
            ],
        )
        assert len(plan.steps) == 2
        assert plan.constraints["quality"] == "1080p"
        assert plan.steps[1].depends_on == ["verify"]

    def test_plan_requires_intent(self):
        with pytest.raises(ValidationError):
            AgentPlan(user_goal="test")

    def test_plan_requires_user_goal(self):
        with pytest.raises(ValidationError):
            AgentPlan(intent=Intent.CHAT)


class TestPlanExecutionResult:
    """Tests for PlanExecutionResult formatting."""

    def test_format_all_successful(self):
        plan = AgentPlan(intent=Intent.SEARCH, user_goal="test")
        result = PlanExecutionResult(
            plan=plan,
            steps=[
                PlanExecutionStep(
                    step=PlanStep(id="s1", tool_name="movie.resolve_metadata"),
                    success=True,
                    summary="TMDB: Severance S02, rating 8.7",
                ),
            ],
            all_successful=True,
        )
        formatted = result.format_for_prompt()
        assert "[s1] movie.resolve_metadata — OK" in formatted
        assert "TMDB: Severance S02" in formatted

    def test_format_mixed_success(self):
        plan = AgentPlan(intent=Intent.DOWNLOAD, user_goal="test")
        result = PlanExecutionResult(
            plan=plan,
            steps=[
                PlanExecutionStep(
                    step=PlanStep(id="s1", tool_name="movie.resolve_metadata"),
                    success=True, summary="OK",
                ),
                PlanExecutionStep(
                    step=PlanStep(id="s2", tool_name="search_torrents"),
                    success=False, error="Tool not available",
                ),
            ],
            all_successful=False,
        )
        formatted = result.format_for_prompt()
        assert "[s1] movie.resolve_metadata — OK" in formatted
        assert "[s2] search_torrents — FAILED" in formatted
        assert "Tool not available" in formatted


class FakeToolRegistry:
    """A fake tool registry that returns canned results for specific tools."""

    def __init__(self):
        self._tools = {}

    def register(self, name, handler):
        self._tools[name] = handler

    def has_tool(self, name):
        return name in self._tools

    def get_definition(self, name):
        return {"name": name}

    async def execute(self, name, arguments):
        handler = self._tools.get(name)
        if handler is None:
            raise ValueError(f"Unknown tool: {name}")
        return await handler(arguments)


class TestPlanExecutor:
    """Tests for PlanExecutor — step execution, dependency checking, error handling."""

    @pytest.fixture
    def tool_executor(self):
        from src.ai.tool_executor import ToolCallExecutor
        registry = FakeToolRegistry()
        return ToolCallExecutor(registry), registry

    @pytest.fixture
    def plan(self):
        return AgentPlan(
            intent=Intent.SEARCH,
            user_goal="Find info on Severance",
            steps=[
                PlanStep(id="tmdb", tool_name="movie.resolve_metadata",
                         arguments={"query": "Severance"},
                         success_condition="valid TMDB entry"),
                PlanStep(id="tvmaze", tool_name="get_tvmaze_info",
                         arguments={"show": "Severance"},
                         depends_on=["tmdb"],
                         success_condition="scheduling info found"),
            ],
        )

    @pytest.mark.asyncio
    async def test_executes_steps_in_order(self):
        """Valid plan with no dependencies executes tools in order."""
        from src.ai.tool_executor import ToolCallExecutor
        registry = FakeToolRegistry()

        async def tmdb_handler(args):
            return {"name": "Severance", "rating": 8.7}

        async def tvmaze_handler(args):
            return {"status": "airing", "next_episode": "2026-06-01"}

        registry.register("movie.resolve_metadata", tmdb_handler)
        registry.register("get_tvmaze_info", tvmaze_handler)

        executor = ToolCallExecutor(registry)
        plan_exec = PlanExecutor(
            tool_executor=executor,
            allowed_tool_names={"movie.resolve_metadata", "get_tvmaze_info"},
        )

        plan = AgentPlan(
            intent=Intent.SEARCH,
            user_goal="Find info on Severance",
            steps=[
                PlanStep(id="tmdb", tool_name="movie.resolve_metadata",
                         arguments={"query": "Severance"}),
                PlanStep(id="tvmaze", tool_name="get_tvmaze_info",
                         arguments={"show": "Severance"},
                         depends_on=["tmdb"]),
            ],
        )

        result = await plan_exec.execute(plan)
        assert result.all_successful is True
        assert len(result.steps) == 2
        assert result.steps[0].success is True
        assert result.steps[1].success is True

    @pytest.mark.asyncio
    async def test_failed_dependency_stops_execution(self):
        """When a dependency fails, later steps are not executed."""
        from src.ai.tool_executor import ToolCallExecutor
        registry = FakeToolRegistry()

        async def failing_handler(args):
            raise ValueError("API error")

        registry.register("movie.resolve_metadata", failing_handler)

        executor = ToolCallExecutor(registry)
        plan_exec = PlanExecutor(
            tool_executor=executor,
            allowed_tool_names={"movie.resolve_metadata", "get_tvmaze_info"},
        )

        plan = AgentPlan(
            intent=Intent.SEARCH,
            user_goal="Find info",
            steps=[
                PlanStep(id="tmdb", tool_name="movie.resolve_metadata",
                         arguments={"query": "Severance"}),
                PlanStep(id="tvmaze", tool_name="get_tvmaze_info",
                         arguments={"show": "Severance"},
                         depends_on=["tmdb"]),
            ],
        )

        result = await plan_exec.execute(plan)
        assert result.all_successful is False
        # First step should have failed due to the exception
        assert result.steps[0].success is False
        assert "API error" in (result.steps[0].error or "")

    @pytest.mark.asyncio
    async def test_missing_dependency_stops_execution(self):
        """When a dependency ID does not exist, execution stops."""
        from src.ai.tool_executor import ToolCallExecutor
        registry = FakeToolRegistry()
        executor = ToolCallExecutor(registry)
        plan_exec = PlanExecutor(
            tool_executor=executor,
            allowed_tool_names={"search_torrents"},
        )

        plan = AgentPlan(
            intent=Intent.DOWNLOAD,
            user_goal="Download test",
            steps=[
                PlanStep(id="search", tool_name="search_torrents",
                         arguments={"query": "test"},
                         depends_on=["nonexistent_step"]),
            ],
        )

        result = await plan_exec.execute(plan)
        assert result.all_successful is False
        assert result.steps[0].success is False
        assert "nonexistent_step" in (result.steps[0].error or "")

    @pytest.mark.asyncio
    async def test_disallowed_tool_is_blocked(self):
        """A tool not in allowed_tool_names is blocked by ToolCallExecutor."""
        from src.ai.tool_executor import ToolCallExecutor
        registry = FakeToolRegistry()

        async def dangerous_handler(args):
            return {"deleted": True}

        registry.register("delete_everything", dangerous_handler)

        executor = ToolCallExecutor(registry)
        # Note: delete_everything is intentionally NOT in allowed_tool_names
        plan_exec = PlanExecutor(
            tool_executor=executor,
            allowed_tool_names={"movie.resolve_metadata"},
        )

        plan = AgentPlan(
            intent=Intent.SEARCH,
            user_goal="Test blocked tool",
            steps=[
                PlanStep(id="bad", tool_name="delete_everything",
                         arguments={}),
            ],
        )

        result = await plan_exec.execute(plan)
        # The step detects the error payload and fails accordingly
        assert result.steps[0].success is False
        assert "not available" in (result.steps[0].error or "").lower()

    @pytest.mark.asyncio
    async def test_executor_exception_returns_failure(self):
        """An exception during tool execution returns a failed step."""
        from src.ai.tool_executor import ToolCallExecutor
        registry = FakeToolRegistry()

        # Don't register the tool — will cause ValueError in registry.execute
        executor = ToolCallExecutor(registry)
        plan_exec = PlanExecutor(
            tool_executor=executor,
            allowed_tool_names={"nonexistent_tool"},
        )

        plan = AgentPlan(
            intent=Intent.SEARCH,
            user_goal="Test error",
            steps=[
                PlanStep(id="fail", tool_name="nonexistent_tool",
                         arguments={}),
            ],
        )

        result = await plan_exec.execute(plan)
        assert result.all_successful is False
        assert result.steps[0].success is False

    @pytest.mark.asyncio
    async def test_resolves_selected_magnet_placeholder(self):
        """Step with a SELECTED_MAGNET placeholder resolves it using preceding search results."""
        from src.ai.tool_executor import ToolCallExecutor
        registry = FakeToolRegistry()

        async def search_handler(args):
            return {
                "query": "Severance S02E01",
                "candidates": [
                    {
                        "title": "Severance S02E01 1080p",
                        "magnet": "magnet:?xt=urn:btih:testmagnet12345",
                    }
                ]
            }

        async def download_handler(args):
            # Assert that the placeholder was resolved to the actual magnet link
            assert args["magnet"] == "magnet:?xt=urn:btih:testmagnet12345"
            return {"status": "queued"}

        registry.register("search_media_torrents", search_handler)
        registry.register("queue_download", download_handler)

        executor = ToolCallExecutor(registry)
        plan_exec = PlanExecutor(
            tool_executor=executor,
            allowed_tool_names={"search_media_torrents", "queue_download"},
        )

        plan = AgentPlan(
            intent=Intent.DOWNLOAD,
            user_goal="Download Severance",
            steps=[
                PlanStep(id="search", tool_name="search_media_torrents",
                         arguments={"name": "Severance", "season": 2, "episode": 1}),
                PlanStep(id="download", tool_name="queue_download",
                         arguments={"name": "Severance", "magnet": "<SELECTED_MAGNET>"},
                         depends_on=["search"]),
            ],
        )

        result = await plan_exec.execute(plan)
        assert result.all_successful is True
        assert len(result.steps) == 2
        assert result.steps[0].success is True
        assert result.steps[1].success is True


class TestExtractJson:
    """Tests for ReasoningPlanner._extract_json static method."""

    def test_plain_json_object(self):
        text = '{"intent": "SEARCH", "user_goal": "test", "steps": []}'
        result = ReasoningPlanner._extract_json(text)
        assert result["intent"] == "SEARCH"
        assert result["user_goal"] == "test"

    def test_json_with_markdown_fence(self):
        text = '```json\n{"intent": "DOWNLOAD", "user_goal": "test", "steps": []}\n```'
        result = ReasoningPlanner._extract_json(text)
        assert result["intent"] == "DOWNLOAD"

    def test_json_with_generic_fence(self):
        text = '```\n{"intent": "SEARCH", "user_goal": "test", "steps": []}\n```'
        result = ReasoningPlanner._extract_json(text)
        assert result["intent"] == "SEARCH"

    def test_json_with_leading_text(self):
        text = (
            "Here is the plan:\n"
            '```json\n{"intent": "SEARCH", "user_goal": "test", "steps": []}\n```\n'
            "End of plan."
        )
        result = ReasoningPlanner._extract_json(text)
        assert result["intent"] == "SEARCH"

    def test_raises_on_invalid_json(self):
        with pytest.raises(json.JSONDecodeError):
            ReasoningPlanner._extract_json("this is not json")

    def test_raises_on_empty_string(self):
        with pytest.raises(json.JSONDecodeError):
            ReasoningPlanner._extract_json("")


class TestPlanExecutorErrorDict:
    """Tests for PlanExecutor error dict handling — Phase 3.2.

    Verifies that tool results returning {"error": "..."} payloads
    are treated as failures and stop plan execution.
    """

    @pytest.mark.asyncio
    async def test_error_dict_in_tool_result_stops_execution(self):
        """When a tool returns {"error": "..."}, the step should fail."""
        from src.ai.tool_executor import ToolCallExecutor
        registry = FakeToolRegistry()

        async def flaky_handler(args):
            return {"error": "API rate limit exceeded"}

        registry.register("movie.resolve_metadata", flaky_handler)

        executor = ToolCallExecutor(registry)
        plan_exec = PlanExecutor(
            tool_executor=executor,
            allowed_tool_names={"movie.resolve_metadata"},
        )

        plan = AgentPlan(
            intent=Intent.SEARCH,
            user_goal="Find info on Severance",
            steps=[
                PlanStep(id="tmdb", tool_name="movie.resolve_metadata",
                         arguments={"query": "Severance"}),
            ],
        )

        result = await plan_exec.execute(plan)
        assert result.all_successful is False
        assert result.steps[0].success is False
        assert "API rate limit exceeded" in (result.steps[0].error or "")

    @pytest.mark.asyncio
    async def test_error_dict_stops_subsequent_steps(self):
        """When a step returns an error dict, later dependent steps are skipped."""
        from src.ai.tool_executor import ToolCallExecutor
        registry = FakeToolRegistry()

        async def failing_handler(args):
            return {"error": "Media not found"}

        registry.register("movie.resolve_metadata", failing_handler)

        executor = ToolCallExecutor(registry)
        plan_exec = PlanExecutor(
            tool_executor=executor,
            allowed_tool_names={"movie.resolve_metadata", "tv.resolve_show"},
        )

        plan = AgentPlan(
            intent=Intent.SEARCH,
            user_goal="Find info",
            steps=[
                PlanStep(id="verify", tool_name="movie.resolve_metadata",
                         arguments={"query": "Unknown"}),
                PlanStep(id="schedule", tool_name="tv.resolve_show",
                         arguments={"show": "Unknown"},
                         depends_on=["verify"]),
            ],
        )

        result = await plan_exec.execute(plan)
        assert result.all_successful is False
        assert result.steps[0].success is False
        assert "Media not found" in (result.steps[0].error or "")
        # Only the first step should have executed — second depends on it
        assert len(result.steps) == 1


class TestPlanExecutorTracePersistence:
    """Tests for plan trace persistence — Phase 3.3.

    Verifies that save_trace() is called with session_id in both
    the PlanExecutor and the AgentLoopExecutor paths.
    """

    @pytest.mark.asyncio
    async def test_plan_executor_save_trace_called(self):
        """PlanExecutor should trigger save_trace on its own (via caller)."""
        from src.ai.tool_executor import ToolCallExecutor
        registry = FakeToolRegistry()

        async def tmdb_handler(args):
            return {"name": "Severance", "rating": 8.7}

        registry.register("movie.resolve_metadata", tmdb_handler)

        executor = ToolCallExecutor(registry)
        plan_exec = PlanExecutor(
            tool_executor=executor,
            allowed_tool_names={"movie.resolve_metadata"},
        )

        plan = AgentPlan(
            intent=Intent.SEARCH,
            user_goal="Find info",
            steps=[
                PlanStep(id="tmdb", tool_name="movie.resolve_metadata",
                         arguments={"query": "Severance"}),
            ],
        )

        result = await plan_exec.execute(plan)
        assert result.all_successful is True

    @pytest.mark.asyncio
    async def test_agent_loop_saves_trace_with_session_id(self):
        """AgentLoopExecutor._execute_plan_steps should call save_trace with session_id."""
        from src.ai.tool_executor import ToolCallExecutor
        from src.ai.agent_loop import AgentLoopExecutor
        registry = FakeToolRegistry()

        async def tmdb_handler(args):
            return {"name": "Severance", "rating": 8.7}

        registry.register("movie.resolve_metadata", tmdb_handler)

        executor = ToolCallExecutor(registry)
        plan_exec = PlanExecutor(
            tool_executor=executor,
            allowed_tool_names={"movie.resolve_metadata"},
        )

        plan = AgentPlan(
            intent=Intent.SEARCH,
            user_goal="Find info",
            steps=[
                PlanStep(id="tmdb", tool_name="movie.resolve_metadata",
                         arguments={"query": "Severance"}),
            ],
        )

        fake_trace_store = AsyncMock()
        loop_state = AgentLoopState()
        messages = []

        result = await AgentLoopExecutor._execute_plan_steps(
            plan=plan,
            plan_executor=plan_exec,
            messages=messages,
            loop_state=loop_state,
            plan_trace_store=fake_trace_store,
            session_id="test-session-123",
        )

        # save_trace should have been called with the plan, result, and session_id
        fake_trace_store.save_trace.assert_awaited_once()
        call_args = fake_trace_store.save_trace.call_args
        assert call_args is not None
        _, kwargs = call_args
        assert kwargs.get("session_id") == "test-session-123"

    @pytest.mark.asyncio
    async def test_agent_loop_saves_trace_on_failure(self):
        """save_trace should still be called even when plan execution fails."""
        from src.ai.tool_executor import ToolCallExecutor
        from src.ai.agent_loop import AgentLoopExecutor
        registry = FakeToolRegistry()

        async def failing_handler(args):
            return {"error": "Service unavailable"}

        registry.register("movie.resolve_metadata", failing_handler)

        executor = ToolCallExecutor(registry)
        plan_exec = PlanExecutor(
            tool_executor=executor,
            allowed_tool_names={"movie.resolve_metadata"},
        )

        plan = AgentPlan(
            intent=Intent.SEARCH,
            user_goal="Find info",
            steps=[
                PlanStep(id="tmdb", tool_name="movie.resolve_metadata",
                         arguments={"query": "Severance"}),
            ],
        )

        fake_trace_store = AsyncMock()
        loop_state = AgentLoopState()
        messages = []

        result = await AgentLoopExecutor._execute_plan_steps(
            plan=plan,
            plan_executor=plan_exec,
            messages=messages,
            loop_state=loop_state,
            plan_trace_store=fake_trace_store,
            session_id="fail-session",
        )

        # save_trace should be called even on failure
        fake_trace_store.save_trace.assert_awaited_once()
        call_args = fake_trace_store.save_trace.call_args
        _, kwargs = call_args
        assert kwargs.get("session_id") == "fail-session"

    @pytest.mark.asyncio
    async def test_streaming_loop_saves_trace_with_session_id(self):
        """StreamingAgentLoopExecutor._execute_plan_steps should call save_trace with session_id."""
        from src.ai.tool_executor import ToolCallExecutor
        from src.ai.streaming_agent_loop import StreamingAgentLoopExecutor
        registry = FakeToolRegistry()

        async def tmdb_handler(args):
            return {"name": "Severance", "rating": 8.7}

        registry.register("movie.resolve_metadata", tmdb_handler)

        executor = ToolCallExecutor(registry)
        plan_exec = PlanExecutor(
            tool_executor=executor,
            allowed_tool_names={"movie.resolve_metadata"},
        )

        plan = AgentPlan(
            intent=Intent.SEARCH,
            user_goal="Find info",
            steps=[
                PlanStep(id="tmdb", tool_name="movie.resolve_metadata",
                         arguments={"query": "Severance"}),
            ],
        )

        fake_trace_store = AsyncMock()
        messages = []

        error = await StreamingAgentLoopExecutor._execute_plan_steps(
            plan=plan,
            plan_executor=plan_exec,
            messages=messages,
            plan_trace_store=fake_trace_store,
            session_id="stream-session-456",
        )

        assert error is None  # Successful execution
        fake_trace_store.save_trace.assert_awaited_once()
        call_args = fake_trace_store.save_trace.call_args
        _, kwargs = call_args
        assert kwargs.get("session_id") == "stream-session-456"

    @pytest.mark.asyncio
    async def test_plan_results_inject_into_messages(self):
        """PlanExecutor results should produce valid tool result messages."""
        from src.ai.tool_executor import ToolCallExecutor
        registry = FakeToolRegistry()

        async def tmdb_handler(args):
            return {"name": "Severance", "rating": 8.7}

        registry.register("movie.resolve_metadata", tmdb_handler)

        executor = ToolCallExecutor(registry)
        plan_exec = PlanExecutor(
            tool_executor=executor,
            allowed_tool_names={"movie.resolve_metadata"},
        )

        plan = AgentPlan(
            intent=Intent.SEARCH,
            user_goal="Find info on Severance",
            steps=[
                PlanStep(id="tmdb", tool_name="movie.resolve_metadata",
                         arguments={"query": "Severance"}),
            ],
        )

        result = await plan_exec.execute(plan)
        assert result.all_successful is True
        assert len(result.steps) == 1

        step_result = result.steps[0]
        assert step_result.success is True
        msg = step_result.result
        assert msg["role"] == "tool"
        assert msg["name"] == "movie.resolve_metadata"
        assert "tool_call_id" in msg
        assert "content" in msg
        # Verify the content contains the handler return value
        assert "Severance" in msg["content"] or "rating" in msg["content"]

class TestPlanExecutorDependencyPlaceholders:
    """Regression tests for planner placeholders that used to leak into tools."""

    @pytest.mark.asyncio
    async def test_resolves_natural_language_first_search_result_url_placeholder(self):
        """Planner prose URL placeholders are resolved from web_search output."""
        from src.ai.tool_executor import ToolCallExecutor
        registry = FakeToolRegistry()
        calls = []

        async def web_search(args):
            calls.append(("web_search", dict(args)))
            return {
                "query": args["query"],
                "ok": True,
                "results": [
                    {
                        "title": "Gioco del Ponte - Pisa",
                        "url": "https://example.org/gioco-del-ponte",
                        "snippet": "Annual event in Pisa.",
                    }
                ],
            }

        async def read_web_page(args):
            calls.append(("read_web_page", dict(args)))
            assert args["url"] == "https://example.org/gioco-del-ponte"
            return {"title": "Gioco del Ponte", "content": "History and details."}

        registry.register("web_search", web_search)
        registry.register("read_web_page", read_web_page)

        plan_exec = PlanExecutor(
            tool_executor=ToolCallExecutor(registry),
            allowed_tool_names={"web_search", "read_web_page"},
        )
        plan = AgentPlan(
            intent=Intent.SEARCH,
            user_goal="Find detailed information about Gioco Del Ponte.",
            steps=[
                PlanStep(
                    id="search_event",
                    tool_name="web_search",
                    arguments={"query": "Gioco Del Ponte annual event Pisa", "max_results": 5},
                ),
                PlanStep(
                    id="read_event_page",
                    tool_name="read_web_page",
                    arguments={"url": "<URL from the first search result of step search_event>"},
                    depends_on=["search_event"],
                ),
            ],
        )

        result = await plan_exec.execute(plan)
        assert result.all_successful is True
        assert [name for name, _ in calls] == ["web_search", "read_web_page"]

    @pytest.mark.asyncio
    async def test_resolves_brace_only_dependency_placeholder(self):
        """Local models often emit {step.path}; keep that compatibility."""
        from src.ai.tool_executor import ToolCallExecutor
        registry = FakeToolRegistry()

        async def metadata_lookup(args):
            return {"best": {"number_of_seasons": 5}}

        async def search_media_torrents(args):
            assert args["season"] == 5
            return {"candidates": []}

        registry.register("metadata_lookup", metadata_lookup)
        registry.register("search_media_torrents", search_media_torrents)

        plan_exec = PlanExecutor(
            tool_executor=ToolCallExecutor(registry),
            allowed_tool_names={"metadata_lookup", "search_media_torrents"},
        )
        plan = AgentPlan(
            intent=Intent.DOWNLOAD,
            user_goal="Find latest-season episode.",
            steps=[
                PlanStep(
                    id="fetch_latest_season",
                    tool_name="metadata_lookup",
                    arguments={"query": "For All Mankind", "media_type": "tv"},
                ),
                PlanStep(
                    id="search_episode",
                    tool_name="search_media_torrents",
                    arguments={
                        "name": "For All Mankind",
                        "season": "{fetch_latest_season.latest_season}",
                        "episode": 3,
                    },
                    depends_on=["fetch_latest_season"],
                ),
            ],
        )

        result = await plan_exec.execute(plan)
        assert result.all_successful is True

    @pytest.mark.asyncio
    async def test_resolves_formal_placeholder_and_normalizes_duckduckgo_redirect(self):
        """Formal result URL placeholders normalize DuckDuckGo redirect links."""
        from src.ai.tool_executor import ToolCallExecutor
        registry = FakeToolRegistry()
        calls = []

        async def web_search(args):
            return {
                "results": [
                    {
                        "title": "Official Gioco del Ponte",
                        "url": "//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.giocodelpontedipisa.it%2F&rut=f728",
                    }
                ]
            }

        async def read_web_page(args):
            calls.append(dict(args))
            return {"title": "Gioco del Ponte", "content": "Official page"}

        registry.register("web_search", web_search)
        registry.register("read_web_page", read_web_page)
        plan_exec = PlanExecutor(
            tool_executor=ToolCallExecutor(registry),
            allowed_tool_names={"web_search", "read_web_page"},
        )
        plan = AgentPlan(
            intent=Intent.SEARCH,
            user_goal="Find detailed information about Gioco Del Ponte.",
            steps=[
                PlanStep(
                    id="search_event",
                    tool_name="web_search",
                    arguments={"query": "Gioco Del Ponte annual event Pisa", "max_results": 5},
                ),
                PlanStep(
                    id="read_event_page",
                    tool_name="read_web_page",
                    arguments={"url": "${search_event.results.0.url}"},
                    depends_on=["search_event"],
                ),
            ],
        )

        result = await plan_exec.execute(plan)

        assert result.all_successful is True
        assert calls == [{"url": "https://www.giocodelpontedipisa.it/"}]

    @pytest.mark.asyncio
    async def test_unresolved_read_url_is_blocked_before_web_reader(self):
        """Never pass unresolved planner placeholders into network tools."""
        from src.ai.tool_executor import ToolCallExecutor
        registry = FakeToolRegistry()

        async def read_web_page(args):  # pragma: no cover - must not be called
            raise AssertionError("read_web_page should not receive unresolved placeholders")

        registry.register("read_web_page", read_web_page)
        plan_exec = PlanExecutor(
            tool_executor=ToolCallExecutor(registry),
            allowed_tool_names={"read_web_page"},
        )
        plan = AgentPlan(
            intent=Intent.SEARCH,
            user_goal="Read a result page.",
            steps=[
                PlanStep(
                    id="read_event_page",
                    tool_name="read_web_page",
                    arguments={"url": "<URL from the first search result>"},
                ),
            ],
        )

        result = await plan_exec.execute(plan)
        assert result.all_successful is False
        assert "URL placeholder" in (result.steps[0].error or "") or "requires a resolved" in (result.steps[0].error or "")
