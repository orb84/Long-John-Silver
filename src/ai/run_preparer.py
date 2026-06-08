"""
Agent run preparer for LJS.

Builds shared assistant run state for streaming and non-streaming
execution, removing duplicated setup logic from AIAssistant.run()
and AIAssistant.run_stream().
"""

from loguru import logger
from typing import Optional

from src.core.models import Intent, AgentPlan, PreparedAgentRun, Settings
from src.ai.reasoning import ReasoningPlanner
from src.ai.intent_router import IntentRouter
from src.core.preferences import PreferenceManager
from src.core.conversation import ConversationManager
from src.core.behavior_tracker import BehaviorTracker
from src.ai.prompt_builder import PromptBuilder
from src.ai.pending_actions import PendingActionContextBuilder
from src.utils.item_matcher import ItemMatcher


class AgentRunPreparer:
    """Builds shared assistant run state for streaming and non-streaming execution.

    Extracts the common setup logic from AIAssistant.run() and
    AIAssistant.run_stream() so that prompt construction, intent
    routing, preference loading, and tool selection only happens
    in one place.
    """

    def __init__(
        self,
        settings: Settings,
        preference_manager: PreferenceManager,
        conversation_manager: Optional[ConversationManager] = None,
        behavior_tracker: Optional[BehaviorTracker] = None,
        intent_router: Optional[IntentRouter] = None,
        prompt_builder: Optional[PromptBuilder] = None,
        llm_client: Optional[object] = None,
        pending_action_builder: Optional[PendingActionContextBuilder] = None,
        tool_names_by_intent: Optional[dict] = None,
        search_tool_iterations: int = 10,
        chat_tool_iterations: int = 4,
    ):
        """Initialize the run preparer.

        Args:
            settings: Application settings.
            preference_manager: Preference manager for loading user preferences.
            conversation_manager: Optional conversation manager for memory.
            behavior_tracker: Optional behavior tracker for profiling.
            intent_router: Optional IntentRouter for classification.
            prompt_builder: Optional PromptBuilder for system prompts.
            llm_client: Optional TaskLLMClient for LLM calls.
            pending_action_builder: Optional builder for structured pending result-set context.
            tool_names_by_intent: Mapping from Intent to allowed tool name sets.
            search_tool_iterations: Max iterations for search/download intents.
            chat_tool_iterations: Max iterations for chat intents.
        """
        self._settings = settings
        self._preference_manager = preference_manager
        self._conversation = conversation_manager
        self._behavior_tracker = behavior_tracker
        self._intent_router = intent_router
        self._prompt_builder = prompt_builder
        self._llm_client = llm_client
        self._pending_action_builder = pending_action_builder
        self._tool_names_by_intent = tool_names_by_intent or {}
        self._search_tool_iterations = search_tool_iterations
        self._chat_tool_iterations = chat_tool_iterations

    async def prepare(
        self,
        user_prompt: str,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        include_plan: bool = True,
    ) -> PreparedAgentRun:
        """Prepare prompt, context, tools, and task routing for one assistant request.

        Args:
            user_prompt: The user's message text.
            session_id: Optional session ID for conversation memory.
            user_id: Optional user ID for per-user preferences.
            include_plan: Whether to generate a reasoning plan for complex intents.

        Returns:
            A PreparedAgentRun with all resolved state for the agentic loop.
        """
        # Step 1: Route intent with structured pending action context.
        pending_action_context = ""
        if self._pending_action_builder:
            pending_action_context = await self._pending_action_builder.build_for_session(session_id, current_user_prompt=user_prompt)
        intent = await self._route_intent(user_prompt, context=pending_action_context)

        # Step 2: Handle CLARIFY — return early with clarification message
        if intent == Intent.CLARIFY:
            return PreparedAgentRun(intent=Intent.CLARIFY, task="chat")

        # Step 3: Load preferences and behavioral context
        pref_summary = await self._preference_manager.get_summary(user_id=user_id)
        
        # Build tracked items summary for the planner and assistant context
        tracked_summaries = []
        for item in self._settings.tracked_items:
            lang = getattr(item, "language", None)
            if lang:
                tracked_summaries.append(f"- Item: '{item.key}', configured language: {lang}")
        
        if tracked_summaries:
            tracked_context = "TRACKED MEDIA PREFERENCES (YOU MUST USE THESE CONFIGURED LANGUAGES FOR SEARCH AND DOWNLOAD REQUESTS OF THESE ITEMS):\n" + "\n".join(tracked_summaries)
            pref_summary = f"{pref_summary}\n\n{tracked_context}"
            
        behavior_context = await self._build_behavioral_context(user_id)

        # Step 4: Build system prompt
        system_prompt = self._prompt_builder.build_system_prompt(
            intent, pref_summary, behavior_context=behavior_context,
        )

        # Step 5: Generate structured plan for complex intents (SEARCH, DOWNLOAD)
        agent_plan = None
        if include_plan and intent in (Intent.SEARCH, Intent.DOWNLOAD):
            # Fetch recent conversation context messages to provide to the planner
            context_messages = await self._build_context_messages(
                session_id, user_id, user_prompt=user_prompt,
            )
            
            # Format context messages as a readable chat transcript
            history_str = ""
            if context_messages:
                history_parts = []
                for msg in context_messages:
                    role = msg.get("role", "user")
                    # Use 'or' instead of default to handle case where content key exists but is None (e.g. tool calls)
                    content = msg.get("content") or ""
                    if role in ("user", "assistant") and content:
                        history_parts.append(f"{role.upper()}: {content}")
                if history_parts:
                    history_str = "\nRECENT CONVERSATION HISTORY:\n" + "\n".join(history_parts[-6:]) # Grab last 6 turns for concise context

            planning_context = f"{pref_summary}\n{history_str}"

            planner = self._create_planner()
            agent_plan = await planner.generate_plan(
                user_prompt, intent, context=planning_context,
            )
            if agent_plan:
                # Post-process constraints and arguments to bind tracked item names and
                # fill configured language only when the planner omitted one.
                for item in self._settings.tracked_items:
                    lang = getattr(item, "language", None)
                    if not lang:
                        continue
                    
                    is_mentioned = ItemMatcher.is_item_mentioned(
                        tracked_key=item.key,
                        prompt=user_prompt,
                        goal=agent_plan.user_goal or "",
                        steps=agent_plan.steps,
                    )
                    
                    if is_mentioned:
                        logger.info(
                            f"[Tracked Item Binding] Tracked item '{item.key}' detected in plan. "
                            "Binding exact item key and filling configured language only when the plan omitted language."
                        )
                        plan_has_language = bool(agent_plan.constraints.get("language"))
                        for step in agent_plan.steps:
                            if isinstance(step.arguments, dict) and step.arguments.get("language"):
                                plan_has_language = True
                                break
                        if not plan_has_language:
                            agent_plan.constraints["language"] = lang
                        for step in agent_plan.steps:
                            if not isinstance(step.arguments, dict):
                                continue
                            if not plan_has_language and step.tool_name in ("search_torrents", "search_media_torrents"):
                                step.arguments["language"] = lang
                            for arg_key in ("name", "title", "item_name"):
                                val = step.arguments.get(arg_key)
                                if val and isinstance(val, str) and ItemMatcher.fuzzy_match_names(item.key, val):
                                    logger.info(f"[Item Name Mapping] Correcting step argument '{arg_key}': '{val}' -> '{item.key}'")
                                    step.arguments[arg_key] = item.key
                
                system_prompt += f"\n\nGoal: {agent_plan.user_goal}"
                if agent_plan.constraints:
                    constr_str = "; ".join(
                        f"{k}={v}" for k, v in agent_plan.constraints.items()
                    )
                    system_prompt += f"\nConstraints: {constr_str}"
                
                if agent_plan.steps:
                    step_summaries = []
                    for step in agent_plan.steps[:5]:
                        step_summaries.append(f"{step.id}:{step.tool_name} args={step.arguments}")
                    system_prompt += (
                        "\n\nSTRUCTURED PLAN ADVISORY (not automatically executed): "
                        + " | ".join(step_summaries)
                        + "\nUse this only as a hint. Concrete actions must still be chosen through the "
                        "current tool-call channel, validated against current tool schemas, and adapted to tool results."
                    )

        # Step 6: Build messages
        messages = [{"role": "system", "content": system_prompt}]
        if pending_action_context:
            messages.append({"role": "system", "content": pending_action_context})
        context_messages = await self._build_context_messages(
            session_id, user_id, user_prompt=user_prompt,
        )
        messages.extend(context_messages)
        messages.append({"role": "user", "content": user_prompt})

        # Step 7: Record user turn in conversation memory
        if session_id and self._conversation:
            await self._conversation.add_turn(session_id, "user", user_prompt)

        # Step 8: Determine task name
        task = self._task_for_intent(intent)

        # Step 9: Determine iteration limit
        max_iterations = (
            self._search_tool_iterations
            if intent in (Intent.SEARCH, Intent.DOWNLOAD)
            else self._chat_tool_iterations
        )

        # Step 10: Determine allowed tools
        allowed_tool_names = self._tool_names_by_intent.get(intent, set())

        # Step 11: Should record behavior?
        should_record = intent == Intent.DOWNLOAD and user_id is not None

        return PreparedAgentRun(
            intent=intent,
            task=task,
            messages=messages,
            tool_definitions=None,  # Filled by caller based on their tool registry
            allowed_tool_names=allowed_tool_names,
            max_iterations=max_iterations,
            should_record_download_behavior=should_record,
            system_prompt=system_prompt,
            agent_plan=agent_plan,
        )

    def build_clarification_response(self) -> str:
        """Build a targeted clarification message for CLARIFY intent.

        Returns:
            A clarification prompt string.
        """
        return (
            "I'm not sure what you'd like me to do. Could you clarify? "
            "For example:\n"
            "- **Search** for an item? (e.g., 'Find info on Severance')\n"
            "- **Download** something? (e.g., 'Download Severance S02E01')\n"
            "- **Configure** a setting? (e.g., 'Add Breaking Bad to tracked items')\n"
        )

    async def _route_intent(self, message: str) -> Intent:
        """Route user intent using IntentRouter or legacy method."""
        if self._intent_router:
            return await self._intent_router.route(message)

        # Legacy fallback
        from src.ai.intent_router import route_intent
        from src.core.models import Settings
        llm = self._settings.llm
        return await route_intent(
            message,
            model=llm.get_model_for_task("intent_routing"),
            api_base=llm.get_api_base_for_task("intent_routing"),
            api_key=llm.get_api_key_for_task("intent_routing"),
            context=context,
        )

    async def _build_behavioral_context(self, user_id: Optional[str]) -> str:
        """Build behavioral profile string for the prompt."""
        if not user_id or not self._behavior_tracker:
            return ""
        profile = await self._behavior_tracker.get_behavior_profile(user_id)
        return self._behavior_tracker.format_profile_for_prompt(profile)

    async def _build_context_messages(
        self, session_id: Optional[str], user_id: Optional[str],
        user_prompt: Optional[str] = None,
    ) -> list[dict]:
        """Build conversation context messages from memory."""
        if not session_id or not self._conversation:
            return []

        context_messages = await self._conversation.get_context(session_id)

        if user_prompt and self._conversation.has_vector_store():
            relevant = await self._conversation.get_relevant_context(
                session_id, user_prompt, top_k=3
            )
            if relevant:
                context_parts = []
                for ctx in relevant:
                    similarity = ctx.get("similarity", 0)
                    if similarity >= 0.5:
                        role = ctx.get("role", "user")
                        # Use 'or' instead of default to handle case where content key exists but is None
                        content = ctx.get("content") or ""
                        context_parts.append(f"[{role}]: {content}")
                if context_parts:
                    relevant_text = "\n".join(context_parts)
                    context_messages.insert(0, {
                        "role": "system",
                        "content": f"Relevant past context:\n{relevant_text}",
                    })

        return context_messages

    def _task_for_intent(self, intent: Intent) -> str:
        """Map intent to task name for LLM routing."""
        if intent == Intent.DOWNLOAD:
            return "download"
        elif intent == Intent.SEARCH:
            return "search"
        return "chat"

    def _create_planner(self) -> ReasoningPlanner:
        """Create a ReasoningPlanner using TaskLLMClient if available."""
        if self._llm_client:
            return ReasoningPlanner(llm_client=self._llm_client)
        # Legacy fallback
        from src.core.models import Settings
        llm = self._settings.llm
        return ReasoningPlanner(
            model=llm.get_model_for_task("intent_routing"),
            api_base=llm.get_api_base_for_task("intent_routing"),
            api_key=llm.get_api_key_for_task("intent_routing"),
        )