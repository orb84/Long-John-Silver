"""
Tests for AIAssistant category prompt guidance injection.

Verifies that the assistant correctly loads category-specific guidance,
isolating it when a category is referenced or falling back to listing all
categories' guidance when none is isolated.
"""

from unittest.mock import AsyncMock, MagicMock
import pytest

from src.ai.assistant import AIAssistant, AgentDependencies
from src.core.categories.registry import CategoryRegistry
from src.core.models import Settings, LLMConfig, TvShowItem, Intent
from src.ai.intent_router import IntentRouter


class TestAssistantCategoryGuidance:
    @pytest.mark.asyncio
    async def test_assistant_no_category_isolation_lists_all(self):
        """When the prompt does not isolate a category, include guidance for all categories."""
        settings = Settings(llm=LLMConfig(), tracked_items=[])
        prefs = MagicMock()
        prefs.get_summary = AsyncMock(return_value="Likes Action")

        cat_registry = CategoryRegistry.with_defaults()

        deps = AgentDependencies(
            settings=settings,
            preference_manager=prefs,
            category_registry=cat_registry,
            intent_router=MagicMock(spec=IntentRouter)
        )
        deps.intent_router.route = AsyncMock(return_value=Intent.SEARCH)

        assistant = AIAssistant(deps)

        # Mock conversation binding to not hit DB
        assistant._conversation_binding = MagicMock()
        assistant._conversation_binding.build_context_messages = AsyncMock(return_value=[])
        assistant._conversation_binding.record_turn = AsyncMock()

        ctx = await assistant._prepare_execution_context(
            user_prompt="Hello assistant, can you search for some movies or shows?",
            session_id="session1",
            user_id="user1"
        )

        system_prompt = ctx.messages[0]["content"]

        # Ambiguous category prompts should receive compact router-brief guidance.
        assert "CATEGORY-SCOPED GUIDANCE:" in system_prompt
        assert "router briefs" in system_prompt

    @pytest.mark.asyncio
    async def test_assistant_category_isolation_by_tracked_item(self):
        """When a tracked item name is in the user prompt, isolate that category."""
        # Dexter is tracked as a tv show
        dexter = TvShowItem(key="Dexter", item_type="tv")
        settings = Settings(llm=LLMConfig(), tracked_items=[dexter])
        
        prefs = MagicMock()
        prefs.get_summary = AsyncMock(return_value="Likes Action")

        cat_registry = CategoryRegistry.with_defaults()

        deps = AgentDependencies(
            settings=settings,
            preference_manager=prefs,
            category_registry=cat_registry,
            intent_router=MagicMock(spec=IntentRouter)
        )
        deps.intent_router.route = AsyncMock(return_value=Intent.SEARCH)

        assistant = AIAssistant(deps)

        # Mock conversation binding to not hit DB
        assistant._conversation_binding = MagicMock()
        assistant._conversation_binding.build_context_messages = AsyncMock(return_value=[])
        assistant._conversation_binding.record_turn = AsyncMock()

        ctx = await assistant._prepare_execution_context(
            user_prompt="Search for Dexter season 1",
            session_id="session1",
            user_id="user1"
        )

        system_prompt = ctx.messages[0]["content"]

        # Only tv category instructions should be active
        assert "ACTIVE CATEGORY: TV Shows (tv)" in system_prompt
        # Movie category instructions should NOT be listed/appended
        assert "router briefs" not in system_prompt
        assert "Movies (movie)" not in system_prompt
