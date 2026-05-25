"""
Tests for the Telegram communication bridge.

Tests configuration, formatting rules, and notification sending with
strict Markdown error fallback behavior.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.core.models import Settings, NotificationMessage
from src.web.telegram_bridge import TelegramBridge, _TelegramNotificationBridge


def _make_settings(**overrides) -> Settings:
    """Create a Settings object with optional overrides."""
    defaults = {}
    defaults.update(overrides)
    return Settings(**defaults)


class TestTelegramBridgeConfiguration:
    """Tests for Telegram bridge is_configured and formatting rules."""

    def test_is_installed_boolean(self):
        """Telegram bridge installation status check."""
        bridge = TelegramBridge.__new__(TelegramBridge)
        # Should be a boolean (either True or False depending on environment)
        assert isinstance(bridge.is_installed(), bool)

    def test_is_configured_with_all_fields(self):
        """Bridge is configured when token is set and not default."""
        settings = _make_settings(telegram_token="test_token")
        bridge = TelegramBridge(settings, AsyncMock(), AsyncMock())
        assert bridge.is_configured() is True

    def test_is_configured_missing_token(self):
        """Bridge is not configured when token is default or empty."""
        settings = _make_settings(telegram_token="YOUR_TELEGRAM_TOKEN")
        bridge = TelegramBridge(settings, AsyncMock(), AsyncMock())
        assert bridge.is_configured() is False

        settings = _make_settings(telegram_token="")
        bridge = TelegramBridge(settings, AsyncMock(), AsyncMock())
        assert bridge.is_configured() is False

    def test_get_formatting_instructions(self):
        """Check formatting rules contain expected platform rules."""
        instructions = TelegramBridge.get_formatting_instructions()
        assert "FORMATTING RULES FOR TELEGRAM" in instructions
        assert "escaped" in instructions or "Do NOT escape" in instructions


class TestTelegramNotificationBridge:
    """Tests for the internal Telegram notification bridge fallback logic."""

    @pytest.mark.asyncio
    async def test_send_notification_success(self):
        """Test successful markdown notification sending."""
        mock_bridge = MagicMock()
        mock_bridge._chat_id = 98765
        mock_bridge.application = MagicMock()
        mock_bridge.assistant = MagicMock()
        mock_bridge.assistant.record_external_turn = AsyncMock()
        
        mock_bot = AsyncMock()
        mock_bridge.application.bot = mock_bot

        notifier = _TelegramNotificationBridge(mock_bridge)
        msg = NotificationMessage(title="Test Title", body="Test Body")

        await notifier.send_notification(msg)

        mock_bot.send_message.assert_called_once_with(
            chat_id=98765,
            text="*Test Title*: Test Body",
            parse_mode="Markdown",
        )
        mock_bridge.assistant.record_external_turn.assert_called_once_with(
            "telegram_98765",
            "assistant",
            "*Test Title*: Test Body",
        )

    @pytest.mark.asyncio
    async def test_send_notification_markdown_fail_fallback_plain(self):
        """Test fallback to plain text if Telegram Markdown parsing fails."""
        mock_bridge = MagicMock()
        mock_bridge._chat_id = 98765
        mock_bridge.application = MagicMock()
        mock_bridge.assistant = MagicMock()
        mock_bridge.assistant.record_external_turn = AsyncMock()
        
        mock_bot = AsyncMock()
        mock_bridge.application.bot = mock_bot

        # Raise exception on first call (Markdown mode), succeed on second (plain text)
        mock_bot.send_message.side_effect = [
            Exception("Can't parse entities: character '*' is reserved"),
            AsyncMock()
        ]

        notifier = _TelegramNotificationBridge(mock_bridge)
        msg = NotificationMessage(title="Test Title", body="Test *Invalid Body")

        await notifier.send_notification(msg)

        # Should be called twice: first with Markdown, second without parse_mode (fallback)
        assert mock_bot.send_message.call_count == 2
        mock_bot.send_message.assert_any_call(
            chat_id=98765,
            text="*Test Title*: Test *Invalid Body",
            parse_mode="Markdown",
        )
        mock_bot.send_message.assert_any_call(
            chat_id=98765,
            text="Test Title: Test *Invalid Body",
        )
        mock_bridge.assistant.record_external_turn.assert_called_once_with(
            "telegram_98765",
            "assistant",
            "Test Title: Test *Invalid Body",
        )
