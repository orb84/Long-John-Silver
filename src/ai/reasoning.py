"""
Reasoning planner for LJS.

Builds optional, advisory plans for complex tasks (SEARCH, DOWNLOAD).
The normal tool-calling loop remains authoritative: planner JSON may add
compact goal/constraint hints, but concrete actions still have to be made
through the current registered tools and validated at execution time.
An optional reflection step evaluates whether the results are sufficient.
"""

import json
from typing import Any, Optional

from loguru import logger

from src.core.models import Intent, PlanStep, AgentPlan
from src.utils.circuit_breaker import CircuitBreaker
from src.utils.json_parser import LLMResponseParser


class ReasoningPlanner:
    """Generates optional advisory plans for complex agent tasks.

    For SEARCH and DOWNLOAD intents, the planner can produce a compact
    structured sketch that is fed back to the normal tool-calling loop as
    context. It does not execute tools and does not validate the user's
    natural-language semantics. It can also reflect on tool results to decide
    whether more search is needed.
    """

    _INTENT_GUIDES: dict[Intent, str] = {
        Intent.SEARCH: (
            "1. If the user asks a factual media question about a TV show, movie, episode, actor, cast, creator, release date, season count, rating, or artwork, FIRST call `metadata_lookup` with the best media_type (tv/movie/auto). Do not start with `web_search` for facts that metadata services can answer.\n"
            "2. Use the CATEGORY LIBRARY CONTEXT PACKET when present. If it is absent or stale, call `enquire_about_media` for local library/tracked-item state. Do not invent category-specific read-only tools.\n"
            "3. Use generic web/review research only when metadata_lookup/enquire_about_media cannot answer, when the user asks for current news/articles/reviews, or when metadata_lookup returns no useful result.\n"
            "4. Synthesize findings into a direct answer grounded in tool results. If tools fail, report the failure honestly and try the next sensible read-only source rather than inventing facts.\n"
        ),
        Intent.DOWNLOAD: (
            "1. TOOL PHILOSOPHY: use the small generic chain only: CATEGORY LIBRARY CONTEXT PACKET and/or `enquire_about_media` to understand state, `search_media_torrents` to discover candidates, and `queue_download` to queue chosen candidate IDs. Do not call or invent category-specific download/read micro-tools.\n"
            "2. Check the RECENT CONVERSATION HISTORY. If the user has just replied to a list of torrent options and those options include `candidate_id`/`result_set_id`, generate a plan with one `queue_download` step containing the tracked media `name`, `candidate_id`, and `result_set_id`. Use legacy `option_index` only when no stable IDs are available.\n"
            "3. Otherwise, for fresh searches, generate a plan with a single `search_media_torrents` step for the requested category item or unit. DO NOT pre-schedule `queue_download` unless the user is confirming a previous candidate recommendation. The model must inspect search_media_torrents candidate_picker/batch_recommendation IDs and queue by candidate_id/result_set_id, not raw magnets or invented placeholders.\n"
            "4. Pass the exact tracked item name, structured unit arguments when relevant, and the item configured language unless the user explicitly overrides it. Use local episode/file language data from context to avoid mixing languages silently.\n"
            "5. Evaluate search results with hard filters first: exact unit/pack coverage, required language or acceptable multi-audio, magnet availability, resolution, per-unit size/bitrate, seeders, codecs, and release groups. Seeders are a first-class availability metric: equivalent candidates should be ordered by higher seed count. Queue with candidate_id/result_set_id only when the choice is clear; otherwise ask the user.\n"
            "6. If the user asked for all/remaining/missing/multiple units and search_media_torrents returns batch_recommendation, use it as evidence and queue every recommended candidate only after checking it against the context and language rules. Do not generate another identical search step just to see the same result_set again.\n"
        ),
    }

    _MAX_REPAIR_ATTEMPTS = 2
    """Maximum structured-planner repair attempts after the initial response.

    These retries repair only objective contract failures such as invalid JSON,
    unavailable tool names, or malformed step structures.  They do not try to
    semantically prove that a plan matches the user's request.
    """


    _TOOL_ALIASES = {
        "WebSearch": "web_search",
        "webSearch": "web_search",
        "web_search_tool": "web_search",
        "SearchWeb": "web_search",
        "MetadataLookup": "metadata_lookup",
        "TMDBLookup": "metadata_lookup",
        "ExtractMetadata": "browser_extract",
        "ReadWebPage": "read_web_page",
    }

    @classmethod
    def _available_tool_names(cls, tool_schemas: list[dict] | None) -> set[str]:
        names: set[str] = set()
        for schema in tool_schemas or []:
            func = schema.get("function", {}) if isinstance(schema, dict) else {}
            name = func.get("name")
            if name:
                names.add(str(name))
        return names

    @classmethod
    def _canonical_tool_name(cls, name: str, available: set[str]) -> str:
        if name in available:
            return name
        alias = cls._TOOL_ALIASES.get(name)
        if alias in available:
            return alias
        snake = ""
        for idx, ch in enumerate(str(name or "")):
            if ch.isupper() and idx > 0 and str(name)[idx - 1].islower():
                snake += "_"
            snake += ch.lower()
        if snake in available:
            return snake
        return name

    @classmethod
    def _validate_plan_contract(
        cls,
        plan: AgentPlan,
        *,
        available_tools: set[str],
    ) -> AgentPlan:
        """Validate only objective planner/tool contracts.

        This deliberately does **not** attempt semantic matching between the
        user's natural-language request and the plan.  A structured plan is only
        an advisory hint for the normal agent loop, so the safe checks here are
        limited to things the application can know objectively:

        - tool names must exist in the current tool surface;
        - historical aliases may be canonicalized to real exposed tools;
        - arguments must be dictionaries;
        - dependencies must refer to earlier step ids.

        Bad plans are discarded; they are never repaired with lexical overlap
        heuristics and never become authoritative execution.
        """
        seen_step_ids: set[str] = set()
        for index, step in enumerate(plan.steps):
            if not step.id:
                step.id = f"step_{index + 1}"
            canonical = cls._canonical_tool_name(step.tool_name, available_tools)
            if canonical != step.tool_name:
                logger.info("Canonicalized planner tool '{}' -> '{}'", step.tool_name, canonical)
                step.tool_name = canonical
            if available_tools and step.tool_name not in available_tools:
                raise ValueError(
                    f"Planner selected unavailable tool '{step.tool_name}'. "
                    f"Available tools: {sorted(available_tools)}"
                )
            if not isinstance(step.arguments, dict):
                raise ValueError(f"Planner step '{step.id}' arguments must be an object/dict.")
            unknown_dependencies = [dep for dep in (step.depends_on or []) if dep not in seen_step_ids]
            if unknown_dependencies:
                raise ValueError(
                    f"Planner step '{step.id}' depends on unknown or later step(s): {unknown_dependencies}."
                )
            seen_step_ids.add(step.id)
        return plan

    def __init__(
        self,
        llm_client: Optional[object] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
        model: Optional[str] = None,
        api_base: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        """Initialize the reasoning planner.

        Args:
            llm_client: TaskLLMClient for LLM calls. If provided,
                model/api_base/api_key are ignored.
            circuit_breaker: Optional circuit breaker for LLM calls.
            model: LLM model (legacy, used if llm_client is None).
            api_base: Optional API base URL (legacy).
            api_key: Optional API key (legacy).
        """
        self._llm_client = llm_client
        self._model = model
        self._api_base = api_base
        self._api_key = api_key
        self._breaker = circuit_breaker or CircuitBreaker(
            "reasoning", failure_threshold=3, recovery_seconds=30,
        )

    async def generate_plan(self, user_prompt: str, intent: Intent,
                             context: str = "", tool_schemas: list[dict] | None = None) -> Optional[AgentPlan]:
        """Ask the LLM to produce a structured plan as typed JSON.

        Args:
            user_prompt: The user's request.
            intent: The classified intent.
            context: Additional context (preferences, behavioral profile).
            tool_schemas: Optional list of available tool definitions with parameters.

        Returns:
            An AgentPlan with typed PlanSteps, or None if the model
            doesn't support planning or cannot produce valid JSON.
        """
        if intent not in self._INTENT_GUIDES:
            return None

        prompt = self._build_plan_prompt(user_prompt, intent, context, tool_schemas=tool_schemas)
        base_prompt = prompt
        available_tools = self._available_tool_names(tool_schemas)
        raw_json = None

        for attempt in range(self._MAX_REPAIR_ATTEMPTS + 1):
            try:
                raw_text = await self._call_llm(prompt, intent)
                raw_json = self._extract_json(raw_text)
                plan = AgentPlan.model_validate(raw_json)
                plan.intent = intent
                plan = self._validate_plan_contract(
                    plan,
                    available_tools=available_tools,
                )
                
                # Detailed tree-style logging of the plan steps and argument payloads
                steps_log = "\n".join(
                    f"  [Step {i+1}] ID: '{step.id}'\n"
                    f"     - Tool: '{step.tool_name}'\n"
                    f"     - Arguments: {json.dumps(step.arguments)}\n"
                    f"     - Depends On: {step.depends_on}\n"
                    f"     - Success Condition: {step.success_condition}"
                    for i, step in enumerate(plan.steps)
                )
                logger.info(
                    f"Generated structured plan for {intent.value}:\n"
                    f"User Goal: {plan.user_goal}\n"
                    f"Constraints: {plan.constraints}\n"
                    f"Steps:\n{steps_log}"
                )
                return plan
            except Exception as e:
                logger.warning(
                    f"Plan generation attempt {attempt + 1} failed: {e}"
                )
                if attempt < self._MAX_REPAIR_ATTEMPTS:
                    prompt = self._build_repair_prompt(
                        raw_json if raw_json else raw_text if 'raw_text' in dir() else "",
                        str(e),
                        original_prompt=base_prompt,
                    )

        logger.warning("All plan generation attempts failed. Continuing without plan.")
        return None

    def _build_plan_prompt(self, user_prompt: str, intent: Intent,
                            context: str, tool_schemas: list[dict] | None = None) -> str:
        """Build the prompt asking the LLM for structured JSON output.

        Args:
            user_prompt: The user's request.
            intent: The classified intent.
            context: Additional context for the planner.
            tool_schemas: Optional list of available tool definitions with parameters.

        Returns:
            A prompt string requesting JSON matching AgentPlan schema.
        """
        # Keep planner prompts compact. The full Pydantic schema is several
        # thousand characters and caused simple single-item requests to exceed
        # local model budgets once category context and tool descriptions were
        # added. Validation still happens with AgentPlan.model_validate().
        schema_json = (
            '{"intent":"DOWNLOAD|SEARCH", "user_goal":"string", '
            '"constraints":{}, "steps":[{"id":"string", '
            '"tool_name":"available_tool_name", "arguments":{}, '
            '"depends_on":[], "success_condition":"string"}]}'
        )
        intent_guide = self._INTENT_GUIDES[intent]

        prompt = (
            "You are a planning assistant that produces structured plans "
            "for media automation. The plan is advisory only; the live tool-calling agent will decide and execute. Return ONLY valid JSON — no other text.\n\n"
            f"Request: {user_prompt}\n"
            f"Type of task: {intent.value}\n"
            f"Recommended approach:\n{intent_guide}\n"
        )
        if context:
            prompt += f"Context:\n{context}\n\n"

        if tool_schemas:
            prompt += "Available Tools (use EXACTLY these tool names; arguments must use the listed parameter names):\n\n"
            for s in tool_schemas:
                func = s.get("function", {})
                params = func.get("parameters") or {}
                props = params.get("properties") if isinstance(params, dict) else {}
                required = params.get("required") if isinstance(params, dict) else []
                prompt += f"- Tool: '{func.get('name')}'\n"
                prompt += f"  Description: {func.get('description')}\n"
                if isinstance(props, dict) and props:
                    param_bits = []
                    for pname, pschema in props.items():
                        if not isinstance(pschema, dict):
                            param_bits.append(str(pname))
                            continue
                        ptype = pschema.get("type") or "any"
                        desc = str(pschema.get("description") or "").strip()
                        if len(desc) > 90:
                            desc = desc[:87].rstrip() + "…"
                        flag = " required" if pname in (required or []) else ""
                        param_bits.append(f"{pname}:{ptype}{flag}" + (f" — {desc}" if desc else ""))
                    prompt += "  Parameters: " + "; ".join(param_bits) + "\n"
                else:
                    prompt += "  Parameters: none\n"
                if required:
                    prompt += f"  Required: {', '.join(required)}\n"
                prompt += "\n"

        prompt += (
            f"\nProduce a JSON object matching this schema:\n{schema_json}\n\n"
            "Rules:\n"
            "- 'id' is a unique string per step (e.g. 'verify_item', 'search_results').\n"
            "- 'tool_name' must be a real available tool listed above.\n"
            "- 'arguments' is a dict of key-value pairs matching the listed parameter names/types of the selected tool.\n"
            "- 'depends_on' lists step IDs that must run first.\n"
            "- 'success_condition' describes what makes this step successful.\n"
            "- Include 'user_goal' summarising the user's intent.\n"
            "- Include 'constraints' listing quality, language, or other limits.\n"
            "- CRITICAL LANGUAGE RULE: If a matched category context packet or tracked media preference provides an item language (e.g. 'Italian'), use that language for search/download arguments unless the user explicitly overrides it. Also inspect existing-unit audio_languages in context; do not silently queue a different-language release when the library is already in the preferred/existing language.\n"
            "- TOOL PHILOSOPHY RULE: Prefer a small chain of generic tools. Category state and rules arrive through context/enquire_about_media/metadata_lookup; category-specific micro-tools must not be invented or called for ordinary download decisions.\n"
            "- DEPENDENCY OUTPUT RULE: Do not write prose placeholders like '<URL from the first search result>'. When one step needs data from a previous step, use ${step_id.path.to.field}; for example ${search_event.results.0.url} or ${lookup_show.latest_season}.\n"
            "- Do not call read_web_page/browser_open/browser_extract with a URL unless the argument is either a literal http(s) URL or a ${step_id.results.0.url}-style placeholder.\n"
            "JSON:"
        )
        return prompt

    def _build_repair_prompt(self, previous_output: str,
                              error_message: str,
                              *,
                              original_prompt: str = "") -> str:
        """Build a repair prompt asking the LLM to fix invalid JSON.

        Args:
            previous_output: The previous (invalid) JSON output.
            error_message: The validation error message.

        Returns:
            A prompt for the repair attempt.
        """
        schema_json = json.dumps(AgentPlan.model_json_schema(), indent=2)
        return (
            "The previous planner output was invalid or unsafe. Repair it for the SAME current user request.\n"
            "Do not substitute an example task. Do not invent unavailable tools.\n\n"
            f"Original planning prompt, including current request and available tools:\n{original_prompt}\n\n"
            f"Error: {error_message}\n"
            f"Previous output:\n{previous_output}\n\n"
            f"Produce a corrected JSON object matching this schema:\n{schema_json}\n"
            "Return ONLY valid JSON — no other text.\n"
            "Corrected JSON:"
        )

    async def _call_llm(self, prompt: str, intent: Intent) -> str:
        """Make an LLM call and return the raw response text.

        Args:
            prompt: The prompt to send to the LLM.
            intent: The current intent for task routing.

        Returns:
            The raw response text from the LLM.
        """
        task = "research_web" if intent == Intent.SEARCH else "planning_strict"
        if self._llm_client:
            response = await self._breaker.call(
                self._llm_client.completion,
                task=task,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=500,
                temperature=0.0,
            )
        else:
            import litellm
            response = await self._breaker.call(
                litellm.acompletion,
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                api_base=self._api_base,
                api_key=self._api_key,
                max_tokens=500,
                temperature=0.0,
            )
        return LLMResponseParser.safe_extract_content(response)

    @staticmethod
    def _extract_json(raw_text: str) -> dict:
        """Extract a JSON object from raw LLM output.

        Handles markdown code fences and leading/trailing text.

        Args:
            raw_text: Raw LLM response that may contain JSON.

        Returns:
            Parsed JSON dict.

        Raises:
            json.JSONDecodeError: If no valid JSON object is found.
        """
        return LLMResponseParser.extract_json_resilient(raw_text)

    async def reflect(self, user_prompt: str, tool_results: list[str],
                       task: str = "research") -> Optional[str]:
        """Evaluate whether the tool results are sufficient or more is needed.

        Args:
            user_prompt: The original user request.
            tool_results: List of compact tool result summaries.
            task: The LLM task to use for reflection (default: "research").

        Returns:
            A reflection string ("SUFFICIENT" or "NEED MORE: ...")
            or None if reflection fails.
        """
        results_text = "\n".join(f"- {r}" for r in tool_results[-3:])
        prompt = (
            "Given the user's request and the tool results so far, "
            "should the agent stop and respond, or does it need to search "
            "again with different terms?\n\n"
            f"Request: {user_prompt}\n\n"
            f"Recent results:\n{results_text}\n\n"
            "Respond with ONLY 'SUFFICIENT' or 'NEED MORE: [reason]'"
        )

        try:
            if self._llm_client:
                response = await self._breaker.call(
                    self._llm_client.completion,
                    task=task,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=50,
                    temperature=0.2,
                )
            else:
                import litellm
                response = await self._breaker.call(
                    litellm.acompletion,
                    model=self._model,
                    messages=[{"role": "user", "content": prompt}],
                    api_base=self._api_base,
                    api_key=self._api_key,
                    max_tokens=50,
                    temperature=0.2,
                )
            reflection = LLMResponseParser.safe_extract_content(response)
            logger.info(f"Reflection: {reflection}")
            return reflection
        except Exception as e:
            logger.warning(f"Reflection failed: {e}")
            return None