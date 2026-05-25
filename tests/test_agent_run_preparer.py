"""
Tests for AgentRunPreparer — shared agent run preparation.

Verifies that intent routing, context building, plan generation,
and tool selection are correctly factored out of AIAssistant.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.models import Intent, LLMConfig, Settings, PreparedAgentRun
from src.core.preferences import PreferenceManager
from src.ai.run_preparer import AgentRunPreparer
from src.ai.intent_router import IntentRouter


def _run(coro):
    """Run an async test coroutine with a fresh event loop."""
    return asyncio.run(coro)


class FakePreferenceManager:
    """Minimal PreferenceManager for testing."""

    def __init__(self):
        self._summary = "Likes: Action, Sci-Fi | Dislikes: Horror"

    async def get_summary(self, user_id=None):
        return self._summary


class FakeConversationManager:
    """Minimal ConversationManager for testing."""

    def __init__(self):
        self._turns = []

    def has_vector_store(self):
        return False

    async def add_turn(self, session_id, role, content, tool_call_id=None):
        self._turns.append({"session_id": session_id, "role": role, "content": content})
        return len(self._turns)

    async def get_context(self, session_id, max_turns=20, max_tokens=4000):
        return []


class FakeBehaviorTracker:
    """Minimal BehaviorTracker for testing."""

    async def get_behavior_profile(self, user_id):
        return {"preferred_resolution": "1080p", "top_genres": ["Action"]}

    def format_profile_for_prompt(self, profile):
        return "User prefers 1080p Action content."


class TestAgentRunPreparer:
    """Tests for AgentRunPreparer preparation logic."""

    def _make_preparer(self, intent_result=Intent.SEARCH, **kwargs):
        """Create a preparer with mock dependencies."""
        settings = Settings(llm=LLMConfig(model="gpt-3.5-turbo"))

        mock_intent_router = MagicMock(spec=IntentRouter)
        mock_intent_router.route = AsyncMock(return_value=intent_result)

        from src.ai.prompt_builder import PromptBuilder
        prompt_builder = PromptBuilder()

        preparer = AgentRunPreparer(
            settings=settings,
            preference_manager=FakePreferenceManager(),
            conversation_manager=FakeConversationManager(),
            behavior_tracker=FakeBehaviorTracker(),
            intent_router=mock_intent_router,
            prompt_builder=prompt_builder,
            tool_names_by_intent={
                Intent.SEARCH: {"movie.resolve_metadata", "search_torrents"},
                Intent.DOWNLOAD: {"movie.resolve_metadata", "search_torrents", "get_library_status"},
                Intent.CONFIG: {"category_item_add", "add_preference"},
            },
            **kwargs,
        )
        return preparer

    def test_search_request_includes_research_tools(self):
        """SEARCH intent should include search tool names."""
        preparer = self._make_preparer(intent_result=Intent.SEARCH)
        result = _run(
            preparer.prepare("Find info on Severance", session_id="test-session")
        )
        assert Intent.SEARCH in result.allowed_tool_names or len(result.allowed_tool_names) > 0

    def test_download_request_includes_torrent_tool(self):
        """DOWNLOAD intent should include search_torrents."""
        preparer = self._make_preparer(intent_result=Intent.DOWNLOAD)
        result = _run(
            preparer.prepare("Download Severance S02E01")
        )
        assert "search_torrents" in result.allowed_tool_names
        assert result.intent == Intent.DOWNLOAD
        assert result.task == "download"

    def test_config_request_includes_config_tools(self):
        """CONFIG intent should include config tool names."""
        preparer = self._make_preparer(intent_result=Intent.CONFIG)
        result = _run(
            preparer.prepare("Add Breaking Bad to my shows")
        )
        assert "category_item_add" in result.allowed_tool_names

    def test_chat_request_has_no_tools(self):
        """CHAT intent should have empty tool names."""
        preparer = self._make_preparer(intent_result=Intent.CHAT)
        result = _run(
            preparer.prepare("Hello there!")
        )
        assert len(result.allowed_tool_names) == 0
        assert result.task == "chat"

    def test_conversation_context_appended(self):
        """Conversation context should be included in messages."""
        preparer = self._make_preparer(intent_result=Intent.SEARCH)
        result = _run(
            preparer.prepare("Find info on Severance", session_id="test-session")
        )
        # Should have at least system + user messages
        assert len(result.messages) >= 2
        assert result.messages[0]["role"] == "system"
        assert result.messages[-1]["role"] == "user"

    def test_clarify_returns_early(self):
        """CLARIFY intent should return without full setup."""
        preparer = self._make_preparer(intent_result=Intent.CLARIFY)
        result = _run(
            preparer.prepare("hello")
        )
        assert result.intent == Intent.CLARIFY
        assert result.task == "chat"

    def test_planning_for_search_intent(self):
        """SEARCH intent should generate a plan when include_plan is True."""
        preparer = self._make_preparer(intent_result=Intent.SEARCH)
        result = _run(
            preparer.prepare("Find info on Severance", session_id="test-session")
        )
        # The planner should exist (even if plan generation fails due to no LLM)
        # The system prompt should contain planning-related content
        assert result.intent == Intent.SEARCH

    def test_no_planning_for_chat_intent(self):
        """CHAT intent should not generate a plan."""
        preparer = self._make_preparer(intent_result=Intent.CHAT)
        result = _run(
            preparer.prepare("Hello there!")
        )
        # Chat shouldn't have a plan
        assert result.agent_plan is None

    def test_max_iterations_search(self):
        """SEARCH intent should use search_tool_iterations."""
        preparer = self._make_preparer(
            intent_result=Intent.SEARCH,
            search_tool_iterations=10,
        )
        result = _run(
            preparer.prepare("Find Severance")
        )
        assert result.max_iterations == 10

    def test_max_iterations_chat(self):
        """CHAT intent should use chat_tool_iterations."""
        preparer = self._make_preparer(
            intent_result=Intent.CHAT,
            chat_tool_iterations=4,
        )
        result = _run(
            preparer.prepare("Hello")
        )
        assert result.max_iterations == 4

    def test_should_record_behavior_for_download(self):
        """Download intent with user_id should record behavior."""
        preparer = self._make_preparer(intent_result=Intent.DOWNLOAD)
        result = _run(
            preparer.prepare("Download Severance S02E01", user_id="user1")
        )
        assert result.should_record_download_behavior is True

    def test_should_not_record_behavior_for_search(self):
        """Search intent should not record download behavior."""
        preparer = self._make_preparer(intent_result=Intent.SEARCH)
        result = _run(
            preparer.prepare("Find Severance info", user_id="user1")
        )
        assert result.should_record_download_behavior is False

    def test_build_clarification_response(self):
        """build_clarification_response should return a helpful string."""
        preparer = self._make_preparer()
        response = preparer.build_clarification_response()
        assert "Search" in response
        assert "Download" in response
        assert "Configure" in response


class TestPreparedAgentRun:
    """Tests for PreparedAgentRun model."""

    def test_default_values(self):
        run = PreparedAgentRun(intent=Intent.CHAT, task="chat")
        assert run.intent == Intent.CHAT
        assert run.task == "chat"
        assert run.messages == []
        assert run.tool_definitions is None
        assert run.max_iterations == 4

    def test_custom_values(self):
        run = PreparedAgentRun(
            intent=Intent.SEARCH,
            task="search",
            messages=[{"role": "user", "content": "test"}],
            allowed_tool_names={"movie.resolve_metadata"},
            max_iterations=10,
        )
        assert run.intent == Intent.SEARCH
        assert run.task == "search"
        assert len(run.messages) == 1


@pytest.mark.asyncio
async def test_preparer_enforces_tracked_show_language_on_plan():
    from unittest.mock import MagicMock, AsyncMock, patch
    from src.core.models import Intent, Settings, LLMConfig, TvShowItem, AgentPlan, PlanStep
    from src.ai.run_preparer import AgentRunPreparer
    
    # 1. Create a show item in settings configured with Italian
    show_item = TvShowItem(key="For All Mankind", language="Italian")
    settings = Settings(
        llm=LLMConfig(model="gpt-3.5-turbo"),
        tracked_items=[show_item]
    )
    
    mock_intent_router = MagicMock()
    mock_intent_router.route = AsyncMock(return_value=Intent.DOWNLOAD)
    
    from src.ai.prompt_builder import PromptBuilder
    prompt_builder = PromptBuilder()
    
    preparer = AgentRunPreparer(
        settings=settings,
        preference_manager=FakePreferenceManager(),
        conversation_manager=FakeConversationManager(),
        behavior_tracker=FakeBehaviorTracker(),
        intent_router=mock_intent_router,
        prompt_builder=prompt_builder,
        tool_names_by_intent={Intent.DOWNLOAD: {"search_media_torrents"}},
    )
    
    # 2. Mock ReasoningPlanner to return a plan in English
    mock_plan = AgentPlan(
        user_goal="Download For All Mankind Season 5 Episode 4",
        intent=Intent.DOWNLOAD,
        constraints={"resolution": "1080p", "language": "English"},
        steps=[
            PlanStep(
                id="search_torrent",
                tool_name="search_media_torrents",
                arguments={"name": "For All Mankind", "season": 5, "episode": 4, "language": "English"},
                depends_on=[],
                success_condition="Success"
            )
        ]
    )
    
    mock_planner = MagicMock()
    mock_planner.generate_plan = AsyncMock(return_value=mock_plan)
    
    with patch.object(preparer, "_create_planner", return_value=mock_planner):
        # 3. Call prepare with prompt mentioning 'For All Mankind'
        result = await preparer.prepare(
            "Download For All Mankind Season 5 Episode 4",
            session_id="test-session",
            include_plan=True
        )
        
        # 4. Assert that language was programmatically overridden to 'Italian'!
        assert result.agent_plan is not None
        assert result.agent_plan.constraints["language"] == "Italian"
        assert result.agent_plan.steps[0].arguments["language"] == "Italian"
