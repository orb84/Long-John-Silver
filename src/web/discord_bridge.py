"""
Discord bridge for LJS.

Provides natural language interaction via Discord with async compatibility.
Requires the 'discord.py' package. The module deliberately does not cache the
import result at process import time: setup may install discord.py while LJS is
already running, and a later restart/start attempt must re-check the package.
"""

from __future__ import annotations

import asyncio
import importlib
from typing import Any

from loguru import logger

from src.ai.assistant import AIAssistant
from src.core.models import NotificationMessage, Settings
from src.core.notifications import NotificationService
from src.core.task_supervisor import TaskSupervisor
from src.web.comms import CommsBridge


def _load_discord_modules() -> tuple[Any | None, Any | None, Any | None]:
    """Return ``discord``, ``commands`` and ``app_commands`` if importable.

    ``discord.py`` can be installed from the setup wizard after this module has
    already been imported.  A module-level ``_DISCORD_AVAILABLE`` boolean would
    remain false until process restart and can also hide partial import errors.
    This function re-checks the environment each time the bridge is inspected or
    started and logs the real import failure for diagnosis.
    """
    try:
        discord = importlib.import_module("discord")
        commands = importlib.import_module("discord.ext.commands")
        try:
            app_commands = importlib.import_module("discord.app_commands")
        except Exception:
            app_commands = getattr(discord, "app_commands", None)
        return discord, commands, app_commands
    except Exception as exc:
        logger.debug(f"discord.py import check failed: {exc}")
        return None, None, None


def _channel_id(value: Any) -> int | None:
    """Normalize a configured Discord channel ID.

    Settings forms commonly persist numeric IDs as strings.  discord.py's
    ``get_channel`` expects an int, so leaving the value as a string makes
    notifications silently fail even when the bot is online.
    """
    if value is None or value == "":
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        logger.warning(f"Discord channel id is not numeric: {value!r}")
        return None


def _split_discord_content(text: str, limit: int = 1900) -> list[str]:
    """Split text into Discord-safe message chunks."""
    if not text:
        return [""]
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n", 0, limit)
        if split_at == -1 or split_at < limit // 2:
            split_at = remaining.rfind(" ", 0, limit)
        if split_at == -1 or split_at < limit // 2:
            split_at = limit
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip()
    return chunks


