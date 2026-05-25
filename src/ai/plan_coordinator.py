"""
Plan coordinator for LJS.

Prepares and coordinates structured plan generation for complex
intents (SEARCH, DOWNLOAD). Generates AgentPlan, injects goal
and constraints into the system prompt, and creates PlanExecutor
for deterministic step execution.
"""

from __future__ import annotations

import re

from typing import Any, Optional, TYPE_CHECKING

from src.ai.plan_executor import PlanExecutor
from src.ai.reasoning import ReasoningPlanner
from src.ai.tool_executor import ToolCallExecutor
from src.core.models import Intent, AgentPlan, PlanStep
from src.utils.item_matcher import ItemMatcher
from src.ai.tools.metadata_lookup_support import MetadataLookupRequest

if TYPE_CHECKING:
    from src.core.models import Settings


class PlanCoordinator:
    """Prepares and coordinates plan generation for complex intents.

    Generates structured AgentPlan for SEARCH and DOWNLOAD intents,
    injects goal and constraints into the system prompt, and creates
    PlanExecutor for deterministic step execution. Both run() and
    run_stream() use this coordinator to eliminate duplicate code.
    """

    def __init__(self, tool_executor: ToolCallExecutor, llm_client: Any, settings: Optional[Settings] = None) -> None:
        """Initialize the plan coordinator.

        Args:
            tool_executor: Shared ToolCallExecutor for plan step execution.
            llm_client: The LLM client for the ReasoningPlanner.
            settings: Optional Settings instance.
        """
        self._tool_executor = tool_executor
        self._llm_client = llm_client
        self._settings = settings

    def update_settings(self, settings: Settings) -> None:
        """Update the coordinator's settings (supports hot-reloading)."""
        self._settings = settings

    def create_planner(self) -> ReasoningPlanner:
        """Create a ReasoningPlanner using the injected LLM client.

        Public factory used by the assistant for reflection during
        the agentic loop, in addition to plan generation.

        Returns:
            A configured ReasoningPlanner instance.
        """
        return ReasoningPlanner(llm_client=self._llm_client)



    def _looks_like_media_fact_question(self, user_prompt: str) -> bool:
        """Deprecated compatibility seam.

        User-language fact detection belongs to the planner LLM, not keyword
        heuristics. The method now returns False so SEARCH plans are not
        rewritten from English phrase guesses.
        """
        return False

    def _metadata_media_type_from_context(self, context: str | None) -> str:
        """Infer a metadata lookup type from category context without hardcoding categories."""
        text = context or ""
        match = re.search(r'"category_id"\s*:\s*"([^"\n]+)"', text, re.IGNORECASE)
        if match:
            return match.group(1).strip() or "auto"
        match = re.search(r"active category:\s*([a-z0-9_-]+)", text, re.IGNORECASE)
        if match:
            return match.group(1).strip() or "auto"
        return "auto"

    def _recent_history_context(self, context: str | None) -> str:
        """Return only recent chat history from the planner context.

        The full planning context may contain large library packets with many
        item names; using all of it for follow-up resolution can select the
        wrong show.  The RECENT CONVERSATION HISTORY suffix is a safer source
        for resolving pronouns like "the fifth season".
        """
        if not context:
            return ""
        marker = "RECENT CONVERSATION HISTORY:"
        idx = context.rfind(marker)
        if idx < 0:
            return ""
        return context[idx + len(marker):]

    def _recently_mentioned_tracked_item(self, text: str) -> Any | None:
        """Return the tracked item most recently mentioned in text."""
        if not self._settings or not text:
            return None
        lower = text.casefold()
        best_item = None
        best_pos = -1
        for item in self._settings.tracked_items:
            key = str(getattr(item, "key", "") or "")
            if not key:
                continue
            pos = lower.rfind(key.casefold())
            if pos > best_pos:
                best_pos = pos
                best_item = item
        return best_item if best_pos >= 0 else None

    def _media_coordinates_from_text(self, text: str) -> dict[str, int]:
        """Extract explicit SxxEyy/season/episode coordinates from one text block."""
        coords: dict[str, int] = {}
        blob = text or ""
        season_episode_matches = list(re.finditer(r"\bS0*(\d{1,2})\s*E0*(\d{1,3})\b", blob, re.IGNORECASE))
        if season_episode_matches:
            match = season_episode_matches[-1]
            coords["season"] = int(match.group(1))
            coords["episode"] = int(match.group(2))
            return coords
        season = MetadataLookupRequest.infer_season_number(blob)
        episode = MetadataLookupRequest.infer_episode_number(blob)
        if season is not None:
            coords["season"] = season
        if episode is not None:
            coords["episode"] = episode
        return coords

    def _recent_media_coordinates(self, user_prompt: str, context: str | None) -> dict[str, int]:
        """Extract safe follow-up media coordinates for metadata lookup.

        Episode numbers are copied only when the *current* user message names a
        specific episode.  Broad questions like "how many episodes still need
        to air?" may reuse a recent season, but must not inherit S05E07 from
        the previous download receipt and accidentally narrow the lookup.
        """
        current = self._media_coordinates_from_text(user_prompt or "")
        if current.get("episode") is not None:
            if current.get("season") is None:
                recent = self._media_coordinates_from_text(self._recent_history_context(context))
                if recent.get("season") is not None:
                    current["season"] = recent["season"]
            return current

        coords: dict[str, int] = {}
        if current.get("season") is not None:
            coords["season"] = current["season"]
            return coords

        recent = self._media_coordinates_from_text(self._recent_history_context(context))
        if recent.get("season") is not None:
            coords["season"] = recent["season"]
        return coords

    @staticmethod
    def _metadata_query_has_title(query: str, item: Any | None) -> bool:
        """Return whether a metadata query already names the matched item."""
        key = str(getattr(item, "key", "") or "") if item is not None else ""
        if not key or not query:
            return False
        return key.casefold() in query.casefold() or ItemMatcher.fuzzy_match_names(key, query)

    def _normalize_search_plan(
        self,
        agent_plan: AgentPlan,
        user_prompt: str,
        allowed_tool_names: set[str],
        context: str | None,
    ) -> AgentPlan:
        """Prefer generic metadata services before web for media fact plans.

        This is not a bespoke answer workflow. It is a small safety net for the
        common failure mode where planning jumps directly to web_search for a
        media fact that TMDB/TVMaze/IMDb-style metadata is designed to answer.
        """
        if agent_plan.intent != Intent.SEARCH:
            return agent_plan
        if "metadata_lookup" not in allowed_tool_names:
            return agent_plan
        if not agent_plan.steps:
            return agent_plan
        if any(step.tool_name == "metadata_lookup" for step in agent_plan.steps):
            return agent_plan
        first_tool = agent_plan.steps[0].tool_name
        if first_tool not in {"web_search", "read_web_page", "browser_extract"}:
            return agent_plan
        media_type = self._metadata_media_type_from_context(context)
        query = user_prompt
        matched_item = None
        for item in (self._settings.tracked_items if self._settings else []):
            if ItemMatcher.is_item_mentioned(
                tracked_key=item.key,
                prompt=user_prompt,
                goal=agent_plan.user_goal or "",
                steps=agent_plan.steps,
            ):
                matched_item = item
                break
        if matched_item is None:
            matched_item = self._recently_mentioned_tracked_item(self._recent_history_context(context))
        if matched_item is None:
            return agent_plan
        query = matched_item.key
        for step in agent_plan.steps:
            if step.tool_name not in {"web_search", "read_web_page", "browser_extract"} or not isinstance(step.arguments, dict):
                continue
            original_query = str(step.arguments.get("query") or step.arguments.get("url") or "").strip()
            if original_query and not self._metadata_query_has_title(original_query, matched_item):
                step.arguments["query"] = f"{matched_item.key} {original_query}"
        agent_plan.steps = [
            PlanStep(
                id="lookup_metadata",
                tool_name="metadata_lookup",
                arguments={
                    "query": query,
                    "media_type": media_type,
                    "service": "auto",
                    "question": user_prompt,
                },
                depends_on=[],
                success_condition="Structured metadata is returned that can answer the user's factual media question.",
            ),
            *agent_plan.steps,
        ]
        agent_plan.constraints["source_priority"] = "metadata_lookup_before_web_search"
        agent_plan.constraints["web_queries_include_recent_title"] = matched_item.key
        return agent_plan

    def _infer_requested_season(self, user_prompt: str, agent_plan: AgentPlan, item: Any | None = None) -> int | None:
        """Return an explicitly structured season coordinate from a plan.

        Natural-language season/latest/current interpretation belongs to the
        LLM/category context packet. This fallback only preserves already
        structured numeric arguments when a plan has to be normalized away from
        unsafe direct category tools.
        """
        for step in agent_plan.steps:
            season_arg = (step.arguments or {}).get("season")
            if isinstance(season_arg, int):
                return season_arg
            if isinstance(season_arg, str) and season_arg.isdigit():
                return int(season_arg)
        return None

    def _step_has_placeholder(self, step: PlanStep) -> bool:
        """Detect unbound pseudo variables like <item_id_from_get_lib>."""
        for value in (step.arguments or {}).values():
            if isinstance(value, str) and value.strip().startswith("<") and value.strip().endswith(">"):
                return True
        return False

    @staticmethod
    def _looks_like_direct_category_download_tool(tool_name: str | None) -> bool:
        """Return True for category-owned write/download workflow tools.

        Weaker planning models sometimes skip the generic discovery flow and
        emit category workflow calls such as ``<category>.download_*`` directly,
        often with unresolved step-output placeholders.  Those workflows are
        concrete execution actions, not safe candidate discovery.  The planner
        normalizer should route fresh download requests through
        ``search_media_torrents`` first so candidate IDs and batch
        recommendations are produced deterministically.

        This guard is intentionally category-neutral: it does not know about TV,
        movies, books, or games.  It only classifies dotted category-scoped tool
        names by operation verbs that imply queueing/downloading/importing
        payloads.  Generic safe tools and category read workflows are left
        alone.
        """
        if not tool_name:
            return False
        name = str(tool_name).strip()
        if "." not in name:
            return False
        operation = name.rsplit(".", 1)[-1].casefold()
        unsafe_verbs = (
            "download",
            "queue",
            "grab",
            "fetch",
            "import",
            "add",
        )
        return any(verb in operation for verb in unsafe_verbs)


    @staticmethod
    def _step_has_dependency_placeholder(step: PlanStep) -> bool:
        """Return True when a step still references another plan step output.

        Fresh download plans should stop at candidate discovery.  If a local
        planner adds an immediate queue step with placeholders like
        ``${search_season.candidate_ids}``, keep the search/read-only work and
        let the generic post-search queue guard or streaming tool loop queue
        from the real search payload.
        """
        for value in (step.arguments or {}).values():
            if isinstance(value, str) and re.fullmatch(r"\$?\{[A-Za-z0-9_-]+\.[^}]+\}", value.strip()):
                return True
            if isinstance(value, (list, tuple)):
                for item in value:
                    if isinstance(item, str) and re.fullmatch(r"\$?\{[A-Za-z0-9_-]+\.[^}]+\}", item.strip()):
                        return True
        return False

    @staticmethod
    def _looks_like_multi_unit_download_request(user_prompt: str, agent_plan: AgentPlan) -> bool:
        """Return True when a download request targets a set of units.

        The planner may collapse phrases such as "missing episodes", "latest
        season", or "all remaining units" into a single guessed episode.  That
        is unsafe: category hooks own missing-unit expansion and pack safety.
        This detector is intentionally generic and only looks for multi-unit
        language, not category-specific workflows.
        """
        text = f"{user_prompt or ''} {agent_plan.user_goal or ''}".casefold()
        unit_words = (
            "episodes", "episodi", "unit", "units", "capitoli", "chapters",
            "tracks", "discs", "volumes", "season", "stagione",
        )
        scope_words = (
            "missing", "mancanti", "remaining", "rimanenti", "latest",
            "current", "all", "every", "rest", "complete", "whole",
        )
        return any(word in text for word in unit_words) and any(word in text for word in scope_words)

    @staticmethod
    def _requested_pack_scope(user_prompt: str, agent_plan: AgentPlan) -> str | None:
        """Infer a category-neutral pack search scope from planner/user wording.

        This does not decide TV behavior.  It only carries the user's search
        phase preference to the owning category, which may interpret packs,
        bundles, volumes, editions, archives, or platform builds appropriately.
        """
        text = f"{user_prompt or ''} {agent_plan.user_goal or ''} {agent_plan.constraints}".casefold()
        pack_words = ("pack", "bundle", "complete", "full", "whole", "intera", "completa", "pacchetto")
        unit_scope = ("season", "stagione", "series", "saga", "volume", "collection", "album")
        explicit_pack_request = any(word in text for word in pack_words) and any(word in text for word in unit_scope)

        # A request for a whole/latest/last season is not a request for one
        # episode.  Treat it as pack-preferred even when the user did not say
        # "pack" explicitly, but do not apply that to missing/remaining-unit
        # requests where the category should search only the absent units.
        whole_scope_phrases = (
            "latest season", "last season", "current season", "new season",
            "ultima stagione", "stagione più recente", "stagione piu recente",
            "stagione corrente", "nuova stagione",
        )
        missing_words = ("missing", "mancanti", "remaining", "rimanenti", "need", "needed", "manca", "mancano")
        implicit_whole_season_request = any(phrase in text for phrase in whole_scope_phrases) and not any(
            word in text for word in missing_words
        )
        if not explicit_pack_request and not implicit_whole_season_request:
            return None

        # Strict pack-only intent must come from the user's wording/goal, not
        # from internal constraint strings such as "download_plan_contract=...only...".
        strict_text = f"{user_prompt or ''} {agent_plan.user_goal or ''}".casefold()
        strict_patterns = (
            r"\bpack[-\s]?only\b",
            r"\bonly\s+(?:a\s+)?(?:season\s+)?pack\b",
            r"\bsolo\s+(?:il\s+)?pacchetto\b",
            r"\bsoltanto\s+(?:il\s+)?pacchetto\b",
            r"\bexclusively\s+(?:a\s+)?(?:season\s+)?pack\b",
            r"\bnot\s+single\s+episodes\b",
        )
        if explicit_pack_request and any(re.search(pattern, strict_text) for pattern in strict_patterns):
            return "season_pack_only"
        return "season_pack_preferred"

    @staticmethod
    def _recent_media_name_from_context(context: str | None) -> str | None:
        """Extract the most recent media name from tool/chat context.

        Tracked settings are not enough for untracked items discovered in the
        current chat.  Prefer explicit search tool summaries because they carry
        the category-owned item name used by the previous turn.
        """
        text = context or ""
        if not text:
            return None
        patterns = (
            r'TOOL \(search_media_torrents\) query:\s*([^\n(]+)',
            r'"display_name"\s*:\s*"([^"]+)"',
            r'"name"\s*:\s*"([^"]+)"',
            r'for \*\*([^*`\n]+?)\s+Season\s+\d+\*\*',
            r'of \*\*([^*`\n]+?)\s+Season\s+\d+\*\*',
        )
        for pattern in patterns:
            matches = list(re.finditer(pattern, text, re.IGNORECASE))
            if not matches:
                continue
            value = matches[-1].group(1).strip()
            value = re.sub(r'\s+(?:Season|S)\s*\d+.*$', '', value, flags=re.IGNORECASE).strip()
            value = re.sub(r'\s+S\d{1,2}.*$', '', value, flags=re.IGNORECASE).strip()
            if value and not value.isdigit() and len(value) > 1:
                return value
        return None

    def _normalize_multi_unit_search_steps(self, agent_plan: AgentPlan, user_prompt: str) -> AgentPlan:
        """Remove single-unit guesses from generic media searches.

        For multi/missing requests, a structured ``episode=10`` (or equivalent
        unit coordinate) produced by the planner is treated as a guess unless
        the user explicitly asked for that one unit.  Keeping the broader season
        or item coordinate lets ``search_media_torrents`` delegate fan-out and
        safe bundle discovery to the category implementation.
        """
        if not self._looks_like_multi_unit_download_request(user_prompt, agent_plan):
            return agent_plan
        changed = False
        unit_keys = ("episode", "chapter", "track", "disc", "volume", "unit", "unit_number")
        for step in agent_plan.steps:
            if step.tool_name != "search_media_torrents" or not isinstance(step.arguments, dict):
                continue
            for key in unit_keys:
                if key in step.arguments and step.arguments.get(key) is not None:
                    step.arguments.pop(key, None)
                    changed = True
        if changed:
            agent_plan.constraints["multi_unit_scope"] = "category_owned_fanout_without_single_unit_guess"
        return agent_plan

    def _remove_premature_queue_steps(self, agent_plan: AgentPlan) -> AgentPlan:
        """Strip unresolved queue steps from fresh search-first plans.

        This preserves the architecture rule from the prompt: discovery is one
        phase, queueing happens only after concrete candidate IDs are returned.
        It also prevents a failed placeholder from aborting the whole request
        before the batch auto-queue guard can run.
        """
        if not any(step.tool_name == "search_media_torrents" for step in agent_plan.steps):
            return agent_plan
        filtered: list[PlanStep] = []
        removed = False
        for step in agent_plan.steps:
            if step.tool_name == "queue_download" and (step.depends_on or self._step_has_dependency_placeholder(step)):
                removed = True
                continue
            filtered.append(step)
        if removed and filtered:
            agent_plan.steps = filtered
        return agent_plan

    def _apply_download_search_scope(self, agent_plan: AgentPlan, user_prompt: str) -> AgentPlan:
        """Attach a category-neutral staged search scope to torrent searches."""
        scope = self._requested_pack_scope(user_prompt, agent_plan)
        if not scope:
            return agent_plan
        for step in agent_plan.steps:
            if step.tool_name == "search_media_torrents" and isinstance(step.arguments, dict):
                step.arguments.setdefault("search_scope", scope)
        agent_plan.constraints["download_search_scope"] = scope
        return agent_plan


    @staticmethod
    def _is_dependency_placeholder(value: Any) -> bool:
        """Return True for model-invented references to prior step outputs.

        Fresh download discovery must not depend on arbitrary JSON paths that a
        planner guessed from a tool result.  Candidate discovery is an app-owned
        contract: a category receives the item plus concrete coordinates, then
        resolves metadata such as "latest season" internally.
        """
        return isinstance(value, str) and bool(re.fullmatch(r"\$?\{[A-Za-z0-9_-]+\.[^}]+\}", value.strip()))

    @classmethod
    def _safe_scalar_arg(cls, value: Any) -> Any | None:
        """Return literal JSON scalars while dropping unresolved placeholders."""
        if cls._is_dependency_placeholder(value):
            return None
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return None

    @staticmethod
    def _safe_positive_int_arg(value: Any) -> int | None:
        """Coerce literal numeric plan arguments; reject placeholders/prose."""
        if isinstance(value, bool):
            return None
        if isinstance(value, int) and value > 0:
            return value
        if isinstance(value, str) and value.strip().isdigit():
            parsed = int(value.strip())
            return parsed if parsed > 0 else None
        return None

    def _download_search_seed_args(self, agent_plan: AgentPlan, user_prompt: str) -> dict[str, Any] | None:
        """Build one safe search_media_torrents argument set for DOWNLOAD.

        This is the root contract repair for planner/tool integration.  LLMs may
        propose metadata -> search -> storage chains, but a fresh download
        discovery turn should be a single category-owned search using literal
        facts only.  Category hooks then resolve omitted/latest season, pack
        schemas, fallback episodes, size ranking, and bundle semantics.
        """
        seed: dict[str, Any] = {}

        # Prefer an existing generic search step; it already carries the item
        # name most reliably for untracked items such as a newly requested show.
        for step in agent_plan.steps:
            if step.tool_name != "search_media_torrents" or not isinstance(step.arguments, dict):
                continue
            name = self._safe_scalar_arg(step.arguments.get("name"))
            if isinstance(name, str) and name.strip():
                seed["name"] = name.strip()
            language = self._safe_scalar_arg(step.arguments.get("language"))
            if isinstance(language, str) and language.strip():
                seed["language"] = language.strip()
            season = self._safe_positive_int_arg(step.arguments.get("season"))
            if season is not None:
                seed["season"] = season
            episode = self._safe_positive_int_arg(step.arguments.get("episode"))
            if episode is not None:
                seed["episode"] = episode
            scope = self._safe_scalar_arg(step.arguments.get("search_scope"))
            if isinstance(scope, str) and scope.strip():
                seed["search_scope"] = scope.strip()
            break

        # Tracked-library binding can improve the canonical item name/language,
        # but it must not be required for untracked items.
        if self._settings and getattr(self._settings, "tracked_items", None):
            for item in self._settings.tracked_items:
                if not ItemMatcher.is_item_mentioned(
                    tracked_key=item.key,
                    prompt=user_prompt,
                    goal=agent_plan.user_goal or "",
                    steps=agent_plan.steps,
                ):
                    continue
                seed["name"] = item.key
                if not seed.get("language") and getattr(item, "language", None):
                    seed["language"] = getattr(item, "language")
                    agent_plan.constraints["language"] = getattr(item, "language")
                break

        # If the planner skipped a search step, try to recover a literal title
        # from metadata/enquiry steps.  Never use digits like "23" as titles.
        if not seed.get("name"):
            for step in agent_plan.steps:
                if step.tool_name not in {"metadata_lookup", "enquire_about_media"} or not isinstance(step.arguments, dict):
                    continue
                for key in ("name", "item_name", "query", "title"):
                    value = self._safe_scalar_arg(step.arguments.get(key))
                    if isinstance(value, str) and value.strip() and not value.strip().isdigit():
                        seed["name"] = value.strip()
                        break
                if seed.get("name"):
                    break

        if not seed.get("name"):
            return None

        scope = self._requested_pack_scope(user_prompt, agent_plan)
        if scope:
            seed["search_scope"] = scope
            agent_plan.constraints["download_search_scope"] = scope
        elif seed.get("search_scope") is None:
            seed["search_scope"] = "default"

        # Literal user/plan season coordinates are safe.  "latest" is not a
        # literal coordinate; omit season and let the owning category resolve it.
        if "season" not in seed:
            season = self._infer_requested_season(user_prompt, agent_plan)
            if season is not None:
                seed["season"] = season

        return {k: v for k, v in seed.items() if v not in (None, "", [], {})}

    def _canonicalize_download_discovery_plan(self, agent_plan: AgentPlan, user_prompt: str) -> AgentPlan:
        """Reduce fresh DOWNLOAD discovery to one category-owned search step.

        The previous architecture allowed the LLM planner to stitch together
        metadata lookups, storage preflights, and search calls using guessed JSON
        paths.  Those paths became an endless source of runtime crashes.  This
        canonicalization makes DOWNLOAD discovery deterministic and category-
        extensible: the app supplies concrete literals, categories own schemas,
        and queueing happens later from real candidate IDs.
        """
        if agent_plan.intent != Intent.DOWNLOAD:
            return agent_plan
        if agent_plan.steps and all(step.tool_name == "queue_download" for step in agent_plan.steps):
            return agent_plan
        if not any(step.tool_name == "search_media_torrents" for step in agent_plan.steps):
            return agent_plan
        args = self._download_search_seed_args(agent_plan, user_prompt)
        if not args:
            return agent_plan
        agent_plan.steps = [
            PlanStep(
                id="search_candidates",
                tool_name="search_media_torrents",
                arguments=args,
                depends_on=[],
                success_condition="Category-owned torrent candidates are returned for the requested media item and constraints.",
            )
        ]
        agent_plan.constraints["download_plan_contract"] = "canonical_search_only_no_planner_placeholders"
        return agent_plan

    def _normalize_download_plan(self, agent_plan: AgentPlan, user_prompt: str, allowed_tool_names: set[str] | None = None) -> AgentPlan:
        """Fail-safe DOWNLOAD planning rules.

        Fresh download requests must start with search_media_torrents. Category
        workflow tools that directly download/queue payloads are execution
        actions, not safe candidate discovery steps, and placeholder item IDs
        cannot be resolved by the deterministic executor. Rewrite those plans
        before execution.
        """
        if agent_plan.intent != Intent.DOWNLOAD:
            return agent_plan

        # Keep explicit queue-by-selection plans intact.
        if agent_plan.steps and all(step.tool_name == "queue_download" for step in agent_plan.steps):
            return agent_plan

        has_search = any(step.tool_name == "search_media_torrents" for step in agent_plan.steps)
        has_unsafe = any(self._looks_like_direct_category_download_tool(step.tool_name) or self._step_has_placeholder(step) for step in agent_plan.steps)
        has_unknown = bool(allowed_tool_names) and any(step.tool_name not in allowed_tool_names for step in agent_plan.steps)
        # Plans generated by weaker models sometimes include invented immediate
        # workflow download tools instead of candidate search. Do not let those
        # reach the executor.
        if has_search and not has_unknown:
            # Root contract rule: fresh download discovery is a single
            # category-owned search call.  Do not execute model-authored
            # metadata/search dependency chains with placeholder paths.
            agent_plan = self._canonicalize_download_discovery_plan(agent_plan, user_prompt)
            agent_plan = self._normalize_multi_unit_search_steps(agent_plan, user_prompt)
            agent_plan = self._apply_download_search_scope(agent_plan, user_prompt)
            return self._remove_premature_queue_steps(agent_plan)

        if not self._settings:
            return agent_plan

        for item in self._settings.tracked_items:
            if not ItemMatcher.is_item_mentioned(
                tracked_key=item.key,
                prompt=user_prompt,
                goal=agent_plan.user_goal or "",
                steps=agent_plan.steps,
            ):
                continue
            args: dict[str, Any] = {"name": item.key}
            lang = getattr(item, "language", None)
            if lang:
                args["language"] = lang
                agent_plan.constraints["language"] = lang
            season = self._infer_requested_season(user_prompt, agent_plan, item=item)
            if season is not None:
                args["season"] = season
            scope = self._requested_pack_scope(user_prompt, agent_plan)
            if scope:
                args["search_scope"] = scope
                agent_plan.constraints["download_search_scope"] = scope
            agent_plan.steps = [
                PlanStep(
                    id="search_candidates",
                    tool_name="search_media_torrents",
                    arguments=args,
                    depends_on=[],
                    success_condition="Torrent candidates are returned for the requested media item and constraints.",
                )
            ]
            return agent_plan
        return agent_plan

    @staticmethod
    def _needs_future_airdate_crosscheck(user_prompt: str, agent_plan: AgentPlan) -> bool:
        """Return whether a SEARCH plan needs official/date cross-check evidence.

        Provider metadata can be regional or UTC-normalized; Apple-style release
        pages may list a different local calendar date.  Any explicit episode
        air/release-date question with future wording (for example "when will
        episode 10 air?") should therefore add a lightweight official-source
        cross-check when web search is available.
        """
        text = f"{user_prompt} {agent_plan.user_goal}".casefold()
        date_terms = ("air", "aired", "release", "released", "premiere", "premiered", "onda", "uscit")
        future_terms = ("will", "scheduled", "schedule", "still", "future", "upcoming", "not yet", "ancora", "deve", "previst")
        episode_terms = ("episode", "episodes", "episodio", "puntata", "s0", "ep ")
        return (
            any(term in text for term in date_terms)
            and any(term in text for term in episode_terms)
            and any(term in text for term in future_terms)
        )

    @staticmethod
    def _official_airdate_query(title: str, user_prompt: str) -> str:
        """Build a title-bound official-source search query for date validation."""
        episode = MetadataLookupRequest.infer_episode_number(user_prompt)
        ep_part = f" episode {episode}" if episode is not None else " episodes"
        return f"{title}{ep_part} air date official Apple TV press".strip()

    async def prepare_plan(
        self,
        user_prompt: str,
        intent: Intent,
        system_prompt_content: str,
        allowed_tool_names: set[str],
        context: str | None = None,
    ) -> tuple[AgentPlan | None, PlanExecutor | None, str]:
        """Generate a structured plan and prepare for execution.

        Only generates plans for SEARCH and DOWNLOAD intents.
        Injects the plan's user_goal and constraints into the system
        prompt and creates a PlanExecutor when the plan has steps.

        Args:
            user_prompt: The user's original request.
            intent: The routed intent.
            system_prompt_content: The current system prompt text.
            allowed_tool_names: Tool names allowed for this intent.
            context: Optional preference/behavior context for planning.

        Returns:
            Tuple of (agent_plan, plan_executor, updated_system_prompt_content).
            agent_plan may be None if the intent does not support planning.
        """
        if intent not in (Intent.SEARCH, Intent.DOWNLOAD):
            return None, None, system_prompt_content

        tool_schemas = self._tool_executor.get_definitions(allowed_tool_names)
        planner = self.create_planner()
        agent_plan = await planner.generate_plan(
            user_prompt, intent, context=context, tool_schemas=tool_schemas
        )

        if not agent_plan:
            return None, None, system_prompt_content

        agent_plan = self._normalize_download_plan(agent_plan, user_prompt, allowed_tool_names)
        agent_plan = self._normalize_search_plan(
            agent_plan,
            user_prompt=user_prompt,
            allowed_tool_names=allowed_tool_names,
            context=context,
        )

        # Post-process constraints and arguments to enforce tracked category item language and key matching
        if self._settings and self._settings.tracked_items:
            import re
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
                    from loguru import logger
                    logger.info(
                        f"[Tracked Item Binding] Tracked item '{item.key}' detected in plan. "
                        "Binding exact item key and filling configured language only when the plan omitted language."
                    )
                    language_relevant_tools = {"search_torrents", "search_media_torrents", "queue_download", "queue_media_download"}
                    plan_has_language = bool(agent_plan.constraints.get("language"))
                    for step in agent_plan.steps:
                        if (
                            isinstance(step.arguments, dict)
                            and step.tool_name in language_relevant_tools
                            and step.arguments.get("language")
                        ):
                            plan_has_language = True
                            break
                    if intent == Intent.DOWNLOAD and not plan_has_language:
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

        # Follow-up media fact questions often omit the title (for example:
        # "when did episode 10 air?") because the previous turn already named
        # the show. If the LLM has already selected metadata_lookup, repair only
        # that structured metadata call from recent tracked-item context. This is
        # not an intent heuristic and does not create category-specific tools.
        recent_item = self._recently_mentioned_tracked_item(self._recent_history_context(context))
        recent_name = getattr(recent_item, "key", None) if recent_item is not None else self._recent_media_name_from_context(context)
        recent_coords = self._recent_media_coordinates(user_prompt, context)
        metadata_step_ids: set[str] = set()
        if recent_name:
            media_type = self._metadata_media_type_from_context(context)
            for step in agent_plan.steps:
                if step.tool_name != "metadata_lookup" or not isinstance(step.arguments, dict):
                    continue
                metadata_step_ids.add(step.id)
                q = str(step.arguments.get("query") or "").strip()
                query_binds_to_recent = False
                if not q or q.isdigit() or self._is_dependency_placeholder(q):
                    step.arguments["query"] = recent_name
                    q = recent_name
                    query_binds_to_recent = True
                    agent_plan.constraints["metadata_title_bound_from_recent_context"] = recent_name
                elif recent_item is not None and self._metadata_query_has_title(q, recent_item):
                    query_binds_to_recent = True

                if not step.arguments.get("question"):
                    step.arguments["question"] = user_prompt
                if not query_binds_to_recent:
                    # A fresh, explicit metadata query such as "Quentin Tarantino"
                    # must not inherit the previous media title, season, or media
                    # type.  Earlier repair logic overwrote these SEARCH turns with
                    # stale context (for example Yellowstone/Leon), which made web
                    # research look broken even when the browser fallback worked.
                    continue

                if media_type and str(step.arguments.get("media_type") or "auto") == "auto":
                    step.arguments["media_type"] = media_type
                for coord_name, coord_value in recent_coords.items():
                    step.arguments.setdefault(coord_name, coord_value)
                if step.arguments.get("episode") is not None or step.arguments.get("season") is not None:
                    step.arguments["include_episodes"] = True
        if metadata_step_ids and self._needs_future_airdate_crosscheck(user_prompt, agent_plan) and "web_search" in allowed_tool_names:
            if not any(step.tool_name == "web_search" for step in agent_plan.steps):
                title = None
                if recent_name:
                    title = recent_name
                for step in agent_plan.steps:
                    if step.tool_name == "metadata_lookup" and isinstance(step.arguments, dict):
                        title = title or step.arguments.get("query")
                        break
                query = self._official_airdate_query(str(title or user_prompt), user_prompt)
                agent_plan.steps.append(
                    PlanStep(
                        id="official_airdate_crosscheck",
                        tool_name="web_search",
                        arguments={"query": query, "max_results": 5},
                        depends_on=[],
                        success_condition="Official or high-confidence source confirms upcoming episode air/release date.",
                    )
                )
                agent_plan.constraints["future_airdate_source_policy"] = "metadata_plus_official_web_crosscheck"

        if metadata_step_ids:
            kept_steps: list[PlanStep] = []
            removed_fallback = False
            for step in agent_plan.steps:
                if step.tool_name in {"web_search", "read_web_page", "browse_page", "browser_extract"} and set(step.depends_on or []) & metadata_step_ids:
                    removed_fallback = True
                    continue
                kept_steps.append(step)
            if removed_fallback and kept_steps:
                agent_plan.steps = kept_steps
                agent_plan.constraints["metadata_first_fallback_policy"] = "agent_loop_falls_back_only_if_metadata_is_insufficient"

        updated = system_prompt_content
        updated += f"\n\nGoal: {agent_plan.user_goal}"
        if agent_plan.constraints:
            constr_str = "; ".join(
                f"{k}={v}" for k, v in agent_plan.constraints.items()
            )
            updated += f"\nConstraints: {constr_str}"

        plan_exec = None
        if agent_plan.steps:
            plan_exec = PlanExecutor(
                tool_executor=self._tool_executor,
                allowed_tool_names=allowed_tool_names,
            )
            if any(step.tool_name in ("queue_download", "queue_media_download") for step in agent_plan.steps):
                updated += (
                    "\n\nCRITICAL CONTEXT: A structured download execution plan has been prepared. "
                    "Only confirm that a download is queued when the queue tool result explicitly has status=queued and a download_id. "
                    "If the queue tool returns an error or lacks a verified download_id, tell the user the queue action failed and include the error. "
                    "Use the user's language and list item/unit details only when verified."
                )

        return agent_plan, plan_exec, updated
