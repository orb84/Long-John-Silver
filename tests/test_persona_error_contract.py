"""Tests for persona-aware user-facing prompts and deterministic errors."""

from src.ai.error_presenter import AgentErrorPresenter
from src.ai.intent_router import ClarificationBuilder
from src.ai.prompt_builder import PromptBuilder
from src.core.models import Intent


class TestPersonaPromptContract:
    """Verify persona guidance is present in user-facing prompts."""

    def test_system_prompt_loads_persona_file(self) -> None:
        """The final assistant prompt must include the active persona text."""
        prompt = PromptBuilder("default").build_system_prompt(Intent.CHAT)
        assert "Long John Silver" in prompt
        assert "Address the user as \"Captain\"" in prompt

    def test_system_prompt_includes_error_contract(self) -> None:
        """The LLM must receive explicit guidance for persona-styled errors."""
        prompt = PromptBuilder("default").build_system_prompt(Intent.DOWNLOAD)
        assert "USER-FACING VOICE CONTRACT" in prompt
        assert "⚠️ **Error" in prompt
        assert "technical detail" in prompt


class TestPersonaErrorPresenter:
    """Verify deterministic errors are clear, useful, and in character."""

    def test_plan_failure_has_error_marker_and_detail(self) -> None:
        """Plan failures should preserve exact tool and error information."""
        message = AgentErrorPresenter("default").plan_failure(
            "search_media_torrents", "invalid literal for int()"
        )
        assert message.startswith("⚠️ **Error")
        assert "Captain" in message
        assert "search_media_torrents" in message
        assert "invalid literal for int()" in message

    def test_circuit_open_keeps_recovery_hint(self) -> None:
        """Circuit-breaker messages should tell the user what happened next."""
        message = AgentErrorPresenter("default").circuit_open("AI completion")
        assert "⚠️ **Error" in message
        assert "AI completion" in message
        assert "try again in a minute" in message.lower()

    def test_clarification_keeps_persona_address(self) -> None:
        """Clarification text should not fall back to a sterile bot voice."""
        message = ClarificationBuilder.build("download", Intent.DOWNLOAD)
        assert message.startswith("Captain")
