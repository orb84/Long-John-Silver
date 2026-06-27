"""
Agentic assistant for LJS.

Orchestrates intent routing, tool calling, conversation memory,
behavioral learning, and persona-based interactions. Supports
per-task LLM model routing so different tasks can use different
models and endpoints.

Delegates context building to ConversationBinding, LLM configuration
and completion functions to LLMTaskRuntime, and plan preparation to
PlanCoordinator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from loguru import logger
from typing import TYPE_CHECKING, AsyncIterator, Any

if TYPE_CHECKING:
    from src.core.actions.audit import ActionEventStore
    from src.core.config import SettingsManager
    from src.core.downloader import DownloadManager
    from src.core.categories.registry import CategoryRegistry

from src.ai.intent_router import IntentRouter, route_intent, ClarificationBuilder
from src.ai.language import detect_user_language_label
from src.ai.agent_loop_state import INTENTS_ELIGIBLE_FOR_REFLECTION
from src.ai.agent_loop import AgentLoopExecutor
from src.ai.streaming_agent_loop import StreamingAgentLoopExecutor
from src.core.models import AgentRunContext, AgentStreamEvent
from src.ai.tool_executor import ToolCallExecutor
from src.ai.torrent_selection import TorrentSelectionService
from src.ai.prompt_builder import PromptBuilder
from src.ai.tool_registry import ToolRegistry
from src.ai.category_resolver import CategoryResolver
from src.ai.tool_policy import AgentToolPolicy
from src.ai.memory_composer import PromptMemoryComposer
from src.core.models import Intent, Settings
from src.core.preferences import PreferenceManager
from src.core.conversation import ConversationManager
from src.core.behavior_tracker import BehaviorTracker
from src.ai.behavior_recorder import BehaviorRecorder
from src.core.release_groups import ReleaseGroupTracker
from src.ai.conversation_binding import ConversationBinding
from src.ai.download_context_policy import DownloadContextPolicy
from src.ai.llm_task_runtime import LLMTaskRuntime
from src.ai.error_presenter import AgentErrorPresenter
from src.ai.chat_presenter import AgentChatPresenter
from src.ai.plan_coordinator import PlanCoordinator
from src.ai.pending_actions import PendingActionContextBuilder
from src.ai.goal_state import AgentGoalStateManager
from src.ai.taste_signal_ingestion import TasteSignalIngestionService
from src.utils.detailed_logger import ChatLogger, StructuredReplyLogger


@dataclass
class AgentDependencies:
    """Dependencies for AIAssistant, injected via constructor.

    All services are optional except llm_client, settings, and
    preference_manager, which are required for normal operation.
    """

    settings: Settings
    preference_manager: PreferenceManager
    llm_client: object | None = None
    conversation_manager: ConversationManager | None = None
    intent_router: IntentRouter | None = None
    behavior_tracker: BehaviorTracker | None = None
    behavior_recorder: BehaviorRecorder | None = None
    tool_registry: ToolRegistry | None = None
    torrent_selection_service: TorrentSelectionService | None = None
    search_aggregator: object | None = None
    release_group_tracker: ReleaseGroupTracker | None = None
    database: object | None = None
    downloader: DownloadManager | None = None
    settings_manager: SettingsManager | None = None
    action_event_store: ActionEventStore | None = None
    memory_composer: PromptMemoryComposer | None = None
    chat_logger: ChatLogger | None = None
    structured_logger: StructuredReplyLogger | None = None
    category_registry: CategoryRegistry | None = None
    comms_registry: Any | None = None
    storage_monitor: Any | None = None
    taste_profiler: Any | None = None
    taste_signal_ingestor: TasteSignalIngestionService | None = None


@dataclass
class ExecutionContext:
    """Prepared execution context shared by run() and run_stream().

    Returned by _prepare_execution_context() with all common state
    collected to avoid duplicating intent routing, message building,
    and tool filtering logic.
    """
    intent: Intent
    messages: list[dict]
    allowed_tool_names: set[str]
    tool_definitions: list[dict] | None
    max_iterations: int
    task: str
    clarification: str | None = None
    pref_summary: str | None = None
    agent_context: AgentRunContext | None = None
    category_id: str | None = None





class AIAssistant:
    """Agentic AI assistant with tool calling, memory, and behavioral learning.

    Orchestrates the full agent lifecycle: intent routing, context
    building, plan preparation, LLM completion, and behavior recording.
    Delegates specialized work to ConversationBinding, LLMTaskRuntime,
    and PlanCoordinator.
    """

    def __init__(self, dependencies: AgentDependencies) -> None:
        """Initialize assistant with injected dependencies.

        Args:
            dependencies: All required and optional services for the assistant.
        """
        self._deps = dependencies
        self._llm_client = dependencies.llm_client
        self._settings = dependencies.settings
        self._category_registry = dependencies.category_registry
        self._comms_registry = dependencies.comms_registry
        self._preference_manager = dependencies.preference_manager
        self._conversation = dependencies.conversation_manager
        self._intent_router = dependencies.intent_router
        self._behavior_tracker = dependencies.behavior_tracker
        self._behavior_recorder = dependencies.behavior_recorder
        self._search_aggregator = dependencies.search_aggregator
        self._tool_registry = dependencies.tool_registry or ToolRegistry()
        self._tool_executor = ToolCallExecutor(self._tool_registry)
        persona_name = dependencies.settings.active_persona if dependencies.settings else "default"
        self._prompt_builder = PromptBuilder(persona_name)
        self._error_presenter = AgentErrorPresenter(persona_name)
        self._chat_presenter = AgentChatPresenter(persona_name)
        self._memory_composer = dependencies.memory_composer or PromptMemoryComposer(
            downloader=dependencies.downloader,
            database=dependencies.database,
            behavior_tracker=dependencies.behavior_tracker,
            preference_manager=dependencies.preference_manager,
            settings_manager=dependencies.settings_manager,
            action_event_store=dependencies.action_event_store,
            storage_monitor=dependencies.storage_monitor,
            taste_profiler=dependencies.taste_profiler,
        )
        self._conversation_binding = ConversationBinding(
            conversation_manager=dependencies.conversation_manager,
        )
        self._pending_actions = PendingActionContextBuilder(dependencies.database)
        self._goal_state = AgentGoalStateManager(dependencies.database)
        self._llm_runtime = LLMTaskRuntime(
            settings=dependencies.settings,
            llm_client=dependencies.llm_client,
            tool_registry=self._tool_registry,
        )
        self._plan_coordinator = PlanCoordinator(
            tool_executor=self._tool_executor,
            llm_client=self._llm_client,
            settings=dependencies.settings,
        )
        self._chat_logger = dependencies.chat_logger
        self._structured_logger = dependencies.structured_logger
        self._category_resolver = CategoryResolver(
            category_registry=dependencies.category_registry,
            settings=dependencies.settings,
        )
        self._tool_policy = AgentToolPolicy(settings=dependencies.settings)
        self._taste_signal_ingestor = dependencies.taste_signal_ingestor or (
            TasteSignalIngestionService(
                llm_client=dependencies.llm_client,
                settings=dependencies.settings,
                taste_profiler=dependencies.taste_profiler,
                category_registry=dependencies.category_registry,
            ) if dependencies.taste_profiler else None
        )
        self._preflight_intent_cache: dict[tuple[str, str, str], tuple[Intent, str | None]] = {}

    @property
    def tool_registry(self) -> ToolRegistry:
        """Expose the tool registry for web layer wiring."""
        return self._tool_registry

    async def preflight_intent_for_chat_status(
        self, user_prompt: str, session_id: str | None = None, user_id: str | None = None
    ) -> Intent:
        """Route intent early so bridges know whether a progress status is warranted.

        The result is cached for the immediately following assistant turn, so a
        bridge does not pay a second routing call simply to avoid noisy status
        pings for trivial CHAT messages such as thanks/acknowledgements.
        """
        pending_action_context = await self._pending_actions.build_for_session(session_id, current_user_prompt=user_prompt)
        routing_context = await self._conversation_binding.build_intent_routing_context(
            session_id,
            pending_action_context=pending_action_context,
        )
        intent = await self._route_intent(user_prompt, routing_context)
        self._preflight_intent_cache[(session_id or "default", user_id or "", user_prompt)] = (
            intent, pending_action_context
        )
        return intent

    async def generate_progress_message(
        self, user_prompt: str, tick: int = 0, intent: Intent | None = None
    ) -> str:
        """Generate or fall back to a short persona/language-aware progress line.

        Progress messages are user-visible before the tool loop has evidence.
        Never let a free-form LLM progress completion refuse, answer, or steer a
        side-effecting turn.  DOWNLOAD/CONFIG turns use deterministic persona
        messages only; other intents may use the LLM as a cosmetic helper, but
        refusal/error-like text is rejected.
        """
        fallback = self.format_progress_message(user_prompt, tick)
        intent_value = intent.value if isinstance(intent, Intent) else str(intent or "").upper()
        if tick != 0 or not self._llm_client or intent_value in {"DOWNLOAD", "CONFIG"}:
            return fallback
        language = detect_user_language_label(user_prompt)
        try:
            prompt = (
                "Write one very short in-character progress acknowledgement for a media-library assistant. "
                "It must be in the same language as the user's message"
                + (f" ({language})" if language else "")
                + ". Do not answer the request. Do not refuse. Do not apologize. Do not claim a specific tool has run. "
                "No more than 18 words. Vary the wording.\n\n"
                f"User message: {user_prompt}\n"
                f"Intent: {intent.value if intent else 'unknown'}"
            )
            response = await self._llm_client.completion(
                task="chat",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=50,
                temperature=0.7,
            )
            from src.utils.json_parser import LLMResponseParser

            text = LLMResponseParser.safe_extract_content(response).strip().strip('"')
            if text and not self._looks_like_bad_progress_ack(text):
                return text[:220]
        except Exception as exc:  # pragma: no cover - status generation must never break chat
            logger.debug(f"Progress acknowledgement generation fell back to templates: {exc}")
        return fallback

    @staticmethod
    def _looks_like_bad_progress_ack(text: str) -> bool:
        """Return True for progress text that looks like an answer/refusal."""
        lowered = str(text or "").strip().lower()
        if not lowered:
            return True
        bad_fragments = (
            "i can't help", "i cannot help", "i can’t help",
            "i'm sorry", "i’m sorry", "can't assist", "cannot assist",
            "i won't", "i will not", "not able to", "unable to",
        )
        return any(fragment in lowered for fragment in bad_fragments)

    async def _route_intent(self, user_prompt: str, pending_action_context: str | None) -> Intent:
        """Route user intent through the configured router/client."""
        if self._intent_router:
            return await self._intent_router.route(user_prompt, context=pending_action_context)
        routing_config = self._llm_runtime.get_llm_config("intent_routing")
        return await route_intent(
            user_prompt,
            model=routing_config["model"],
            api_base=routing_config["api_base"],
            api_key=routing_config["api_key"],
            context=pending_action_context,
        )

    def format_progress_message(self, user_prompt: str, tick: int = 0) -> str:
        """Format a deterministic in-chat progress update in the active persona.

        Websocket clients use this while a long tool-heavy assistant turn is
        still running, so the Captain sees useful status instead of silent dots.
        """
        return self._chat_presenter.progress(user_prompt, tick)

    def format_chat_error(self, operation: str, exc: BaseException | str) -> str:
        """Format a deterministic user-visible chat error in the active persona.

        Websocket and bridge layers use this when an exception occurs outside
        the normal LLM loop, so the user still receives a clear, useful error
        with the same Long John Silver voice as ordinary assistant replies.

        Args:
            operation: Operation or subsystem that failed.
            exc: Exception object or exact error detail.
        """
        return self._error_presenter.exception(operation, exc)

    async def record_external_turn(self, session_id: str, role: str, content: str) -> None:
        """Record an externally generated conversation turn (e.g. system notification).

        Args:
            session_id: The session identifier.
            role: The role ('user', 'assistant', etc.).
            content: The message content.
        """
        await self._conversation_binding.record_turn(session_id, role, content)

    def set_tool_registry(self, registry: ToolRegistry) -> None:
        """Replace the tool registry after construction.

        Used during startup wiring: the assistant is created with a
        default/empty ToolRegistry, then AgentToolCatalog builds the
        full registry and sets it here.
        """
        self._tool_registry = registry
        self._tool_executor = ToolCallExecutor(registry)
        self._llm_runtime.update_tool_registry(registry)
        self._plan_coordinator = PlanCoordinator(
            tool_executor=self._tool_executor,
            llm_client=self._llm_client,
            settings=self._settings,
        )

    def update_settings(self, settings: Settings) -> None:
        """Hot-reload settings without restarting the assistant."""
        self._settings = settings
        self._llm_runtime.update_settings(settings)
        self._prompt_builder.reload_persona(settings.active_persona)
        self._error_presenter = AgentErrorPresenter(settings.active_persona)
        self._chat_presenter = AgentChatPresenter(settings.active_persona)
        self._plan_coordinator.update_settings(settings)
        if self._llm_client:
            self._llm_client.update_config(settings.llm)
        logger.info("Assistant settings hot-reloaded.")

    async def _prepare_execution_context(
        self, user_prompt: str, session_id: str | None = None,
        user_id: str | None = None,
    ) -> ExecutionContext:
        """Build shared execution context for run() and run_stream().

        Performs intent routing, handles CLARIFY, builds system prompt,
        loads conversation context, and resolves tool definitions.

        Args:
            user_prompt: The user's message text.
            session_id: Optional session ID for conversation memory.
            user_id: Optional user ID for per-user preferences/behavior.

        Returns:
            ExecutionContext with intent, messages, tool config, etc.
        """
        pending_action_context = await self._pending_actions.build_for_session(session_id, current_user_prompt=user_prompt)
        cache_key = (session_id or "default", user_id or "", user_prompt)
        cached = self._preflight_intent_cache.pop(cache_key, None)
        if cached is not None:
            intent, cached_context = cached
            pending_action_context = cached_context
        else:
            routing_context = await self._conversation_binding.build_intent_routing_context(
                session_id,
                pending_action_context=pending_action_context,
            )
            intent = await self._route_intent(user_prompt, routing_context)

        if self._structured_logger:
            try:
                await self._structured_logger.log_intent(
                    query=user_prompt, routed_intent=intent.value, confidence=1.0
                )
            except Exception as le:
                logger.warning(f"Failed to log intent routing: {le}")

        if intent == Intent.CLARIFY:
            intent_hint = None
            if self._intent_router:
                intent_hint = getattr(self._intent_router, "_last_clarify_hint", None)
            clarification = ClarificationBuilder.build(user_prompt, intent_hint=intent_hint)
            await self._conversation_binding.record_turn(session_id, "user", user_prompt)
            await self._conversation_binding.record_turn(session_id, "assistant", clarification)
            return ExecutionContext(
                intent=intent,
                messages=[],
                allowed_tool_names=set(),
                tool_definitions=None,
                max_iterations=0,
                task="chat",
                clarification=clarification,
            )

        # Resolve the active category before prompt/tool selection.
        # The assistant should see category-owned guidance, but not a full
        # library dump unless the category confirms a matched item.  This keeps
        # short follow-ups and factual research from losing the useful recent
        # conversation context behind thousands of unrelated library entries.
        agent_context = self._category_resolver.build_context(user_prompt, intent)
        active_category = (
            self._category_registry.get(agent_context.category_id)
            if self._category_registry and agent_context.category_id
            else None
        )

        goal_context = ""
        if intent in {Intent.SEARCH, Intent.DOWNLOAD, Intent.CONFIG}:
            goal_context = await self._goal_state.build_context_and_update(
                session_id=session_id,
                user_prompt=user_prompt,
                intent=intent,
                category_id=active_category.category_id if active_category else None,
            )

        category_guidance = ""
        category_context_text = ""
        if active_category:
            category_guidance = (
                f"ACTIVE CATEGORY: {active_category.display_name} ({active_category.category_id})\n\n"
                f"{active_category.build_prompt_guidance(intent.value.lower(), settings=self._settings)}"
            )
            try:
                import json
                category_packet = await active_category.build_llm_context_packet(
                    user_message=user_prompt,
                    intent=intent,
                    settings=self._settings,
                    db=self._deps.database,
                    max_units=80,
                )
                category_context_text = json.dumps(
                    category_packet,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                )
                if len(category_context_text) > 10000:
                    category_context_text = category_context_text[:10000] + "\n... [category context truncated]"
                category_guidance += (
                    "\n\nCATEGORY LIBRARY CONTEXT PACKET "
                    "(owned by the active category; use this before asking the user to repeat known info):\n"
                    f"{category_context_text}"
                )
            except Exception as exc:
                logger.warning(f"Failed to build {active_category.category_id} context packet: {exc}")
        elif self._category_registry:
            briefs = [brief.model_dump() for brief in self._category_registry.router_briefs()]
            if briefs:
                category_guidance = (
                    "No single active category was confidently resolved. Use these compact router briefs "
                    f"only to ask a targeted clarification if needed: {briefs}"
                )

        # Determine platform formatting guidance based on session_id
        platform_guidance = ""
        if session_id:
            prefix = session_id.split("_")[0]
            # Try to get guidance from comms registry first
            bridge_info = None
            if hasattr(self, "_comms_registry") and self._comms_registry:
                bridge_info = self._comms_registry.get_registered_info(prefix)
            
            if bridge_info:
                factory = bridge_info["factory"]
                platform_guidance = factory.get_formatting_instructions()
            else:
                # Fallback mapping
                if prefix == "discord":
                    platform_guidance = (
                        "FORMATTING RULES FOR DISCORD:\n"
                        "- Always format responses using Discord-compatible Markdown.\n"
                        "- Use bold (`**text**`), italic (`*text*` or `_text_`), strikethrough (`~~text~~`), and inline code (`` `code` ``) or code blocks.\n"
                        "- Do NOT output HTML tags.\n"
                        "- NEVER use Markdown tables. They do not render correctly on Discord mobile. Instead, format lists or alternatives as clean vertical lines starting with emojis (e.g. ⚓, 🔹, ⭐).\n"
                        "- Use actual newline characters (ASCII 10) for line breaks. Do NOT escape them as '\\n' text.\n"
                        "- Keep messages concise and within Discord's 2000-character limit."
                    )
                elif prefix == "telegram":
                    platform_guidance = (
                        "FORMATTING RULES FOR TELEGRAM:\n"
                        "- Always format responses using Telegram-compatible Markdown.\n"
                        "- Use standard Markdown rules: bold (`*text*`), italic (`_text_`), inline code (`` `code` ``), and code blocks.\n"
                        "- Do NOT use MarkdownV2 syntax (do not escape periods or hyphens).\n"
                        "- Never output HTML tags.\n"
                        "- Avoid Markdown tables, since they render poorly on Telegram mobile screens. Instead, use emojis and bulleted text lists.\n"
                        "- Use actual newline characters for line breaks. Do NOT escape them as '\\n' text."
                    )
                elif prefix == "whatsapp":
                    platform_guidance = (
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
                elif prefix == "web":
                    platform_guidance = (
                        "FORMATTING RULES FOR WEB UI:\n"
                        "- You are talking via a premium Web UI dashboard. All standard Markdown is fully supported and rendered as HTML.\n"
                        "- Use standard Markdown tables, bold (`**text**`), italics, and bullet lists. They will be styled beautifully in the interface.\n"
                        "- Use actual newline characters for line breaks. Do NOT escape them as '\\n' text."
                    )

        memory_context = await self._memory_composer.compose(
            user_id=user_id,
            intent=intent,
            category_id=active_category.category_id if active_category else None,
        )
        planning_pref_context = memory_context
        if category_context_text:
            planning_pref_context = (
                f"{memory_context}\n\nACTIVE CATEGORY LIBRARY CONTEXT PACKET:\n{category_context_text}"
            )
        system_prompt = self._prompt_builder.build_system_prompt(
            intent,
            preferences_summary=memory_context,
            category_guidance=category_guidance,
            platform_guidance=platform_guidance,
            user_language_hint=detect_user_language_label(user_prompt),
            active_category_id=active_category.category_id if active_category else None,
        )

        allowed_tool_names = self._tool_policy.allowed_tool_names(intent, category=active_category)
        agent_context.allowed_tool_names = sorted(allowed_tool_names)
        tool_definitions = self._tool_policy.definitions_for_intent(
            self._tool_registry, intent, category=active_category,
        )
        system_prompt = self._append_live_tool_contract(system_prompt, allowed_tool_names, tool_definitions)
        logger.debug(
            "Prepared agent context: intent={} category={} tools={} messages_context_chars={}",
            intent.value,
            active_category.category_id if active_category else None,
            sorted(allowed_tool_names),
            len(system_prompt),
        )

        llm = self._settings.llm
        max_iterations = (
            llm.search_tool_iterations
            if intent in (Intent.SEARCH, Intent.DOWNLOAD)
            else llm.chat_tool_iterations
        )
        task = "download" if intent == Intent.DOWNLOAD else (
            "search" if intent == Intent.SEARCH else "chat"
        )

        await self._llm_runtime.ensure_context_metadata_for_task(task)
        context_budget = self._llm_runtime.context_budget_for_task(task)
        messages = [{"role": "system", "content": system_prompt}]
        if goal_context:
            messages.append({"role": "system", "content": goal_context})
            planning_pref_context = (
                f"{planning_pref_context}\n\n{goal_context}"
                if planning_pref_context else goal_context
            )
        if pending_action_context:
            messages.append({"role": "system", "content": pending_action_context})
            planning_pref_context = (
                f"{planning_pref_context}\n\n{pending_action_context}"
                if planning_pref_context else pending_action_context
            )
        fresh_download_request = DownloadContextPolicy.should_suppress_pending_candidates(user_prompt, intent)
        context_msgs = await self._conversation_binding.build_context_messages(
            session_id,
            user_id,
            user_prompt=user_prompt,
            max_turns=context_budget.get("max_recent_turns"),
            max_tokens=context_budget.get("conversation_tokens"),
            raw_recent_tokens=context_budget.get("raw_recent_conversation_tokens"),
            compressed_history_tokens=context_budget.get("compressed_history_tokens"),
            fresh_download_request=fresh_download_request,
        )
        messages.extend(context_msgs)
        messages.append({"role": "user", "content": user_prompt})

        await self._conversation_binding.record_turn(session_id, "user", user_prompt)

        return ExecutionContext(
            intent=intent,
            messages=messages,
            allowed_tool_names=allowed_tool_names,
            tool_definitions=tool_definitions,
            max_iterations=max_iterations,
            task=task,
            pref_summary=planning_pref_context,
            agent_context=agent_context,
            category_id=agent_context.category_id,
        )


    @staticmethod
    def _append_live_tool_contract(system_prompt: str, allowed_tool_names: set[str], tool_definitions: list[dict] | None) -> str:
        """Append compact, live tool-loop rules to every tool-using turn."""
        if not tool_definitions:
            return system_prompt
        names = sorted(allowed_tool_names or [])
        return (
            system_prompt
            + "\n\nLIVE TOOL CONTRACT:\n"
            + "- Use only the tools exposed in this turn; never invent historical/tool-alias names.\n"
            + "- Tool failures with ok=false/recoverable=true are evidence, not final answers. Try a different available source or corrected arguments before giving up, within the iteration limit.\n"
            + "- Prefer direct provider/category tools before broad web search when the category guidance says they can answer.\n"
            + "- Do not claim a download/search/action succeeded unless the latest tool result explicitly says it succeeded.\n"
            + f"- Available tool names now: {', '.join(names[:40])}."
        )

    async def run(self, user_prompt: str, session_id: str | None = None,
                  user_id: str | None = None) -> str:
        """Execute the agentic loop with intent routing and tool calling.

        Args:
            user_prompt: The user's message text.
            session_id: Optional session ID for conversation memory.
            user_id: Optional user ID for per-user preferences/behavior.

        Returns:
            The final response text.
        """
        if self._chat_logger:
            try:
                await self._chat_logger.log_message(
                    sender="USER", content=user_prompt, session_id=session_id or "default"
                )
            except Exception as le:
                logger.warning(f"Failed to log user message: {le}")

        ctx = await self._prepare_execution_context(user_prompt, session_id, user_id)
        if ctx.clarification:
            if self._chat_logger:
                try:
                    await self._chat_logger.log_message(
                        sender="ASSISTANT", content=ctx.clarification, session_id=session_id or "default"
                    )
                except Exception as le:
                    logger.warning(f"Failed to log clarification response: {le}")
            return ctx.clarification

        # Build conversational history context to pass to the planner
        history_str = ""
        prior_msgs = ctx.messages[:-1]
        history_parts = self._format_history_parts(prior_msgs)
        if history_parts:
            history_str = "\nRECENT CONVERSATION HISTORY:\n" + "\n".join(history_parts[-6:])

        planning_context = ctx.pref_summary
        if history_str:
            planning_context = f"{planning_context}\n{history_str}"

        if ctx.intent == Intent.DOWNLOAD:
            agent_plan, plan_exec = None, None
            ctx.messages[0]["content"] = self._download_tool_loop_contract(
                ctx.messages[0]["content"]
            )
        else:
            agent_plan, plan_exec, ctx.messages[0]["content"] = (
                await self._plan_coordinator.prepare_plan(
                    user_prompt=user_prompt, intent=ctx.intent,
                    system_prompt_content=ctx.messages[0]["content"],
                    allowed_tool_names=ctx.allowed_tool_names,
                    context=planning_context,
                )
            )

        if agent_plan and self._structured_logger:
            try:
                await self._structured_logger.log_plan(
                    user_goal=agent_plan.user_goal,
                    intent=ctx.intent.value,
                    steps=[s.model_dump() for s in agent_plan.steps],
                )
            except Exception as le:
                logger.warning(f"Failed to log structured plan: {le}")

        should_reflect = ctx.intent.value in INTENTS_ELIGIBLE_FOR_REFLECTION
        loop_executor = AgentLoopExecutor(
            tool_executor=self._tool_executor,
            llm_completion=self._llm_runtime.make_completion_fn(),
            error_presenter=self._error_presenter,
        )
        plan_trace_store = (
            getattr(self._deps.database, 'plan_traces', None)
            if self._deps.database else None
        )
        num_prior_messages = len(ctx.messages)
        loop_result = await loop_executor.execute(
            messages=ctx.messages,
            tool_definitions=ctx.tool_definitions,
            allowed_tool_names=ctx.allowed_tool_names,
            max_iterations=ctx.max_iterations,
            task=ctx.task,
            generation_options=self._llm_runtime.get_generation_options(
                self._llm_runtime.get_llm_config(ctx.task),
            ),
            planner=self._plan_coordinator.create_planner() if should_reflect else None,
            user_prompt=user_prompt,
            should_reflect=should_reflect,
            plan=agent_plan,
            plan_executor=plan_exec,
            plan_trace_store=plan_trace_store,
            session_id=session_id,
            active_category_id=ctx.category_id,
        )
        final_response = loop_result.response

        # Record all intermediate tool calls and tool responses
        for msg in ctx.messages[num_prior_messages:]:
            await self._conversation_binding.record_message(session_id, msg)

        await self._conversation_binding.record_turn(
            session_id, "assistant", final_response,
        )

        if self._chat_logger:
            try:
                await self._chat_logger.log_message(
                    sender="ASSISTANT", content=final_response, session_id=session_id or "default"
                )
            except Exception as le:
                logger.warning(f"Failed to log assistant response: {le}")

        if ctx.intent == Intent.DOWNLOAD and user_id and self._behavior_recorder:
            await self._behavior_recorder.record_download(
                user_id, item_name=user_prompt[:100],
            )

        await self._ingest_taste_from_turn(
            user_prompt=user_prompt,
            assistant_response=final_response,
            user_id=user_id,
            session_id=session_id,
            ctx=ctx,
        )

        return final_response

    async def run_stream(self, user_prompt: str, session_id: str | None = None,
                         user_id: str | None = None) -> AsyncIterator[str]:
        """Stream the agentic loop, yielding tokens as they arrive from the LLM.

        Tool calls are handled sequentially; the final text response
        is streamed token by token to the caller.

        Yields:
            String chunks of the response as they are generated.
        """
        if self._chat_logger:
            try:
                await self._chat_logger.log_message(
                    sender="USER", content=user_prompt, session_id=session_id or "default"
                )
            except Exception as le:
                logger.warning(f"Failed to log user message: {le}")

        ctx = await self._prepare_execution_context(user_prompt, session_id, user_id)
        if ctx.clarification:
            if self._chat_logger:
                try:
                    await self._chat_logger.log_message(
                        sender="ASSISTANT", content=ctx.clarification, session_id=session_id or "default"
                    )
                except Exception as le:
                    logger.warning(f"Failed to log clarification response: {le}")
            yield ctx.clarification
            return

        # Build conversational history context to pass to the planner
        history_str = ""
        prior_msgs = ctx.messages[:-1]
        history_parts = self._format_history_parts(prior_msgs)
        if history_parts:
            history_str = "\nRECENT CONVERSATION HISTORY:\n" + "\n".join(history_parts[-6:])

        planning_context = ctx.pref_summary
        if history_str:
            planning_context = f"{planning_context}\n{history_str}"

        if ctx.intent == Intent.DOWNLOAD:
            agent_plan, plan_exec = None, None
            ctx.messages[0]["content"] = self._download_tool_loop_contract(
                ctx.messages[0]["content"]
            )
        else:
            agent_plan, plan_exec, ctx.messages[0]["content"] = (
                await self._plan_coordinator.prepare_plan(
                    user_prompt=user_prompt, intent=ctx.intent,
                    system_prompt_content=ctx.messages[0]["content"],
                    allowed_tool_names=ctx.allowed_tool_names,
                    context=planning_context,
                )
            )

        if agent_plan and self._structured_logger:
            try:
                await self._structured_logger.log_plan(
                    user_goal=agent_plan.user_goal,
                    intent=ctx.intent.value,
                    steps=[s.model_dump() for s in agent_plan.steps],
                )
            except Exception as le:
                logger.warning(f"Failed to log structured plan: {le}")

        stream_executor = StreamingAgentLoopExecutor(
            tool_executor=self._tool_executor,
            stream_completion=self._llm_runtime.make_stream_completion_fn(),
            error_presenter=self._error_presenter,
            chat_presenter=self._chat_presenter,
        )
        plan_trace_store = (
            getattr(self._deps.database, 'plan_traces', None)
            if self._deps.database else None
        )
        num_prior_messages = len(ctx.messages)
        async for token in stream_executor.execute(
            messages=ctx.messages,
            tool_definitions=ctx.tool_definitions,
            allowed_tool_names=ctx.allowed_tool_names,
            max_iterations=ctx.max_iterations,
            task=ctx.task,
            generation_options=self._llm_runtime.get_generation_options(
                self._llm_runtime.get_llm_config(ctx.task),
            ),
            plan=agent_plan,
            plan_executor=plan_exec,
            plan_trace_store=plan_trace_store,
            session_id=session_id,
            active_category_id=ctx.category_id,
            user_prompt=user_prompt,
        ):
            yield token

        # Record all intermediate tool calls and tool responses
        for msg in ctx.messages[num_prior_messages:]:
            await self._conversation_binding.record_message(session_id, msg)

        await self._conversation_binding.record_turn(
            session_id, "assistant", stream_executor.last_content,
        )

        if self._chat_logger:
            try:
                await self._chat_logger.log_message(
                    sender="ASSISTANT", content=stream_executor.last_content, session_id=session_id or "default"
                )
            except Exception as le:
                logger.warning(f"Failed to log assistant response: {le}")

        if ctx.intent == Intent.DOWNLOAD and user_id and self._behavior_recorder:
            await self._behavior_recorder.record_download(
                user_id, item_name=user_prompt[:100],
            )

        await self._ingest_taste_from_turn(
            user_prompt=user_prompt,
            assistant_response=stream_executor.last_content,
            user_id=user_id,
            session_id=session_id,
            ctx=ctx,
        )


    @staticmethod
    def _download_tool_loop_contract(system_prompt: str) -> str:
        """Append the contract for LLM-led but schema-bound download turns.

        Download tasks keep the natural tool-calling loop so the LLM can reason
        across categories and follow-up context.  The deterministic structured
        pre-plan is intentionally not used for DOWNLOAD because model-authored
        dependency placeholders repeatedly caused runtime failures.
        """
        return (
            system_prompt
            + "\n\nDOWNLOAD AGENT CONTRACT:\n"
            + "- You are free to reason and call tools naturally, but only call registered tools exactly as declared.\n"
            + "- Do not write ${step.path} placeholders, '<URL from result>' prose placeholders, or guessed internal JSON paths in tool arguments. Tools return stable handles, result_set_id, candidate_id, candidate_ids, and next_actions; use those handles.\n"
            + "- Treat the ACTIVE GOAL STATE and PENDING ACTION CONTEXT as structured task state. Continue an active goal when the current user message semantically refers to it.\n"
            + "- For torrent discovery, call search_media_torrents with literal item constraints and a category-neutral search_scope. The owning category resolves latest season, missing units, packs, fallbacks, and naming schemas.\n"
            + "- When search results return candidate_picker, summarize candidates by result_set_id/candidate_id/title/size/seeders and choose by candidate_id. Ask to inspect more detail when coverage is ambiguous.\n"
            + "- Never present candidates outside the requested season/episode as options to satisfy that request; wrong-scope rows are diagnostic noise unless the user explicitly broadens the target.\n"
            + "- Apply configured media language as a constraint when the category context/tool result provides it. Use category guidance for language-tag semantics and do not invent cross-category language priorities.\n"
            + "- If storage context is WARNING/CRITICAL and a candidate size is known, call check_storage_capacity before claiming it cannot fit; deterministic storage math belongs to that tool/queue preflight, not prose guesses.\n"
            + "- Queue only when the chosen candidate or batch is clear and queue_download confirms status=queued or returns download IDs.\n"
            + "- If any state-changing download tool runs (queue, cancel, remove, pause, resume, restart, priority), the final answer must explicitly report each action result with any download_id/status returned by the tool. Do not bury or omit a cancellation because you then searched again.\n"
            + "- Do not cancel/remove an already queued or active download merely because the user asks for a better match or corrects constraints. Treat that as a search/refinement first; cancel/remove only after explicit user instruction or confirmation from a manage_downloads confirmation_required result.\n"
            + "- Never say a download was queued, started, cancelled, paused, resumed, or removed unless the latest tool result explicitly reports that state change.\n"
            + "- If a tool returns ok=false with recoverable=true, adjust the next tool call using its next_actions instead of ending with a crash.\n"
        )

    async def _ingest_taste_from_turn(
        self,
        *,
        user_prompt: str,
        assistant_response: str,
        user_id: str | None,
        session_id: str | None,
        ctx: ExecutionContext,
    ) -> None:
        """Best-effort post-turn taste ingestion.

        This must never break the chat response. It records raw taste evidence
        after the assistant has answered, using LLM-led extraction and
        category-owned metadata enrichment.
        """
        if not self._taste_signal_ingestor:
            return
        try:
            result = await self._taste_signal_ingestor.ingest_user_turn(
                user_message=user_prompt,
                assistant_response=assistant_response,
                user_id=user_id,
                session_id=session_id,
                active_category_id=ctx.category_id,
                intent=ctx.intent,
            )
            if result.stored:
                logger.debug(f"Taste ingestion stored {result.stored} signal(s) for session {session_id or 'default'}")
        except Exception as exc:  # pragma: no cover - defensive around memory side effects
            logger.debug(f"Taste ingestion skipped after assistant turn: {exc}")

    async def run_stream_events(
        self, user_prompt: str, session_id: str | None = None,
        user_id: str | None = None,
    ) -> AsyncIterator[AgentStreamEvent]:
        """Stream the agentic loop with typed events for UI integration.

        Yields AgentStreamEvent objects with type 'text', 'tool_start',
        'tool_end', or 'error'. This enables the UI to show status
        indicators during tool execution.

        Args:
            user_prompt: The user's message text.
            session_id: Optional session ID for conversation memory.
            user_id: Optional user ID for per-user preferences/behavior.

        Yields:
            AgentStreamEvent objects.
        """
        async for chunk in self.run_stream(user_prompt, session_id, user_id):
            yield AgentStreamEvent(type="text", content=chunk)

    def _format_tool_message_for_planner(self, msg: dict) -> str:
        """Format a tool message compactly for the planner context, keeping it clean and token-efficient."""
        name = msg.get("name") or "result"
        content = msg.get("content") or ""
        
        # If it is search results, format as clean summary
        if name in ("search_media_torrents", "search_torrents"):
            import json
            try:
                data = json.loads(content)
                if isinstance(data, dict):
                    candidates = data.get("candidates", [])
                    query = data.get("query", "")
                    result_set_id = data.get("result_set_id")
                    lines = [f"TOOL ({name}) query: {query}"]
                    if result_set_id:
                        lines.append(f"  result_set_id: {result_set_id}")
                    for c in candidates:
                        idx = c.get("index") or c.get("option_index")
                        title = c.get("title", "")
                        size = c.get("size", "")
                        seeders = c.get("seeders", "")
                        cid = c.get("candidate_id")
                        rid = c.get("result_set_id") or result_set_id
                        id_part = f", candidate_id: {cid}, result_set_id: {rid}" if cid else ""
                        lines.append(f"  - Option {idx}: {title} (Size: {size}, Seeders: {seeders}{id_part})")
                    batch = data.get("batch_recommendation") if isinstance(data, dict) else None
                    if isinstance(batch, dict) and batch.get("queue_download_arguments"):
                        lines.append("  BATCH_RECOMMENDATION: use these exact queue_download arguments when the user confirms the recommended batch:")
                        try:
                            lines.append("  " + json.dumps(batch.get("queue_download_arguments"), ensure_ascii=False))
                        except Exception:
                            lines.append(f"  {batch.get('queue_download_arguments')}")
                        for group in batch.get("groups") or []:
                            lines.append(
                                f"  - Recommended {group.get('unit')}: candidate_id {group.get('recommended_candidate_id')} "
                                f"({group.get('title')}, Seeders: {group.get('seeders')})"
                            )
                    return "\n".join(lines)
                elif isinstance(data, list):
                    lines = [f"TOOL ({name}) results:"]
                    for idx, c in enumerate(data):
                        title = c.get("title", "")
                        size = c.get("size", "")
                        seeders = c.get("seeders", "")
                        cid = c.get("candidate_id")
                        rid = c.get("result_set_id")
                        id_part = f", candidate_id: {cid}, result_set_id: {rid}" if cid else ""
                        lines.append(f"  - Option {idx+1}: {title} (Size: {size}, Seeders: {seeders}{id_part})")
                    return "\n".join(lines)
            except Exception:
                pass
                
        # Fallback to simple truncation
        truncated = content[:1000] + "..." if len(content) > 1000 else content
        return f"TOOL ({name}): {truncated}"

    def _format_history_parts(self, prior_msgs: list[dict]) -> list[str]:
        """Format prior message list for context history passed to the planner."""
        history_parts = []
        for msg in prior_msgs:
            role = msg.get("role", "user")
            content = msg.get("content") or ""
            if role in ("user", "assistant") and content:
                if role == "assistant" and content.startswith("__TOOL_CALLS__:"):
                    history_parts.append("ASSISTANT: [Tool Calls]")
                else:
                    history_parts.append(f"{role.upper()}: {content}")
            elif role == "tool" and content:
                history_parts.append(self._format_tool_message_for_planner(msg))
        return history_parts
