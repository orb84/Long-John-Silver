"""
Telegram bridge for LJS.

Provides natural language interaction via Telegram with async compatibility.
Requires the 'python-telegram-bot' package. Gracefully disabled if not installed.
"""

from loguru import logger
from src.core.models import Settings, NotificationMessage
from src.core.notifications import NotificationService
from src.core.task_supervisor import TaskSupervisor
from src.ai.assistant import AIAssistant
from src.web.comms import CommsBridge

try:
    from telegram import Update
    from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters, CommandHandler
    _TELEGRAM_AVAILABLE = True
except ImportError:
    _TELEGRAM_AVAILABLE = False


class TelegramBridge(CommsBridge):
    """Telegram communication bridge (CommsBridge plugin).

    Connects to Telegram as a bot, forwards messages to the AI assistant,
    and sends notifications to the configured chat.
    """

    name = "telegram"
    display_name = "Telegram"
    description = "Chat with the Quartermaster via Telegram bot in direct messages."
    icon = "✈️"
    package_name = "python-telegram-bot"
    settings_token_field = "telegram_token"

    @classmethod
    def get_formatting_instructions(cls) -> str:
        """Return platform-specific formatting instructions and best practices for Telegram.

        Instructs the LLM to use legacy Markdown syntax (not MarkdownV2) and avoid HTML,
        tables, or escaped newlines (which would render literally).
        """
        return (
            "FORMATTING RULES FOR TELEGRAM:\n"
            "- Always format responses using Telegram-compatible Markdown.\n"
            "- Use standard Markdown rules: bold (`*text*`), italic (`_text_`), inline code (`` `code` ``), and code blocks.\n"
            "- Do NOT use MarkdownV2 syntax (do not escape periods or hyphens).\n"
            "- Never output HTML tags.\n"
            "- Avoid Markdown tables, since they render poorly on Telegram mobile screens. Instead, use emojis and bulleted text lists.\n"
            "- Use actual newline characters for line breaks. Do NOT escape them as '\\n' text."
        )

    def __init__(self, settings: Settings, assistant: AIAssistant,
                 notifications: NotificationService, supervisor: TaskSupervisor | None = None):
        super().__init__(settings, assistant, notifications, supervisor=supervisor)
        self.application = None
        self._chat_id = None

    @property
    def chat_id(self) -> object | None:
        """Return the last Telegram chat id used for replies/notifications."""
        return self._chat_id

    def session_id_for_chat(self, chat_id: object | None = None) -> str:
        """Return the assistant session id for a Telegram chat."""
        return f"telegram_{chat_id if chat_id is not None else self._chat_id}"

    def is_installed(self) -> bool:
        """Return whether TelegramBridge satisfies this condition.

        Use this method as a read-only capability check.  Avoid side effects
        so callers can safely use it in routing, health checks, and tests.
        """
        return _TELEGRAM_AVAILABLE

    def is_configured(self) -> bool:
        """Return whether TelegramBridge satisfies this condition.

        Use this method as a read-only capability check.  Avoid side effects
        so callers can safely use it in routing, health checks, and tests.
        """
        token = getattr(self._settings, "telegram_token", None)
        return bool(token and token != "YOUR_TELEGRAM_TOKEN")

    async def start(self) -> None:
        """Start the Telegram bot."""
        if not _TELEGRAM_AVAILABLE:
            logger.warning("Telegram bot not available — install python-telegram-bot to enable")
            return

        token = self._settings.telegram_token
        if not token or token == "YOUR_TELEGRAM_TOKEN":
            logger.warning("Telegram token not set. Bot will not start.")
            return

        self.application = ApplicationBuilder().token(token).build()

        self.application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND, self._handle_message,
        ))
        self.application.add_handler(CommandHandler("start", self._start_command))
        self.application.add_handler(CommandHandler("status", self._status_command))
        self.application.add_handler(CommandHandler("search", self._search_command))
        self.application.add_handler(CommandHandler("download", self._download_command))

        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling()

        bridge = _TelegramNotificationBridge(self)
        self._notifications.register_bridge(bridge, bridge_id="telegram")
        logger.info("Telegram notification bridge registered")
        logger.info("Telegram bot started")

    async def stop(self) -> None:
        """Stop the Telegram bot gracefully."""
        if self.application:
            self._notifications.unregister_bridge("telegram")
            await self.application.updater.stop()
            await self.application.stop()
            await self.application.shutdown()
            logger.info("Telegram bot stopped")

    async def send_notification(self, message: NotificationMessage) -> None:
        """Send a notification through Telegram."""
        if self.application and self._chat_id:
            try:
                await self.application.bot.send_message(
                    chat_id=self._chat_id,
                    text=f"*{message.title}*: {message.body}",
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.error(f"Telegram notification error: {e}")
    async def _reply_markdown(self, update: "Update", text: str) -> None:
        """Reply to the user using Markdown parsing, with plain text fallback on failure."""
        try:
            await update.message.reply_text(text, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Telegram Markdown parse failed: {e}. Falling back to plain text.")
            try:
                await update.message.reply_text(text)
            except Exception as ex:
                logger.error(f"Telegram fallback reply failed: {ex}")


    async def _run_prompt(self, update: "Update", prompt: str, operation: str) -> None:
        """Adapt the shared assistant chat runner to Telegram replies."""
        if not update.message or not update.effective_chat or not update.effective_user:
            return
        session_id = f"telegram_{update.effective_chat.id}"
        user_id = f"telegram_{update.effective_user.id}"
        response_parts: list[str] = []
        try:
            request = self.make_chat_request(prompt, session_id, user_id)
            async for event in self.chat_runner.run_events(request):
                if event.type == "status":
                    await self._reply_markdown(update, event.content)
                elif event.type == "token":
                    response_parts.append(event.content)
                elif event.type == "done":
                    response = "".join(response_parts).strip()
                    if response:
                        await self._reply_markdown(update, response)
                    return
        except Exception as exc:
            logger.error(f"Telegram {operation} error: {exc}")
            await self._reply_markdown(update, self.chat_error_text(operation, exc))

    async def _handle_message(self, update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
        if not update.message or not update.message.text:
            return
        if update.effective_chat:
            self._chat_id = update.effective_chat.id
        await self._run_prompt(update, update.message.text, "Telegram message")

    async def _start_command(self, update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
        if update.effective_chat:
            self._chat_id = update.effective_chat.id
        await update.message.reply_text(
            "Ahoy! I'm the Quartermaster. Tell me what you want to watch, "
            "and I'll find the best torrents for you."
        )

    async def _status_command(self, update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
        if update.effective_chat:
            self._chat_id = update.effective_chat.id
        await update.message.reply_text("LJS System: Online. All hands on deck!")

    async def _search_command(self, update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
        if update.effective_chat:
            self._chat_id = update.effective_chat.id
        if not context.args:
            await update.message.reply_text("Usage: /search <show name>")
            return
        query = " ".join(context.args)
        await self._run_prompt(update, f"search {query}", "Telegram search")

    async def _download_command(self, update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
        if update.effective_chat:
            self._chat_id = update.effective_chat.id
        if not context.args:
            await update.message.reply_text("Usage: /download <show name>")
            return
        query = " ".join(context.args)
        await self._run_prompt(update, f"download {query}", "Telegram download")


class _TelegramNotificationBridge:
    """Internal notification adapter for Telegram."""

    def __init__(self, bridge: TelegramBridge):
        self._bridge = bridge

    def chat_id(self) -> object | None:
        """Return chat id through the public bridge seam, with test-double compatibility."""
        value = getattr(self._bridge, "chat_id", None)
        raw_value = vars(self._bridge).get("_chat_id")
        return raw_value if raw_value is not None else value

    @property
    def delivery_id(self) -> str:
        """Stable delivery-ledger key for the current Telegram chat target."""
        return f"telegram:{self.chat_id() or 'unconfigured'}"

    def session_id(self) -> str:
        """Return the assistant session id for the current Telegram chat."""
        return f"telegram_{self.chat_id()}"

    async def send_notification(self, message: NotificationMessage) -> bool:
        """Send a notification through Telegram.

        Attempts to format as Markdown first, falling back to plain text if Telegram's
        strict Markdown parser rejects the payload syntax.
        """
        if not (self._bridge.application and self.chat_id()):
            return False
        text = f"*{message.title}*: {message.body}"
        recorded_successfully = False
        try:
            await self._bridge.application.bot.send_message(
                chat_id=self.chat_id(),
                text=text,
                parse_mode="Markdown",
            )
            recorded_successfully = True
        except Exception as e:
            logger.warning(f"Telegram notification Markdown parse failed: {e}. Falling back to plain text.")
            try:
                await self._bridge.application.bot.send_message(
                    chat_id=self.chat_id(),
                    text=f"{message.title}: {message.body}",
                )
                text = f"{message.title}: {message.body}"
                recorded_successfully = True
            except Exception as ex:
                logger.error(f"Telegram notification fallback failed: {ex}")

        if recorded_successfully and hasattr(self._bridge, "assistant") and self._bridge.assistant:
            session_id = self.session_id()
            coro = self._bridge.assistant.record_external_turn(session_id, "assistant", text)
            import inspect
            if inspect.isawaitable(coro):
                await coro
        return recorded_successfully
