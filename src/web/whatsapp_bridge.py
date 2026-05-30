"""
WhatsApp bridge for LJS.

Provides natural language interaction via WhatsApp Business API (Cloud API).
Uses the Meta Cloud API directly via httpx — no additional Python package
required beyond what LJS already depends on.

Setup:
1. Create a Meta Business app at https://developers.facebook.com/
2. Enable WhatsApp in the app and get a permanent access token
3. Configure a phone number ID and verify your webhook URL
4. Set whatsapp_token, whatsapp_phone_number_id, and whatsapp_verify_token
   in LJS settings (or via the setup wizard)

The webhook endpoint at /api/comms/whatsapp/webhook receives messages from
Meta. Messages are forwarded to the AI assistant, and responses are sent
back via the Cloud API.
"""

import asyncio
import hmac
import hashlib
from loguru import logger
from typing import Optional

import httpx

from src.core.models import Settings, NotificationMessage
from src.core.notifications import NotificationService
from src.core.task_supervisor import TaskSupervisor
from src.ai.assistant import AIAssistant
from src.web.comms import CommsBridge

# WhatsApp Cloud API base URL
WHATSAPP_API_BASE = "https://graph.facebook.com/v18.0"


class WhatsAppBridge(CommsBridge):
    """WhatsApp communication bridge using the Meta Cloud API.

    Receives messages via a webhook endpoint and sends replies through
    the Cloud API. No special Python package is needed — httpx handles
    all HTTP communication.
    """

    name = "whatsapp"
    display_name = "WhatsApp"
    description = "Chat with the Quartermaster via WhatsApp Business API (Cloud API)."
    icon = "📱"
    package_name = ""  # No package needed — uses httpx (already a dependency)
    settings_token_field = "whatsapp_token"

    @classmethod
    def get_formatting_instructions(cls) -> str:
        """Return platform-specific formatting instructions and best practices for WhatsApp.

        Instructs the LLM on WhatsApp's restricted text formatting (asterisks, underscores,
        tildes, triple backticks), explicitly bans single backticks, tables, HTML, and escaped newlines.
        """
        return (
            "FORMATTING RULES FOR WHATSAPP:\n"
            "- Always format responses using WhatsApp-specific text formatting:\n"
            "  * Bold: wrapped in asterisks, e.g. `*bold text*`\n"
            "  * Italic: wrapped in underscores, e.g. `_italic text_`\n"
            "  * Monospace: wrapped in three backticks, e.g. ```monospace text```\n"
            "  * Strikethrough: wrapped in tildes, e.g. `~strikethrough text~`\n"
            "- Do NOT use standard Markdown (like `**bold**` or single backticks `` `code` ``), as WhatsApp displays them literally.\n"
            "- Never output HTML tags.\n"
            "- Never use Markdown tables. Use emojis for lists and keep blocks of text short and clear.\n"
            "- Use actual newline characters for line breaks. Do NOT escape them as '\\n' text."
        )

    def __init__(self, settings: Settings, assistant: AIAssistant,
                 notifications: NotificationService, supervisor: TaskSupervisor | None = None):
        super().__init__(settings, assistant, notifications, supervisor=supervisor)
        self._client: Optional[httpx.AsyncClient] = None

    def is_installed(self) -> bool:
        """WhatsApp bridge uses httpx (always available)."""
        return True

    def is_configured(self) -> bool:
        """Check if WhatsApp token and phone number ID are set."""
        token = getattr(self._settings, "whatsapp_token", None)
        phone_id = getattr(self._settings, "whatsapp_phone_number_id", None)
        return bool(token and phone_id)

    def _get_client(self) -> httpx.AsyncClient:
        """Lazy-initialize the HTTP client for the WhatsApp Cloud API."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=WHATSAPP_API_BASE,
                timeout=30.0,
            )
        return self._client

    async def start(self) -> None:
        """Validate configuration and log readiness.

        The actual message receiving happens via the webhook endpoint in
        app.py — this method just verifies the API is reachable.
        """
        token = getattr(self._settings, "whatsapp_token", None)
        phone_id = getattr(self._settings, "whatsapp_phone_number_id", None)

        if not token or not phone_id:
            logger.warning("WhatsApp bridge not configured — missing token or phone number ID")
            return

        # Verify the API token by fetching the business phone number info
        client = self._get_client()
        try:
            resp = await client.get(
                f"/{phone_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 200:
                logger.info(f"WhatsApp bridge ready (phone_id: {phone_id})")
            else:
                logger.warning(f"WhatsApp API check returned {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            logger.warning(f"WhatsApp API unreachable: {e}")

        bridge = _WhatsAppNotificationBridge(self)
        self._notifications.register_bridge(bridge, bridge_id="whatsapp")
        logger.info("WhatsApp notification bridge registered")

    async def stop(self) -> None:
        """Close the HTTP client."""
        self._notifications.unregister_bridge("whatsapp")
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            logger.info("WhatsApp HTTP client closed")

    async def send_message(self, recipient_phone: str, text: str) -> bool:
        """Send a text message to a WhatsApp user via the Cloud API.

        Args:
            recipient_phone: The recipient's phone number in international
                format (e.g., "15551234567").
            text: The message text to send.

        Returns:
            True if the message was sent successfully, False otherwise.
        """
        token = getattr(self._settings, "whatsapp_token", None)
        phone_id = getattr(self._settings, "whatsapp_phone_number_id", None)

        if not token or not phone_id:
            logger.warning("WhatsApp not configured — cannot send message")
            return False

        client = self._get_client()
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient_phone,
            "type": "text",
            "text": {"preview_url": False, "body": text},
        }

        try:
            resp = await client.post(
                f"/{phone_id}/messages",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 200:
                logger.debug(f"WhatsApp message sent to {recipient_phone}")
                return True
            else:
                logger.error(f"WhatsApp send failed ({resp.status_code}): {resp.text[:200]}")
                return False
        except Exception as e:
            logger.error(f"WhatsApp send error: {e}")
            return False

    async def handle_incoming(self, from_phone: str, text: str) -> str:
        """Handle an incoming WhatsApp message through the shared chat runner.

        Progress updates are sent proactively through the Cloud API, while the
        final response is returned to the webhook route for the existing final
        send path.
        """
        session_id = f"whatsapp_{from_phone}"
        user_id = f"whatsapp_{from_phone}"
        response_parts: list[str] = []

        try:
            request = self.make_chat_request(text, session_id, user_id)
            async for event in self.chat_runner.run_events(request):
                if event.type == "status":
                    await self.send_message(from_phone, event.content)
                elif event.type == "token":
                    response_parts.append(event.content)
                elif event.type == "done":
                    return "".join(response_parts).strip()
        except Exception as exc:
            logger.error(f"WhatsApp message handling error: {exc}")
            return self.chat_error_text("WhatsApp request", exc)
        return ""

    def set_last_notification_phone(self, phone: str) -> None:
        """Record the most recent phone number for notification broadcasts.

        WhatsApp requires a phone number to send proactive notifications.
        This is called by the webhook handler when a user sends a message.
        """
        self._last_notification_phone = phone

    async def send_notification(self, message: NotificationMessage) -> None:
        """Send a notification through WhatsApp to the last active chat.

        WhatsApp requires a phone number to send to, unlike Discord/Telegram
        which have channel/chat IDs. This only works if a user has previously
        messaged the bot (per WhatsApp's 24-hour messaging window rule).
        """
        # Notification sending requires storing the most recent user phone,
        # which we track in _last_notification_phone via the webhook handler
        last_phone = getattr(self, "_last_notification_phone", None)
        if last_phone:
            text = f"*{message.title}*: {message.body}"
            success = await self.send_message(last_phone, text)
            if success and hasattr(self, "assistant") and self.assistant:
                session_id = f"whatsapp_{last_phone}"
                coro = self.assistant.record_external_turn(session_id, "assistant", text)
                import inspect
                if inspect.isawaitable(coro):
                    await coro

    def verify_webhook(self, mode: str, challenge: str, verify_token: str) -> str | None:
        """Verify a WhatsApp webhook subscription challenge.

        Meta sends a GET request with hub.mode=subscribe, hub.verify_token,
        and hub.challenge. If our verify_token matches, we echo the challenge.

        Args:
            mode: The hub.mode parameter (should be "subscribe").
            challenge: The hub.challenge parameter to echo back.
            verify_token: The hub.verify_token parameter to check.

        Returns:
            The challenge string if verification succeeds, None otherwise.
        """
        expected = getattr(self._settings, "whatsapp_verify_token", None)
        if mode == "subscribe" and verify_token == expected:
            logger.info("WhatsApp webhook verified")
            return challenge
        logger.warning(f"WhatsApp webhook verification failed: mode={mode}")
        return None


class _WhatsAppNotificationBridge:
    """Notification adapter that sends messages through WhatsApp."""

    def __init__(self, bridge: WhatsAppBridge):
        self._bridge = bridge

    async def send_notification(self, message: NotificationMessage) -> None:
        """Execute the public _WhatsAppNotificationBridge.send_notification behavior.

        This method is a supported extension point for callers outside the
        class.  Keep its input/output contract stable and move specialized
        logic into collaborators or protected helpers as the feature grows.
        """
        await self._bridge.send_notification(message)
