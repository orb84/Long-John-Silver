"""Tests for AI assistant category-aware tool exposure and generation options."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.ai.assistant import AIAssistant, AgentDependencies
from src.ai.tool_registry import ToolRegistry
from src.core.categories.registry import CategoryRegistry
from src.core.models import Intent, Settings, LLMConfig, TaskModelConfig


class FakePreferenceManager:
    """Preference manager stub that returns an empty prompt context."""

    async def get_summary(self, user_id: str | None = None) -> str:
        """Return an empty preference summary."""
        return ""


class FakeLLMClient:
    """Fake LLM client that captures call kwargs and returns a canned response."""

    def __init__(self):
        self.captured = {}

    async def completion(self, *, task, messages, tools=None, stream=False, **kwargs):
        """Capture kwargs and return a final text response."""
        self.captured.update(kwargs)
        if tools is not None:
            self.captured["tools"] = tools
        fake = SimpleNamespace()
        fake.choices = [SimpleNamespace(message=SimpleNamespace(content="done", tool_calls=[]))]
        return fake

    def update_config(self, config):
        """Accept config updates from assistant hot reloads."""


def _make_assistant(settings: Settings) -> AIAssistant:
    """Create an AIAssistant with fake dependencies for testing."""
    registry = ToolRegistry()
    registry.register(
        name="get_category_manifest",
        description="Get category manifest",
        parameters={"type": "object", "properties": {"category_id": {"type": "string"}}, "required": ["category_id"]},
        handler=lambda category_id: {},
        intents={Intent.SEARCH, Intent.DOWNLOAD, Intent.CONFIG, Intent.CHAT},
    )
    registry.register(
        name="get_library_status",
        description="Get library status",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=lambda: {},
        intents={Intent.SEARCH, Intent.DOWNLOAD, Intent.CONFIG, Intent.CHAT},
    )
    registry.register(
        name="web_search",
        description="Search web",
        parameters={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        handler=lambda query: {},
        intents={Intent.SEARCH, Intent.CHAT},
    )
    registry.register(
        name="queue_download",
        description="Queue download",
        parameters={"type": "object", "properties": {"magnet": {"type": "string"}}, "required": ["magnet"]},
        handler=lambda magnet: {},
        intents={Intent.DOWNLOAD},
    )

    return AIAssistant(AgentDependencies(
        settings=settings,
        preference_manager=FakePreferenceManager(),
        tool_registry=registry,
        llm_client=FakeLLMClient(),
        category_registry=CategoryRegistry.with_defaults(),
    ))


@pytest.mark.asyncio
async def test_config_intent_exposes_category_configuration_tools(monkeypatch):
    """CONFIG requests should expose manifest/action tools and hide legacy show tools."""
    async def fake_route_intent(*args, **kwargs) -> Intent:
        """Route the request as CONFIG for this test."""
        return Intent.CONFIG

    settings = Settings(llm=LLMConfig(model="test-model", api_key="test-key"))
    assistant = _make_assistant(settings)

    monkeypatch.setattr("src.ai.assistant.route_intent", fake_route_intent)

    result = await assistant.run("configure the movie library category")

    captured = assistant._llm_client.captured
    tool_names = {tool["function"]["name"] for tool in captured["tools"]}
    assert result == "done"
    assert "get_category_manifest" in tool_names
    assert "category_item_add" not in tool_names
    assert "movie.resolve_metadata" not in tool_names


@pytest.mark.asyncio
async def test_search_intent_hides_mutating_tools_and_passes_generation_options(monkeypatch):
    """SEARCH requests should expose safe read tools and honor task settings."""
    async def fake_route_intent(*args, **kwargs) -> Intent:
        """Route the request as SEARCH for this test."""
        return Intent.SEARCH

    settings = Settings(
        llm=LLMConfig(
            model="test-model",
            api_key="test-key",
            search=TaskModelConfig(max_tokens=123, temperature=0.2),
        )
    )
    assistant = _make_assistant(settings)

    monkeypatch.setattr("src.ai.assistant.route_intent", fake_route_intent)

    await assistant.run("find reviews for a movie")

    captured = assistant._llm_client.captured
    tool_names = {tool["function"]["name"] for tool in captured["tools"]}
    assert "web_search" in tool_names
    assert "queue_download" not in tool_names
    assert "category_item_add" not in tool_names
    assert "movie.delete_item" not in tool_names
    assert captured["max_tokens"] == 123
    assert captured["temperature"] == 0.2
