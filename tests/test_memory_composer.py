"""
Tests for PromptMemoryComposer.

Verifies that different intents include the expected context sections
and that the AIAssistant system prompt includes memory composer output.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.ai.assistant import AIAssistant, AgentDependencies
from src.ai.memory_composer import PromptMemoryComposer
from src.ai.tool_registry import ToolRegistry
from src.core.models import (
    DownloadItem, DownloadStatus, Settings, LLMConfig, Intent,
)


# ---------------------------------------------------------------------------
# Fakes / stubs
# ---------------------------------------------------------------------------

class FakePreferenceManager:
    """Returns a canned preference summary."""

    def __init__(self, summary: str = "User prefers 1080p H.265 releases"):
        self._summary = summary

    async def get_summary(self, user_id: str | None = None) -> str:
        return self._summary


class FakeDownloader:
    """Returns a canned list of active downloads."""

    def __init__(self, items: list[DownloadItem] | None = None):
        self._items = items or []

    async def get_active_downloads(self) -> list[DownloadItem]:
        return self._items


class FakeDB:
    """Stub database with pluggable recent/blacklist methods."""

    def __init__(self):
        self.downloads = MagicMock()
        self.downloads.get_recent_downloads = AsyncMock(return_value=[])
        self.downloads.get_blacklist = AsyncMock(return_value=[])


class FakeActionEventStore:
    """Returns a canned recent-action list."""

    def __init__(self, events: list[dict] | None = None):
        self._events = events or []

    async def get_recent(self, limit: int = 50, source=None,
                         action_name: str | None = None) -> list[dict]:
        return self._events


class FakeSettingsManager:
    """Stub settings manager with tracked items."""

    def __init__(self, items=None):
        self.settings = MagicMock()
        self.settings.tracked_items = items or []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_active_download(name: str = "Test Show",
                         status: DownloadStatus = DownloadStatus.DOWNLOADING,
                         progress: float = 0.5) -> DownloadItem:
    return DownloadItem(
        id=f"dl_{name.lower().replace(' ', '_')}",
        item_name=name,
        magnet="magnet:?xt=urn:btih:0" * 3,
        status=status,
        progress=progress,
    )


def make_failure_item(name: str = "Failed Show") -> DownloadItem:
    return DownloadItem(
        id=f"dl_{name.lower().replace(' ', '_')}",
        item_name=name,
        magnet="magnet:?xt=urn:btih:0" * 3,
        status=DownloadStatus.FAILED,
    )


def make_ui_action(name: str = "set_download_priority",
                   args: dict | None = None,
                   ts: str = "2026-05-17T12:00:00") -> dict:
    args = args or {}
    return {
        "action_name": name,
        "arguments_json": args,
        "created_at": ts,
    }


# ---------------------------------------------------------------------------
# Tests — PromptMemoryComposer
# ---------------------------------------------------------------------------

class TestPromptMemoryComposer:
    """Context section selection by intent."""

    @pytest.mark.asyncio
    async def test_download_intent_includes_active_downloads(self):
        """DOWNLOAd intent includes active download state."""
        active = [make_active_download("Show A")]
        composer = PromptMemoryComposer(
            downloader=FakeDownloader(active),
            preference_manager=FakePreferenceManager(),
        )
        text = await composer.compose(intent=Intent.DOWNLOAD)
        assert "Active downloads:" in text
        assert "Show A" in text

    @pytest.mark.asyncio
    async def test_download_intent_includes_recent_failures(self):
        """DOWNLOAd intent includes recent failed download entries."""
        db = FakeDB()
        db.downloads.get_recent_downloads.return_value = [
            make_failure_item("Failed Show"),
        ]
        composer = PromptMemoryComposer(
            database=db,
            preference_manager=FakePreferenceManager(),
        )
        text = await composer.compose(intent=Intent.DOWNLOAD)
        assert "Recent download failures:" in text
        assert "Failed Show" in text

    @pytest.mark.asyncio
    async def test_search_intent_includes_blacklist(self):
        """SEARCH intent includes blacklist/rejected patterns."""
        db = FakeDB()
        db.downloads.get_blacklist.return_value = [
            MagicMock(spec=["pattern"], pattern="CAM-*"),
            MagicMock(spec=["pattern"], pattern="HDRip"),
        ]
        composer = PromptMemoryComposer(
            database=db,
            preference_manager=FakePreferenceManager(),
        )
        text = await composer.compose(intent=Intent.SEARCH)
        assert "Rejected / blacklisted patterns:" in text
        assert "CAM-*" in text
        assert "HDRip" in text

    @pytest.mark.asyncio
    async def test_download_intent_includes_blacklist(self):
        """DOWNLOAd intent also includes blacklist."""
        db = FakeDB()
        db.downloads.get_blacklist.return_value = [
            MagicMock(spec=["pattern"], pattern="BadGroup"),
        ]
        composer = PromptMemoryComposer(
            database=db,
            preference_manager=FakePreferenceManager(),
        )
        text = await composer.compose(intent=Intent.DOWNLOAD)
        assert "Rejected / blacklisted patterns:" in text

    @pytest.mark.asyncio
    async def test_config_intent_includes_library_state(self):
        """CONFIG intent includes tracked library items."""
        fake_settings = FakeSettingsManager(
            items=[MagicMock(key="Show1", enabled=True),
                   MagicMock(key="Show2", enabled=False)],
        )
        composer = PromptMemoryComposer(
            settings_manager=fake_settings,
            preference_manager=FakePreferenceManager(),
        )
        text = await composer.compose(intent=Intent.CONFIG)
        assert "Library state:" in text
        assert "Show1" in text

    @pytest.mark.asyncio
    async def test_search_intent_includes_library_state(self):
        """SEARCH intent includes library state for context."""
        fake_settings = FakeSettingsManager(
            items=[MagicMock(key="Known Show", enabled=True)],
        )
        composer = PromptMemoryComposer(
            settings_manager=fake_settings,
            preference_manager=FakePreferenceManager(),
        )
        text = await composer.compose(intent=Intent.SEARCH)
        assert "Library state:" in text
        assert "Known Show" in text

    @pytest.mark.asyncio
    async def test_download_intent_includes_ui_actions(self):
        """DOWNLOAd intent includes recent UI action summaries."""
        store = FakeActionEventStore([
            make_ui_action("pause_download", {"id": "dl_1"}),
        ])
        composer = PromptMemoryComposer(
            action_event_store=store,
            preference_manager=FakePreferenceManager(),
        )
        text = await composer.compose(intent=Intent.DOWNLOAD)
        assert "Recent UI actions:" in text
        assert "pause_download" in text

    @pytest.mark.asyncio
    async def test_chat_intent_excludes_noisy_state(self):
        """CHAT intent excludes active downloads, failures, blacklist."""
        active = [make_active_download("Noisy Show")]
        db = FakeDB()
        db.downloads.get_blacklist.return_value = [MagicMock(pattern="CAM-*")]
        composer = PromptMemoryComposer(
            downloader=FakeDownloader(active),
            database=db,
            preference_manager=FakePreferenceManager(),
        )
        text = await composer.compose(intent=Intent.CHAT)
        assert "Active downloads:" not in text
        assert "Rejected / blacklisted patterns:" not in text

    @pytest.mark.asyncio
    async def test_chat_intent_still_includes_preferences(self):
        """CHAT intent still includes user preferences."""
        composer = PromptMemoryComposer(
            preference_manager=FakePreferenceManager("User likes 4K HDR"),
        )
        text = await composer.compose(intent=Intent.CHAT)
        assert "User likes 4K HDR" in text

    @pytest.mark.asyncio
    async def test_empty_state_returns_empty_string(self):
        """Composer returns empty string when no state available."""
        composer = PromptMemoryComposer()
        text = await composer.compose(intent=Intent.DOWNLOAD)
        assert text == ""


# ---------------------------------------------------------------------------
# Tests — AIAssistant integration (Phase 2.5)
# ---------------------------------------------------------------------------

class CapturingLLMClient:
    """Captures completion kwargs and messages; returns a canned response."""

    def __init__(self):
        self.captured = {}
        self.last_messages = None

    async def completion(self, *, task, messages, tools=None, stream=False, **kwargs):
        self.captured.update(kwargs)
        self.captured["tools"] = tools
        self.last_messages = messages
        fake = MagicMock()
        fake.choices = [MagicMock(message=MagicMock(content="done", tool_calls=[]))]
        return fake

    def update_config(self, config):
        pass


class TestAssistantMemoryComposerIntegration:
    """System prompt includes memory composer output."""

    @pytest.mark.asyncio
    async def test_system_prompt_includes_memory_composer_output(self, monkeypatch):
        """AIAssistant system prompt contains context from the memory composer."""
        async def fake_route(*args, **kwargs):
            return Intent.DOWNLOAD

        monkeypatch.setattr("src.ai.assistant.route_intent", fake_route)

        mock_compose = AsyncMock(return_value="Memory context: active downloads, preferences")
        monkeypatch.setattr("src.ai.memory_composer.PromptMemoryComposer.compose", mock_compose)

        llm_client = CapturingLLMClient()

        registry = ToolRegistry()
        registry.register(
            name="dummy_tool",
            description="Dummy",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=AsyncMock(return_value={}),
        )

        assistant = AIAssistant(AgentDependencies(
            settings=Settings(llm=LLMConfig(model="test", api_key="key")),
            preference_manager=FakePreferenceManager("ignored"),
            tool_registry=registry,
            llm_client=llm_client,
        ))

        await assistant.run("download test show")

        # The system prompt is messages[0].content
        system_prompt = llm_client.last_messages[0]["content"] if llm_client.last_messages else ""
        assert "Memory context: active downloads, preferences" in system_prompt

class FakeTasteProfiler:
    """Minimal category taste profiler for memory composer tests."""

    async def build_category_profile(self, category_id, user_id=None, include_library=True, limit=200):
        return {"category_id": category_id, "user_id": user_id}

    def format_category_profile_for_prompt(self, category_id, profile):
        return f"CATEGORY TASTE PROFILE [{category_id}]: likes thoughtful sci-fi"


@pytest.mark.asyncio
async def test_composer_includes_category_taste_profile_when_category_is_active():
    """Category-scoped taste profile is injected only when a category is active."""
    composer = PromptMemoryComposer(taste_profiler=FakeTasteProfiler())
    text = await composer.compose(intent=Intent.CHAT, category_id="book", user_id="captain")
    assert "CATEGORY TASTE PROFILE [book]" in text
    assert "thoughtful sci-fi" in text
