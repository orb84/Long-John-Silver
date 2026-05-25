"""
Tests for the WhatsApp communication bridge.

Tests configuration detection, message sending, webhook verification,
and incoming message handling using mocked HTTP responses.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.core.models import Settings, NotificationMessage


def _make_settings(**overrides) -> Settings:
    """Create a Settings object with optional overrides."""
    defaults = {}
    defaults.update(overrides)
    return Settings(**defaults)


class TestWhatsAppBridgeConfiguration:
    """Tests for WhatsApp bridge is_configured and is_installed."""

    def test_is_installed_always_true(self):
        """WhatsApp bridge uses httpx — always available."""
        from src.web.whatsapp_bridge import WhatsAppBridge
        bridge = WhatsAppBridge.__new__(WhatsAppBridge)
        assert bridge.is_installed() is True

    def test_is_configured_with_all_fields(self):
        from src.web.whatsapp_bridge import WhatsAppBridge
        settings = _make_settings(
            whatsapp_token="test_token",
            whatsapp_phone_number_id="123456",
        )
        bridge = WhatsAppBridge(settings, AsyncMock(), AsyncMock())
        assert bridge.is_configured() is True

    def test_is_configured_missing_token(self):
        from src.web.whatsapp_bridge import WhatsAppBridge
        settings = _make_settings(
            whatsapp_phone_number_id="123456",
        )
        bridge = WhatsAppBridge(settings, AsyncMock(), AsyncMock())
        assert bridge.is_configured() is False

    def test_is_configured_missing_phone_id(self):
        from src.web.whatsapp_bridge import WhatsAppBridge
        settings = _make_settings(
            whatsapp_token="test_token",
        )
        bridge = WhatsAppBridge(settings, AsyncMock(), AsyncMock())
        assert bridge.is_configured() is False

    def test_is_configured_empty_strings(self):
        from src.web.whatsapp_bridge import WhatsAppBridge
        settings = _make_settings(
            whatsapp_token="",
            whatsapp_phone_number_id="",
        )
        bridge = WhatsAppBridge(settings, AsyncMock(), AsyncMock())
        assert bridge.is_configured() is False


class TestWhatsAppWebhookVerification:
    """Tests for the webhook verification challenge."""

    def test_verify_webhook_success(self):
        from src.web.whatsapp_bridge import WhatsAppBridge
        settings = _make_settings(
            whatsapp_token="test_token",
            whatsapp_phone_number_id="123456",
            whatsapp_verify_token="my_verify_token",
        )
        bridge = WhatsAppBridge(settings, AsyncMock(), AsyncMock())
        result = bridge.verify_webhook("subscribe", "challenge_123", "my_verify_token")
        assert result == "challenge_123"

    def test_verify_webhook_wrong_token(self):
        from src.web.whatsapp_bridge import WhatsAppBridge
        settings = _make_settings(
            whatsapp_token="test_token",
            whatsapp_phone_number_id="123456",
            whatsapp_verify_token="my_verify_token",
        )
        bridge = WhatsAppBridge(settings, AsyncMock(), AsyncMock())
        result = bridge.verify_webhook("subscribe", "challenge_123", "wrong_token")
        assert result is None

    def test_verify_webhook_wrong_mode(self):
        from src.web.whatsapp_bridge import WhatsAppBridge
        settings = _make_settings(
            whatsapp_token="test_token",
            whatsapp_phone_number_id="123456",
            whatsapp_verify_token="my_verify_token",
        )
        bridge = WhatsAppBridge(settings, AsyncMock(), AsyncMock())
        result = bridge.verify_webhook("unsubscribe", "challenge_123", "my_verify_token")
        assert result is None


class TestWhatsAppHandleIncoming:
    """Tests for incoming message handling."""

    @pytest.mark.asyncio
    async def test_handle_incoming_forwards_to_assistant(self):
        from src.web.whatsapp_bridge import WhatsAppBridge
        settings = _make_settings(
            whatsapp_token="test_token",
            whatsapp_phone_number_id="123456",
        )
        assistant = AsyncMock()
        assistant.run = AsyncMock(return_value="Here is your show")
        bridge = WhatsAppBridge(settings, assistant, AsyncMock())

        response = await bridge.handle_incoming("15551234567", "find me Severance")

        assistant.run.assert_called_once_with(
            "find me Severance",
            session_id="whatsapp_15551234567",
            user_id="whatsapp_15551234567",
        )
        assert response == "Here is your show"

    @pytest.mark.asyncio
    async def test_handle_incoming_error_returns_fallback(self):
        from src.web.whatsapp_bridge import WhatsAppBridge
        settings = _make_settings(
            whatsapp_token="test_token",
            whatsapp_phone_number_id="123456",
        )
        assistant = AsyncMock()
        assistant.run = AsyncMock(side_effect=Exception("LLM down"))
        bridge = WhatsAppBridge(settings, assistant, AsyncMock())

        response = await bridge.handle_incoming("15551234567", "hello")

        assert "error" in response.lower() or "Captain" in response


class TestWhatsAppSendMessage:
    """Tests for outgoing message sending."""

    @pytest.mark.asyncio
    async def test_send_message_not_configured(self):
        from src.web.whatsapp_bridge import WhatsAppBridge
        settings = _make_settings()
        bridge = WhatsAppBridge(settings, AsyncMock(), AsyncMock())
        result = await bridge.send_message("15551234567", "test")
        assert result is False


class TestWhatsAppBridgeRegistry:
    """Tests for WhatsApp bridge registration in CommsRegistry."""

    def test_whatsapp_bridge_registers(self):
        from src.web.comms import CommsRegistry
        from src.web.whatsapp_bridge import WhatsAppBridge

        registry = CommsRegistry()
        registry.register(
            bridge_id="whatsapp",
            factory=WhatsAppBridge,
            display_name="WhatsApp",
            description="Chat via WhatsApp Business API",
            icon="test",
            package_name="",
            settings_token_field="whatsapp_token",
        )

        bridges = registry.list_bridges()
        whatsapp_bridges = [b for b in bridges if b["id"] == "whatsapp"]
        assert len(whatsapp_bridges) == 1
        assert whatsapp_bridges[0]["display_name"] == "WhatsApp"
        assert whatsapp_bridges[0]["installed"] is True