class DiscordBridge(CommsBridge):
    """Discord communication bridge (CommsBridge plugin).

    Supports classic ``!`` commands and natural-language channel messages when
    Discord's privileged Message Content Intent is enabled.  It also registers
    slash commands where supported so the bridge is still usable when that
    privileged intent is not granted in the Discord developer portal.
    """

    name = "discord"
    display_name = "Discord"
    description = "Chat with the Quartermaster via Discord bot in your server."
    icon = "💬"
    package_name = "discord.py"
    settings_token_field = "discord_token"

    @classmethod
    def get_formatting_instructions(cls) -> str:
        """Return platform-specific formatting instructions for Discord."""
        return (
            "FORMATTING RULES FOR DISCORD:\n"
            "- Always format responses using Discord-compatible Markdown.\n"
            "- Use bold (`**text**`), italic (`*text*` or `_text_`), strikethrough (`~~text~~`), and inline code or code blocks.\n"
            "- Do NOT output HTML tags.\n"
            "- NEVER use Markdown tables. They do not render correctly on Discord mobile. Use clean vertical lists instead.\n"
            "- Use actual newline characters for line breaks. Do NOT escape them as '\\n' text.\n"
            "- Keep messages concise and within Discord's 2000-character limit."
        )

    def __init__(self, settings: Settings, assistant: AIAssistant,
                 notifications: NotificationService, supervisor: TaskSupervisor | None = None):
        super().__init__(settings, assistant, notifications, supervisor=supervisor)
        self._bot = None

    def is_installed(self) -> bool:
        """Return whether discord.py is importable right now."""
        discord, commands, _ = _load_discord_modules()
        return bool(discord and commands)

    def is_configured(self) -> bool:
        """Return whether a real Discord bot token is configured."""
        token = getattr(self._settings, "discord_token", None)
        return bool(token and token != "YOUR_DISCORD_TOKEN")

    async def start(self) -> None:
        """Start the Discord bot and register message/slash handlers."""
        discord, commands, app_commands = _load_discord_modules()
        if not discord or not commands:
            logger.warning("Discord bot not available — install discord.py to enable")
            return

        token = getattr(self._settings, "discord_token", None)
        if not token or token == "YOUR_DISCORD_TOKEN":
            logger.warning("Discord token not set. Bot will not start.")
            return

        intents = discord.Intents.default()
        # This still requires enabling Message Content Intent in the Discord
        # developer portal. Slash commands below provide a usable fallback.
        intents.message_content = True

        assistant = self._assistant
        notifications = self._notifications
        bridge = self

        class LJSDiscordBot(commands.Bot):
            """Runtime bot class bound to the currently imported discord.py."""

            def __init__(self) -> None:
                super().__init__(command_prefix="!", intents=intents)
                self.assistant = assistant
                self._bridge = bridge
                self._notifications = notifications
                self._slash_commands_registered = False
                self._notification_bridge_registered = False

            async def on_ready(self) -> None:
                """Log readiness and sync slash commands once Discord connects."""
                user_name = getattr(getattr(self, "user", None), "name", "unknown")
                guild_count = len(getattr(self, "guilds", []) or [])
                logger.info(
                    f"Discord Bot logged in as {user_name}; guilds={guild_count}; "
                    "message_content_intent_requested=True"
                )
                if not self._slash_commands_registered:
                    await self._sync_slash_commands_once()
                if not self._notification_bridge_registered:
                    channel_id = _channel_id(getattr(self._bridge._settings, "discord_channel_id", None))
                    self._notifications.register_bridge(
                        _DiscordNotificationBridge(self, channel_id=channel_id),
                        bridge_id="discord",
                    )
                    self._notification_bridge_registered = True
                    logger.info(f"Discord notification bridge registered; channel_id={channel_id or 'not configured'}")

            async def _sync_slash_commands_once(self) -> None:
                if app_commands is None:
                    logger.info("Discord slash commands unavailable in installed discord.py version")
                    self._slash_commands_registered = True
                    return
                try:
                    synced = await self.tree.sync()
                    logger.info(f"Discord slash commands synced: {len(synced)} command(s)")
                except Exception as exc:
                    logger.warning(f"Discord slash command sync failed: {exc}")
                self._slash_commands_registered = True

            async def on_message(self, message: Any) -> None:
                """Route classic Discord messages to commands or the AI assistant."""
                if message.author == self.user:
                    return
                content = str(getattr(message, "content", "") or "").strip()
                if not content:
                    logger.debug(
                        "Discord message received with empty content. Enable Message Content Intent "
                        "or use /ljs, /search, /download slash commands."
                    )
                    return
                if content.startswith("!"):
                    await self.process_commands(message)
                    return
                await self.handle_prompt(content, message.channel, message.author.id, reply_to=message)

            async def _send_chunked(self, send_func: Any, text: str) -> None:
                """Send text through a Discord send-like callable in safe chunks."""
                for chunk in _split_discord_content(str(text or "")):
                    await send_func(chunk)

            async def _run_prompt_with_progress(
                self,
                prompt: str,
                session_id: str,
                user_id: str,
                send_status: Any,
                send_final: Any,
            ) -> None:
                """Adapt the shared assistant chat stream to Discord delivery."""
                response_parts: list[str] = []
                request = self._bridge.make_chat_request(prompt, session_id, user_id)
                async for event in self._bridge.chat_runner.run_events(request):
                    if event.type == "status":
                        await send_status(event.content)
                    elif event.type == "token":
                        response_parts.append(event.content)
                    elif event.type == "done":
                        response = "".join(response_parts).strip()
                        if not response:
                            response = (
                                "I'm not sure how to help with that, Captain. "
                                "Try `/search query:...`, `/download query:...`, or describe what you need."
                            )
                        await self._send_chunked(send_final, response)

            async def handle_prompt(self, prompt: str, channel: Any, author_id: Any, reply_to: Any | None = None) -> None:
                """Handle a classic Discord prompt with progress pings and chunked final output."""
                session_id = f"discord_{getattr(channel, 'id', 'unknown')}"
                user_id = f"discord_{author_id}"
                status_count = 0

                async def send_status(chunk: str) -> None:
                    """Send one Discord progress chunk, replying to the trigger once when possible."""
                    nonlocal status_count
                    if reply_to is not None and status_count == 0:
                        await reply_to.reply(chunk)
                    else:
                        await channel.send(chunk)
                    status_count += 1

                async def send_final(chunk: str) -> None:
                    """Send one Discord final-response chunk."""
                    await channel.send(chunk)

                try:
                    async with channel.typing():
                        await self._run_prompt_with_progress(
                            prompt=prompt,
                            session_id=session_id,
                            user_id=user_id,
                            send_status=send_status,
                            send_final=send_final,
                        )
                except Exception as exc:
                    logger.error(f"Discord message handling error for user {user_id}: {exc}")
                    target = reply_to.reply if reply_to is not None else channel.send
                    await target(self._bridge.chat_error_text("Discord request", exc))

            async def handle_interaction_prompt(self, interaction: Any, prompt: str) -> None:
                """Handle a slash-command prompt with progress followups."""
                await interaction.response.defer(thinking=True)
                session_id = f"discord_{interaction.channel_id}"
                user_id = f"discord_{interaction.user.id}"
                try:
                    await self._run_prompt_with_progress(
                        prompt=prompt,
                        session_id=session_id,
                        user_id=user_id,
                        send_status=interaction.followup.send,
                        send_final=interaction.followup.send,
                    )
                except Exception as exc:
                    logger.error(f"Discord slash command handling error for user {user_id}: {exc}")
                    await interaction.followup.send(self._bridge.chat_error_text("Discord slash request", exc))

        bot = LJSDiscordBot()
        self._bot = bot

        @bot.command()
        async def status(ctx):
            """Report basic Discord bridge liveness."""
            await ctx.send("LJS System: Online. All hands on deck!")

        @bot.command()
        async def search(ctx, *, query: str):
            """Route a classic-prefix search command into the shared assistant runtime."""
            await bot.handle_prompt(f"search {query}", ctx.channel, ctx.author.id, reply_to=ctx.message)

        @bot.command()
        async def download(ctx, *, query: str):
            """Route a classic-prefix download command into the shared assistant runtime."""
            await bot.handle_prompt(f"download {query}", ctx.channel, ctx.author.id, reply_to=ctx.message)

        if app_commands is not None:
            @bot.tree.command(name="ljs", description="Ask the LJS assistant anything.")
            async def ljs_command(interaction, prompt: str):
                """Route a slash-command free-form prompt into the shared assistant runtime."""
                await bot.handle_interaction_prompt(interaction, prompt)

            @bot.tree.command(name="search", description="Search through LJS.")
            async def search_command(interaction, query: str):
                """Route a slash-command search prompt into the shared assistant runtime."""
                await bot.handle_interaction_prompt(interaction, f"search {query}")

            @bot.tree.command(name="download", description="Ask LJS to download something.")
            async def download_command(interaction, query: str):
                """Route a slash-command download prompt into the shared assistant runtime."""
                await bot.handle_interaction_prompt(interaction, f"download {query}")

        if self._supervisor:
            from src.core.models import TaskCriticality
            self._supervisor.spawn_restartable(
                "discord_bot",
                lambda: bot.start(token),
                TaskCriticality.IMPORTANT,
            )
        else:
            asyncio.create_task(bot.start(token))

    async def stop(self) -> None:
        """Stop the Discord bot gracefully."""
        if self._bot:
            self._notifications.unregister_bridge("discord")
            await self._bot.close()
            logger.info("Discord bot stopped")

    async def send_notification(self, message: NotificationMessage) -> None:
        """Send a notification through Discord."""
        if not self._bot or not self._bot.is_ready():
            return
        channel_id = _channel_id(getattr(self._settings, "discord_channel_id", None))
        if not channel_id:
            return
        channel = self._bot.get_channel(channel_id)
        if channel:
            await channel.send(f"**{message.title}**: {message.body}")


class _DiscordNotificationBridge:
    """Internal notification adapter for Discord."""

    def __init__(self, bot: Any, channel_id: int | None = None):
        self._bot = bot
        self._channel_id = channel_id

    @property
    def delivery_id(self) -> str:
        """Stable delivery-ledger key for this Discord notification target."""
        return f"discord:{self._channel_id or 'unconfigured'}"

    async def send_notification(self, message: NotificationMessage) -> bool:
        """Send a notification to the configured Discord channel."""
        if self._channel_id is None or not self._bot.is_ready():
            return False
        channel = self._bot.get_channel(self._channel_id)
        if not channel:
            logger.warning(f"Discord notification channel not found or not cached: {self._channel_id}")
            return False
        content = f"**{message.title}**\n{message.body}"
        for chunk in _split_discord_content(content, 2000):
            await channel.send(chunk)

        session_id = f"discord_{self._channel_id}"
        if hasattr(self._bot, "assistant") and self._bot.assistant:
            await self._bot.assistant.record_external_turn(session_id, "assistant", content)
        return True
