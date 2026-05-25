"""
Tests for AIAssistant platform formatting guidance injection.
"""

from unittest.mock import AsyncMock, MagicMock
import pytest

from src.ai.assistant import AIAssistant, AgentDependencies
from src.core.models import Settings, LLMConfig, Intent
from src.ai.intent_router import IntentRouter
from src.web.comms import CommsRegistry, CommsBridge


class CustomMockBridge(CommsBridge):
    name = "custom"
    display_name = "Custom Platform"
    
    @classmethod
    def get_formatting_instructions(cls) -> str:
        return "CUSTOM SYSTEM INSTRUCTIONS FOR FORMATTING"


class TestAssistantPlatformFormatting:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("session_id,expected_guidance", [
        ("discord_channel_123", "FORMATTING RULES FOR DISCORD"),
        ("telegram_chat_987", "FORMATTING RULES FOR TELEGRAM"),
        ("whatsapp_phone_555", "FORMATTING RULES FOR WHATSAPP"),
        ("web_session_abc", "FORMATTING RULES FOR WEB UI"),
    ])
    async def test_assistant_platform_fallback_guidance(self, session_id, expected_guidance):
        """Verify fallback prompt instructions when no comms registry is present."""
        settings = Settings(llm=LLMConfig(), tracked_items=[])
        prefs = MagicMock()
        prefs.get_summary = AsyncMock(return_value="Likes Action")

        deps = AgentDependencies(
            settings=settings,
            preference_manager=prefs,
            intent_router=MagicMock(spec=IntentRouter),
            comms_registry=None
        )
        deps.intent_router.route = AsyncMock(return_value=Intent.CHAT)

        assistant = AIAssistant(deps)
        assistant._conversation_binding = MagicMock()
        assistant._conversation_binding.build_context_messages = AsyncMock(return_value=[])
        assistant._conversation_binding.record_turn = AsyncMock()

        ctx = await assistant._prepare_execution_context(
            user_prompt="Hello, who are you?",
            session_id=session_id,
            user_id="user1"
        )

        system_prompt = ctx.messages[0]["content"]
        assert expected_guidance in system_prompt

    @pytest.mark.asyncio
    async def test_assistant_platform_registry_guidance(self):
        """Verify dynamic prompt instructions retrieved from registered comms registry."""
        settings = Settings(llm=LLMConfig(), tracked_items=[])
        prefs = MagicMock()
        prefs.get_summary = AsyncMock(return_value="Likes Action")

        # Create registry and register our custom bridge
        registry = CommsRegistry()
        registry.register(
            bridge_id="custom",
            factory=CustomMockBridge,
            display_name="Custom Platform"
        )

        deps = AgentDependencies(
            settings=settings,
            preference_manager=prefs,
            intent_router=MagicMock(spec=IntentRouter),
            comms_registry=registry
        )
        deps.intent_router.route = AsyncMock(return_value=Intent.CHAT)

        assistant = AIAssistant(deps)
        assistant._conversation_binding = MagicMock()
        assistant._conversation_binding.build_context_messages = AsyncMock(return_value=[])
        assistant._conversation_binding.record_turn = AsyncMock()

        ctx = await assistant._prepare_execution_context(
            user_prompt="Hello, who are you?",
            session_id="custom_session_123",
            user_id="user1"
        )

        system_prompt = ctx.messages[0]["content"]
        assert "CUSTOM SYSTEM INSTRUCTIONS FOR FORMATTING" in system_prompt
