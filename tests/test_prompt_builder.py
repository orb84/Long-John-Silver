"""Tests for the prompt builder."""

from src.ai.prompt_builder import PromptBuilder
from src.core.models import Intent


class TestPromptBuilder:
    def test_default_persona(self):
        builder = PromptBuilder()
        prompt = builder.build_system_prompt(Intent.CHAT)
        assert "Long John Silver" in prompt

    def test_search_intent_includes_tools(self):
        builder = PromptBuilder()
        prompt = builder.build_system_prompt(Intent.SEARCH)
        assert "metadata" in prompt.lower()

    def test_download_intent(self):
        builder = PromptBuilder()
        prompt = builder.build_system_prompt(Intent.DOWNLOAD)
        assert "search_torrents" in prompt

    def test_config_intent(self):
        builder = PromptBuilder()
        prompt = builder.build_system_prompt(Intent.CONFIG)
        assert "configuration" in prompt.lower()

    def test_preferences_included(self):
        builder = PromptBuilder()
        prompt = builder.build_system_prompt(Intent.CHAT, preferences_summary="- Likes: Action, Sci-Fi")
        assert "Action" in prompt

    def test_category_guidance_included(self):
        builder = PromptBuilder()
        prompt = builder.build_system_prompt(
            Intent.CHAT,
            category_guidance="ACTIVE MEDIA CATEGORY: TV Show\nINSTRUCTIONS: Custom TV guidance"
        )
        assert "Custom TV guidance" in prompt
