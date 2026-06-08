"""
Web and browser tools for LJS.

Declarative AgentTool implementations for reading web pages,
browser-based page access, web search, and media research via
BrowserToolProvider and WebResearcher.
"""

from __future__ import annotations

from typing import Any, Optional, TYPE_CHECKING

from loguru import logger

from src.ai.tools.base import AgentTool
from src.search.web.url_utils import normalize_search_result_url
from src.core.models import ToolExecutionContext
from src.core.models import Intent
from src.ai.runtime_date_grounding import RuntimeDateGrounding

if TYPE_CHECKING:
    from src.ai.web_reader import WebReader
    from src.core.config import SettingsManager
    from src.utils.browser.runtime import BrowserRuntime
    from src.utils.browser.browser_wrapper import Browser

_BROWSER_SESSION_CACHE: dict[str, object] = {}


def _browser_session_id(context: ToolExecutionContext) -> str:
    """Resolve a stable browser-session key for one agent run."""
    return context.session_id or context.user_id or "tool-session"


def _get_browser_session(browser_runtime: BrowserRuntime | None, context: ToolExecutionContext):
    """Return a cached browser session shared by semantic browser tools."""
    from src.ai.browser_tools import BrowserToolProvider

    session_id = _browser_session_id(context)
    key = f"{id(browser_runtime)}:{session_id}"
    if key not in _BROWSER_SESSION_CACHE:
        provider = BrowserToolProvider(browser_runtime)
        _BROWSER_SESSION_CACHE[key] = provider.new_session(session_id)
    return _BROWSER_SESSION_CACHE[key]


def _browser_provider(browser_runtime: BrowserRuntime | None):
    """Create a lightweight provider around the shared browser runtime."""
    from src.ai.browser_tools import BrowserToolProvider

    return BrowserToolProvider(browser_runtime)


class ReadWebPageTool:
    """Read a web page by URL and extract its text content."""

    name = "read_web_page"
    description = (
        "Read a web page by URL and extract its text content. "
        "Use this to follow links from search results and read "
        "articles, reviews, or other web content."
    )
    intents = {Intent.SEARCH, Intent.DOWNLOAD}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["web_reader"]

    def __init__(self, web_reader: Optional[WebReader] = None) -> None:
        self._web_reader = web_reader

    def parameters(self) -> dict:
        """Return the public tool parameter schema.

        The schema is consumed by the LLM runtime and should remain
        backward-compatible.  Add optional fields for extensions whenever
        possible, and keep validation rules mirrored in execute().
        """
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL of the web page to read.",
                },
            },
            "required": ["url"],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Read a web page and extract its text content."""
        url = normalize_search_result_url(arguments["url"])
        if not url:
            return {"error": f"read_web_page requires a resolved http(s) URL, got {arguments.get('url')!r}"}
        result = await self._web_reader.read_url(url)
        return result or {"error": f"Failed to read {url}"}


class BrowsePageTool:
    """Navigate to a web page using a real browser (Playwright)."""

    name = "browse_page"
    description = (
        "Navigate to a web page using a real browser (Playwright). "
        "Use this when read_web_page fails due to Cloudflare, JavaScript "
        "rendering, or dynamic content. Slower than read_web_page but "
        "handles pages that require JavaScript or challenge verification."
    )
    intents = {Intent.SEARCH, Intent.DOWNLOAD}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["browser"]

    def __init__(self, browser: Optional[Browser] = None, browser_runtime: Optional[BrowserRuntime] = None) -> None:
        self._browser = browser
        self._browser_runtime = browser_runtime

    def parameters(self) -> dict:
        """Return the public tool parameter schema.

        The schema is consumed by the LLM runtime and should remain
        backward-compatible.  Add optional fields for extensions whenever
        possible, and keep validation rules mirrored in execute().
        """
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to browse.",
                },
                "wait_seconds": {
                    "type": "number",
                    "description": "Seconds to wait for JavaScript rendering. Default: 3.0.",
                },
            },
            "required": ["url"],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Navigate to a web page using a real browser."""
        url = normalize_search_result_url(arguments["url"])
        if not url:
            return {"error": f"browse_page requires a resolved http(s) URL, got {arguments.get('url')!r}"}
        wait_seconds = arguments.get("wait_seconds", 3.0)
        if self._browser:
            result = await self._browser.fetch_page(url, wait_seconds=wait_seconds)
            if not result:
                return {"error": f"Failed to load {url}"}
            return {
                "title": result.get("title", ""),
                "content": result.get("content", ""),
                "url": result.get("url", url),
                "status": result.get("status", 0),
            }
        if self._browser_runtime:
            from src.core.models import BrowserFetchRequest

            result = await self._browser_runtime.fetch(
                BrowserFetchRequest(url=url, wait_seconds=float(wait_seconds or 3.0))
            )
            if not result.ok:
                return {
                    "error": result.error or result.blocked_reason or f"Failed to load {url}",
                    "url": result.final_url or url,
                    "status": result.status,
                    "blocked_reason": result.blocked_reason,
                }
            return {
                "title": result.title or "",
                "content": result.text or "",
                "url": result.final_url or url,
                "status": result.status,
            }
        return {"error": "browse_page is unavailable because no browser runtime is configured."}


class ManagedSearXNGToolStartup:
    """Lazy-start managed SearXNG for agent web tools.

    Startup after UI readiness covers normal launches, but users can run a web
    research turn before the sidecar has finished starting, or the process can
    exit between turns.  Web tools use this helper to make one conservative
    restart attempt before degrading to fallback search.
    """

    @staticmethod
    async def ensure_ready(settings_manager: Optional[SettingsManager], searxng_manager: Any) -> dict[str, Any]:
        if settings_manager is None or searxng_manager is None:
            return {"attempted": False, "reason": "missing_dependencies"}
        cfg = getattr(settings_manager.settings, "web_search", None)
        if cfg is None:
            return {"attempted": False, "reason": "missing_config"}
        if getattr(cfg, "provider", "") != "searxng" or getattr(cfg, "mode", "managed") != "managed":
            return {"attempted": False, "reason": "not_managed_searxng"}
        if getattr(searxng_manager, "is_running", False):
            return {"attempted": False, "reason": "already_running"}
        logger.warning("ManagedSearXNGToolStartup: managed SearXNG is not running; attempting lazy start before web research.")
        ok = await searxng_manager.start(settings_manager.settings, health_timeout_seconds=30.0)
        searxng_manager.save_to_settings(settings_manager.settings)
        settings_manager.save(settings_manager.settings)
        return {
            "attempted": True,
            "ok": bool(ok),
            "url": getattr(searxng_manager, "url", ""),
            "error": None if ok else getattr(searxng_manager, "last_error", None),
        }


class WebSearchTool:
    """Search the web using the configured provider service."""

    name = "web_search"
    description = (
        "Search the web for current information using the configured provider "
        "(Brave, Tavily, Kagi, SearXNG, or an explicit DuckDuckGo fallback). "
        "Returns titles, URLs, and snippets for research, reviews, news, and fact-checking."
    )
    intents = {Intent.SEARCH, Intent.DOWNLOAD}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["browser_runtime"]

    def __init__(self, browser_runtime: Optional[BrowserRuntime] = None, settings_manager: Optional[SettingsManager] = None, searxng_manager: Any = None) -> None:
        self._browser_runtime = browser_runtime
        self._settings_manager = settings_manager
        self._searxng_manager = searxng_manager

    def parameters(self) -> dict:
        """Return the public tool parameter schema.

        The schema is consumed by the LLM runtime and should remain
        backward-compatible.  Add optional fields for extensions whenever
        possible, and keep validation rules mirrored in execute().
        """
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum results to return. Default: 5.",
                },
                "categories": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional provider categories such as general or news. Use news+general for current reporting.",
                },
                "language": {
                    "type": "string",
                    "description": "Optional search language such as it-IT, en-US, all, or auto.",
                },
                "time_range": {
                    "type": "string",
                    "enum": ["", "day", "month", "year"],
                    "description": "Optional freshness filter when supported. Use month/year for current public news or rumors.",
                },
            },
            "required": ["query"],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Search the web using the configured provider service."""
        query = arguments["query"]
        max_results = arguments.get("max_results", 5)
        from src.core.models import WebSearchConfig
        from src.search.web.service import WebSearchService

        base_config = self._settings_manager.settings.web_search if self._settings_manager else WebSearchConfig()
        config = WebSearchConfig(**base_config.model_dump())
        if isinstance(arguments.get("categories"), list) and arguments.get("categories"):
            config.default_categories = [str(value) for value in arguments.get("categories") if str(value).strip()] or config.default_categories
        if arguments.get("language"):
            config.default_language = str(arguments.get("language") or config.default_language)
        time_range = str(arguments.get("time_range") or "")
        startup = await ManagedSearXNGToolStartup.ensure_ready(self._settings_manager, self._searxng_manager)
        logger.info(
            "WebSearchTool: executing query='{}' max_results={} provider={} categories={} language={} time_range={} fallback_allowed={} managed_startup={}",
            str(query)[:120],
            max_results,
            config.provider,
            config.default_categories,
            config.default_language,
            time_range or "none",
            config.allow_duckduckgo_fallback,
            startup,
        )
        result = await WebSearchService(config, time_range=time_range).search(query, max_results=max_results)
        payload = {
            "query": result.query,
            "provider": result.provider,
            "ok": result.ok,
            "fallback_used": result.fallback_used,
            "primary_provider": result.primary_provider,
            "primary_error": result.primary_error,
            "runtime_date_context": RuntimeDateGrounding.runtime_context(),
            "results": [hit.model_dump() for hit in result.hits],
        }
        if not result.ok:
            payload["error"] = result.error or (
                f"Web search provider '{result.provider}' returned no usable results. "
                "Check web-search settings/API key or enable an explicit fallback provider."
            )
        elif result.fallback_used:
            payload["warning"] = (
                f"Used degraded DuckDuckGo fallback because {result.primary_provider or 'the primary provider'} failed: "
                f"{result.primary_error or 'unknown error'}"
            )
        elif result.error:
            payload["warning"] = result.error
        logger.info("WebSearchTool: finished ok={} provider={} fallback_used={} hits={}", result.ok, result.provider, result.fallback_used, len(result.hits))
        return payload


class WebResearchTool:
    """Run bounded web research and fetch candidate pages as evidence."""

    name = "web_research"
    description = (
        "Search the public web and fetch a bounded set of candidate pages as "
        "non-authoritative evidence. Use this when snippets are not enough: "
        "current news, rumors, release-date corroboration, official-page discovery, "
        "or items without structured metadata services. It returns source provenance "
        "and fetched evidence; category-specific facts still require interpretation."
    )
    intents = {Intent.SEARCH, Intent.DOWNLOAD}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["web_reader"]

    def __init__(
        self,
        web_reader: Optional[WebReader] = None,
        settings_manager: Optional[SettingsManager] = None,
        database: Any = None,
        category_registry: Any = None,
        prompt_scheduler: Any = None,
        searxng_manager: Any = None,
        llm_client: Any = None,
    ) -> None:
        self._web_reader = web_reader
        self._settings_manager = settings_manager
        self._database = database
        self._searxng_manager = searxng_manager

    def parameters(self) -> dict:
        """Return the public tool parameter schema."""
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The public web research query."},
                "intent": {"type": "string", "description": "Why this evidence is needed, e.g. release_date_corroboration, news_check, official_page_discovery."},
                "category_id": {"type": "string", "description": "Optional LJS category id for provenance scoping."},
                "item_id": {"type": "string", "description": "Optional tracked item id for provenance scoping."},
                "item_name": {"type": "string", "description": "Optional tracked item display name."},
                "categories": {"type": "array", "items": {"type": "string"}, "description": "Optional SearXNG categories such as general or news."},
                "language": {"type": "string", "description": "Optional search language such as it-IT, en-US, or auto."},
                "time_range": {"type": "string", "enum": ["", "day", "month", "year"], "description": "Optional freshness filter when supported."},
                "max_results": {"type": "integer", "description": "Maximum search results to inspect. Default: 5."},
                "max_urls_to_fetch": {"type": "integer", "description": "Maximum discovered URLs to fetch/extract. Default: 5."},
            },
            "required": ["query"],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Collect public web evidence through WebResearchService."""
        from src.core.models import WebResearchBudget, WebResearchRequest, WebSearchConfig
        from src.search.web.research import WebResearchService

        config = self._settings_manager.settings.web_search if self._settings_manager else WebSearchConfig()
        startup = await ManagedSearXNGToolStartup.ensure_ready(self._settings_manager, self._searxng_manager)
        repository = getattr(self._database, "web_research", None) if self._database else None
        logger.info(
            "WebResearchTool: executing query='{}' intent={} provider={} managed_startup={}",
            str(arguments.get("query", ""))[:120],
            arguments.get("intent") or "general_research",
            config.provider,
            startup,
        )
        request = WebResearchRequest(
            query=arguments["query"],
            intent=arguments.get("intent") or "general_research",
            category_id=arguments.get("category_id") or "",
            item_id=arguments.get("item_id") or "",
            item_name=arguments.get("item_name") or "",
            categories=arguments.get("categories") or config.default_categories,
            language=arguments.get("language") or config.default_language,
            time_range=arguments.get("time_range") or "",
            max_results=arguments.get("max_results") or config.max_results,
            budget=WebResearchBudget(
                max_urls_to_fetch=arguments.get("max_urls_to_fetch") or 5,
                require_page_extraction_before_facts=True,
            ),
        )
        bundle = await WebResearchService(
            config,
            web_reader=self._web_reader,
            repository=repository,
        ).collect_evidence(request)
        logger.info("WebResearchTool: finished ok={} sources={} evidence={} warnings={}", bundle.ok, len(bundle.sources), len(bundle.evidence), len(bundle.warnings))
        payload = bundle.model_dump()
        payload["runtime_date_context"] = RuntimeDateGrounding.runtime_context()
        payload["warning"] = (
            "Search snippets are not durable facts. Use fetched evidence and category-specific interpretation "
            "before changing item state or claiming confirmation."
        )
        return payload


class CategoryWebResearchTool:
    """Run category-owned public web research and interpretation hooks."""

    name = "category_web_research"
    description = (
        "Ask the owning category to build a public web-research plan, fetch bounded candidate pages, "
        "and interpret the evidence into non-mutating category-scoped signals. Use this for category "
        "items such as TV shows when official pages, air dates, delay news, rumours, or other public "
        "sources need category-specific interpretation. It does not authorize downloads or mutate items."
    )
    intents = {Intent.SEARCH, Intent.DOWNLOAD}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["web_reader"]

    def __init__(
        self,
        web_reader: Optional[WebReader] = None,
        settings_manager: Optional[SettingsManager] = None,
        database: Any = None,
        category_registry: Any = None,
        searxng_manager: Any = None,
        llm_client: Any = None,
    ) -> None:
        self._web_reader = web_reader
        self._settings_manager = settings_manager
        self._database = database
        self._category_registry = category_registry
        self._searxng_manager = searxng_manager
        self._llm_client = llm_client

    def parameters(self) -> dict:
        """Return the public tool parameter schema."""
        return {
            "type": "object",
            "properties": {
                "category_id": {"type": "string", "description": "LJS category id, e.g. tv."},
                "item_id": {"type": "string", "description": "Tracked item id or title."},
                "item_name": {"type": "string", "description": "Optional display title when different from item_id."},
                "intent": {"type": "string", "description": "Free-form semantic research objective. This is not an enum; use natural labels such as next season rumours, creator interviews, patch status, or next episode date."},
                "language": {"type": "string", "description": "Optional search language such as it-IT, en-US, or auto."},
                "query": {"type": "string", "description": "User-authored public web research query/search focus. Preserve concrete words from the user: season, episode, bug, version, creator, rumour, interview, or date focus."},
                "context": {"type": "object", "description": "Optional category-owned context, e.g. season, episode, or unit_key."},
            },
            "required": ["category_id", "item_id"],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Run category web research through the generic category orchestrator."""
        from src.core.models import CategoryWebResearchInput, WebSearchConfig
        from src.search.web.category_research import CategoryWebResearchService

        if not self._category_registry:
            return {"ok": False, "error": "Category registry is not configured."}
        config = self._settings_manager.settings.web_search if self._settings_manager else WebSearchConfig()
        startup = await ManagedSearXNGToolStartup.ensure_ready(self._settings_manager, self._searxng_manager)
        repository = getattr(self._database, "web_research", None) if self._database else None
        category_id = arguments.get("category_id") or context.category_id or ""
        logger.info(
            "CategoryWebResearchTool: executing category={} item={} intent={} provider={} managed_startup={}",
            category_id,
            arguments.get("item_id") or "",
            arguments.get("intent") or "general_research",
            config.provider,
            startup,
        )
        raw_context = arguments.get("context") if isinstance(arguments.get("context"), dict) else {}
        if arguments.get("query"):
            raw_context = {**raw_context, "user_query": str(arguments.get("query") or "")}
        research_input = CategoryWebResearchInput(
            category_id=str(category_id),
            item_id=str(arguments.get("item_id") or ""),
            item_name=str(arguments.get("item_name") or arguments.get("item_id") or ""),
            intent=str(arguments.get("intent") or "general_research"),
            language=str(arguments.get("language") or config.default_language),
            context=raw_context,
        )
        result = await CategoryWebResearchService(
            category_registry=self._category_registry,
            config=config,
            web_reader=self._web_reader,
            repository=repository,
            llm_client=self._llm_client,
        ).research(research_input)
        logger.info("CategoryWebResearchTool: finished ok={} category={} item={} evidence={} facts={}", result.ok, result.category_id, result.item_id, len(result.bundle.evidence), len(result.interpretation.facts))
        payload = result.model_dump()
        payload["runtime_date_context"] = RuntimeDateGrounding.runtime_context()
        degraded = any("degraded" in str(w).casefold() or "fallback" in str(w).casefold() for w in (result.warnings or []) + (result.bundle.warnings or []))
        payload["source_quality"] = {
            "primary_provider_degraded": degraded,
            "fetched_evidence_count": len(result.bundle.evidence),
            "candidate_source_count": len(result.bundle.sources),
            "answer_policy": (
                "Primary web provider failed/degraded. Treat results as leads only; disclose limited confidence, do not claim absence of official news, and prefer another search/read step if the answer depends on freshness."
                if degraded else
                "Use fetched evidence and category interpretation; search snippets remain leads, not facts."
            ),
        }
        payload["warning"] = (
            "Category web research stores provenance and interpreted signals only. "
            "Use category workflows/coordinators for any item mutation or download decision."
        )
        return payload


class CreateWebInformationWatchTool:
    """Create a proactive recurring public web-information watch."""

    name = "create_web_information_watch"
    description = (
        "Create a recurring public web-information watch for news, rumors, release notes, patch notes, "
        "or category-item updates. The watch uses web_research/category_web_research when it runs, stores "
        "evidence provenance, and does not mutate category items or queue downloads unless explicitly allowed "
        "and later category/download tools prove availability."
    )
    intents = {Intent.CONFIG, Intent.SEARCH, Intent.DOWNLOAD}
    allow_direct = True
    requires_confirmation = True
    destructive = False
    required_dependencies = ["database"]

    def __init__(
        self,
        *,
        web_reader: Optional[WebReader] = None,
        settings_manager: Optional[SettingsManager] = None,
        database: Any = None,
        category_registry: Any = None,
        prompt_scheduler: Any = None,
        llm_client: Any = None,
    ) -> None:
        self._web_reader = web_reader
        self._settings_manager = settings_manager
        self._database = database
        self._category_registry = category_registry
        self._prompt_scheduler = prompt_scheduler
        self._llm_client = llm_client
        
    def parameters(self) -> dict:
        """Return the public tool parameter schema."""
        return {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short user-facing watch title."},
                "objective": {"type": "string", "description": "What to track and what counts as a meaningful update."},
                "query": {"type": "string", "description": "Primary public web research query or focus. Preserve exact user terms such as title, season/version, bug ID, creator, platform, or date requirement."},
                "intent": {"type": "string", "description": "Free-form semantic watch objective, not an enum. Preserve the user's real target, e.g. creator interviews about next season, patch status for a named bug, or next episode schedule changes."},
                "category_id": {"type": "string", "description": "Optional LJS category id for category-owned interpretation."},
                "item_id": {"type": "string", "description": "Optional tracked/prospective category item id/title."},
                "item_name": {"type": "string", "description": "Optional display name for the watched item."},
                "language": {"type": "string", "description": "Optional search language such as it-IT, en-US, or auto."},
                "cadence_minutes": {"type": "integer", "description": "Recurring cadence in minutes. 10080=weekly."},
                "delay_minutes": {"type": "integer", "description": "Optional first-run delay in minutes."},
                "notify_only_if_meaningful": {"type": "boolean", "description": "Suppress no-change notifications. Default true."},
                "allow_download_queueing": {"type": "boolean", "description": "Set true only if the original user explicitly asked to start downloading when safe."},
                "query_plan": {"type": "object", "description": "Optional bounded query plan: queries, categories, time_range, max_results, max_urls_to_fetch."},
            },
            "required": ["title", "objective"],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Create the watch and schedule its recurring check prompt."""
        from src.core.models import WebSearchConfig
        from src.search.web.information_watch import WebInformationWatchPromptBuilder, WebInformationWatchService

        repository = getattr(self._database, "web_research", None) if self._database else None
        if not repository:
            return {"ok": False, "error": "Web research repository is not configured."}
        config = self._settings_manager.settings.web_search if self._settings_manager else WebSearchConfig()
        service = WebInformationWatchService(
            repository=repository,
            config=config,
            web_reader=self._web_reader,
            category_registry=self._category_registry,
            llm_client=self._llm_client,
        )
        watch = await service.create_watch(
            title=str(arguments.get("title") or arguments.get("objective") or "Web information watch"),
            objective=str(arguments.get("objective") or arguments.get("query") or arguments.get("title") or ""),
            query=str(arguments.get("query") or ""),
            intent=str(arguments.get("intent") or "general_research"),
            owner_type="category_item" if arguments.get("category_id") or arguments.get("item_id") else "user_task",
            category_id=str(arguments.get("category_id") or ""),
            item_id=str(arguments.get("item_id") or arguments.get("item_name") or ""),
            item_name=str(arguments.get("item_name") or arguments.get("item_id") or ""),
            language=str(arguments.get("language") or config.default_language),
            cadence_minutes=int(arguments.get("cadence_minutes") or 10080),
            delay_minutes=arguments.get("delay_minutes"),
            notify_only_if_meaningful=bool(arguments.get("notify_only_if_meaningful", True)),
            llm_evaluation_required=True,
            allow_download_queueing=bool(arguments.get("allow_download_queueing", False)),
            query_plan=arguments.get("query_plan") if isinstance(arguments.get("query_plan"), dict) else {},
        )
        scheduled_task = None
        if self._prompt_scheduler:
            prompt = WebInformationWatchPromptBuilder.scheduled_prompt(watch)
            scheduled_task = await self._prompt_scheduler.create_task(
                prompt=prompt,
                interval_minutes=watch.cadence_minutes,
                user_id=context.user_id,
                channel=context.source or "web",
                title=f"Watch: {watch.title}",
                task_type="condition_check",
                schedule_type="recurring",
                delay_minutes=arguments.get("delay_minutes") if arguments.get("delay_minutes") is not None else watch.cadence_minutes,
                session_id=context.session_id,
            )
        logger.info(
            "CreateWebInformationWatchTool: created watch id={} category={} item={} scheduled_task={}",
            watch.id,
            watch.category_id or "none",
            watch.item_id or watch.item_name or "none",
            getattr(scheduled_task, "id", "none"),
        )
        return {
            "ok": True,
            "watch": watch.model_dump(mode="json"),
            "scheduled_task": {
                "id": scheduled_task.id,
                "next_run_at": scheduled_task.next_run_at.isoformat() if scheduled_task.next_run_at else None,
                "interval_minutes": scheduled_task.interval_minutes,
            } if scheduled_task else None,
            "message": "Created web information watch. It will use fetched evidence and LLM review before notifying.",
            "next_actions": ["run_web_information_watch", "list_web_information_watches", "disable_web_information_watch"],
        }


class RunWebInformationWatchTool:
    """Run a web-information watch immediately."""

    name = "run_web_information_watch"
    description = (
        "Run one existing web-information watch now. It collects bounded public evidence, records an event, "
        "and tells the assistant whether a notification seems warranted. It never queues downloads or mutates category items."
    )
    intents = {Intent.CONFIG, Intent.SEARCH, Intent.DOWNLOAD}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["database"]

    def __init__(self, *, web_reader: Optional[WebReader] = None, settings_manager: Optional[SettingsManager] = None, database: Any = None, category_registry: Any = None, llm_client: Any = None) -> None:
        self._web_reader = web_reader
        self._settings_manager = settings_manager
        self._database = database
        self._category_registry = category_registry
        self._llm_client = llm_client

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "watch_id": {"type": "string", "description": "The web information watch id."},
            },
            "required": ["watch_id"],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        from src.core.models import WebSearchConfig
        from src.search.web.information_watch import WebInformationWatchService

        repository = getattr(self._database, "web_research", None) if self._database else None
        config = self._settings_manager.settings.web_search if self._settings_manager else WebSearchConfig()
        return await WebInformationWatchService(
            repository=repository,
            config=config,
            web_reader=self._web_reader,
            category_registry=self._category_registry,
            llm_client=self._llm_client,
        ).run_watch(str(arguments.get("watch_id") or ""))


class ListWebInformationWatchesTool:
    """List active web-information watches."""

    name = "list_web_information_watches"
    description = "List web-information watches and their current run state."
    intents = {Intent.CONFIG, Intent.SEARCH, Intent.DOWNLOAD}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["database"]

    def __init__(self, *, database: Any = None) -> None:
        self._database = database

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "enabled_only": {"type": "boolean"},
                "category_id": {"type": "string"},
                "item_id": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": [],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        repository = getattr(self._database, "web_research", None) if self._database else None
        if not repository:
            return {"ok": False, "error": "Web research repository is not configured."}
        watches = await repository.list_information_watches(
            enabled_only=bool(arguments.get("enabled_only", False)),
            category_id=str(arguments.get("category_id") or ""),
            item_id=str(arguments.get("item_id") or ""),
            limit=int(arguments.get("limit") or 100),
        )
        return {"ok": True, "watches": watches}


class DisableWebInformationWatchTool:
    """Disable a web-information watch without deleting its history."""

    name = "disable_web_information_watch"
    description = "Pause/disable a web-information watch so it no longer runs."
    intents = {Intent.CONFIG, Intent.SEARCH, Intent.DOWNLOAD}
    allow_direct = True
    requires_confirmation = True
    destructive = False
    required_dependencies = ["database"]

    def __init__(self, *, database: Any = None) -> None:
        self._database = database

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "watch_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["watch_id"],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        repository = getattr(self._database, "web_research", None) if self._database else None
        if not repository:
            return {"ok": False, "error": "Web research repository is not configured."}
        watch = await repository.disable_information_watch(
            str(arguments.get("watch_id") or ""),
            reason=str(arguments.get("reason") or "user_requested"),
        )
        return {"ok": bool(watch), "watch": watch, "message": "Watch disabled." if watch else "Watch not found."}


class BrowserOpenTool:
    """Open a URL in a real browser and get a readable summary."""

    name = "browser_open"
    description = (
        "Open a URL in a real browser and get a readable summary with "
        "extracted links. Use this to read pages that require JavaScript "
        "or for dynamic content."
    )
    intents = {Intent.SEARCH, Intent.DOWNLOAD}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["browser_runtime"]

    def __init__(self, browser_runtime: Optional[BrowserRuntime] = None) -> None:
        self._browser_runtime = browser_runtime

    def parameters(self) -> dict:
        """Return the public tool parameter schema.

        The schema is consumed by the LLM runtime and should remain
        backward-compatible.  Add optional fields for extensions whenever
        possible, and keep validation rules mirrored in execute().
        """
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to open.",
                },
                "purpose": {
                    "type": "string",
                    "description": "Purpose: 'research', 'reviews', or 'release_info'.",
                },
            },
            "required": ["url"],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Open a URL in a real browser."""
        url = normalize_search_result_url(arguments["url"])
        if not url:
            return {"error": f"browser_open requires a resolved http(s) URL, got {arguments.get('url')!r}"}
        purpose = arguments.get("purpose", "research")
        provider = _browser_provider(self._browser_runtime)
        session = _get_browser_session(self._browser_runtime, context)
        handler = provider.make_browser_open_handler(session)
        return await handler(url, purpose=purpose)


class BrowserReadSelectedTool:
    """Read one of the links from a previously opened page by its index number."""

    name = "browser_read_selected"
    description = (
        "Read one of the links from a previously opened page by its index number."
    )
    intents = {Intent.SEARCH, Intent.DOWNLOAD}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["browser_runtime"]

    def __init__(self, browser_runtime: Optional[BrowserRuntime] = None) -> None:
        self._browser_runtime = browser_runtime

    def parameters(self) -> dict:
        """Return the public tool parameter schema.

        The schema is consumed by the LLM runtime and should remain
        backward-compatible.  Add optional fields for extensions whenever
        possible, and keep validation rules mirrored in execute().
        """
        return {
            "type": "object",
            "properties": {
                "link_index": {
                    "type": "integer",
                    "description": "The index of the link to follow (0-based).",
                },
            },
            "required": ["link_index"],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Read one of the links from a previously opened page."""
        link_index = arguments["link_index"]
        provider = _browser_provider(self._browser_runtime)
        session = _get_browser_session(self._browser_runtime, context)
        read_handler = provider.make_browser_read_selected_handler(session)
        return await read_handler(link_index)


class ResearchReviewsTool:
    """Find review scores and critic consensus for a movie or TV show."""

    name = "research_reviews"
    description = (
        "Find review scores and critic consensus for a movie or TV show "
        "from Rotten Tomatoes, IMDb, and Metacritic. "
        "Returns sourced evidence with citations."
    )
    intents = {Intent.SEARCH, Intent.DOWNLOAD}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["browser_runtime", "web_reader"]

    def __init__(
        self,
        browser_runtime: Optional[BrowserRuntime] = None,
        web_reader: Optional[WebReader] = None,
        settings_manager: Optional[SettingsManager] = None,
        database: Any = None,
    ) -> None:
        self._browser_runtime = browser_runtime
        self._web_reader = web_reader
        self._settings_manager = settings_manager
        self._database = database

    def parameters(self) -> dict:
        """Return the public tool parameter schema.

        The schema is consumed by the LLM runtime and should remain
        backward-compatible.  Add optional fields for extensions whenever
        possible, and keep validation rules mirrored in execute().
        """
        return {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "The movie or TV show name.",
                },
            },
            "required": ["title"],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Find review scores and critic consensus."""
        title = arguments["title"]
        from src.ai.web_researcher import WebResearcher

        researcher = WebResearcher(
            runtime=self._browser_runtime, web_reader=self._web_reader
        )
        from src.integrations.rotten_tomatoes import RottenTomatoesExtractor
        from src.integrations.imdb_extractor import IMDbExtractor

        researcher.register_extractor(RottenTomatoesExtractor())
        researcher.register_extractor(IMDbExtractor())
        return await researcher.research_reviews(title)


class ResearchReleaseInfoTool:
    """Find release dates, renewal status, and episode schedule info."""

    name = "research_release_info"
    description = (
        "Find release dates, renewal status, and episode schedule info "
        "for a TV show or movie using web search."
    )
    intents = {Intent.SEARCH, Intent.DOWNLOAD}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["browser_runtime", "web_reader"]

    def __init__(
        self,
        browser_runtime: Optional[BrowserRuntime] = None,
        web_reader: Optional[WebReader] = None,
        settings_manager: Optional[SettingsManager] = None,
        database: Any = None,
    ) -> None:
        self._browser_runtime = browser_runtime
        self._web_reader = web_reader
        self._settings_manager = settings_manager
        self._database = database

    def parameters(self) -> dict:
        """Return the public tool parameter schema.

        The schema is consumed by the LLM runtime and should remain
        backward-compatible.  Add optional fields for extensions whenever
        possible, and keep validation rules mirrored in execute().
        """
        return {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "The movie or TV show name.",
                },
            },
            "required": ["title"],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Find release dates, renewal status, and episode schedule info."""
        title = arguments["title"]
        from src.ai.web_researcher import WebResearcher

        config = self._settings_manager.settings.web_search if self._settings_manager else None
        repository = getattr(self._database, "web_research", None) if self._database else None
        researcher = WebResearcher(
            runtime=self._browser_runtime,
            web_reader=self._web_reader,
            web_search_config=config,
            web_research_repository=repository,
        )
        return await researcher.research_release_info(title)


class BrowserFindLinksTool:
    """Find links matching a purpose on a previously opened page."""

    name = "browser_find_links"
    description = (
        "From a previously opened page, find links matching a purpose "
        "like 'reviews', 'episodes', 'cast', or 'release date'."
    )
    intents = {Intent.SEARCH, Intent.DOWNLOAD}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["browser_runtime"]

    def __init__(self, browser_runtime: Optional[BrowserRuntime] = None) -> None:
        self._browser_runtime = browser_runtime

    def parameters(self) -> dict:
        """Return the public tool parameter schema.

        The schema is consumed by the LLM runtime and should remain
        backward-compatible.  Add optional fields for extensions whenever
        possible, and keep validation rules mirrored in execute().
        """
        return {
            "type": "object",
            "properties": {
                "purpose": {
                    "type": "string",
                    "description": "What kind of links to find: 'reviews', 'episodes', 'cast', 'release date', 'critic reviews', 'audience reviews'.",
                },
                "max_links": {
                    "type": "integer",
                    "description": "Maximum links to return. Default: 5.",
                },
            },
            "required": ["purpose"],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Find links matching a purpose on a previously opened page."""
        purpose = arguments["purpose"]
        max_links = arguments.get("max_links", 5)
        provider = _browser_provider(self._browser_runtime)
        session = _get_browser_session(self._browser_runtime, context)
        handler = provider.make_browser_find_links_handler(session)
        return await handler(purpose=purpose, max_links=max_links)


class BrowserEvidenceReportTool:
    """Return all evidence and citations collected so far during this browsing session."""

    name = "browser_evidence_report"
    description = (
        "Return all evidence and citations collected so far "
        "during this browsing session."
    )
    intents = {Intent.SEARCH, Intent.DOWNLOAD}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["browser_runtime"]

    def __init__(self, browser_runtime: Optional[BrowserRuntime] = None) -> None:
        self._browser_runtime = browser_runtime

    def parameters(self) -> dict:
        """Return the public tool parameter schema.

        The schema is consumed by the LLM runtime and should remain
        backward-compatible.  Add optional fields for extensions whenever
        possible, and keep validation rules mirrored in execute().
        """
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Return all evidence and citations collected so far."""
        provider = _browser_provider(self._browser_runtime)
        session = _get_browser_session(self._browser_runtime, context)
        handler = provider.make_browser_evidence_report_handler(session)
        return await handler()


class BrowserExtractTool:
    """Open a URL and extract structured facts relevant to a specific question."""

    name = "browser_extract"
    description = (
        "Open a URL and extract structured facts relevant to a specific question."
    )
    intents = {Intent.SEARCH, Intent.DOWNLOAD}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["browser_runtime", "web_reader"]

    def __init__(self, browser_runtime: Optional[BrowserRuntime] = None, web_reader: Optional[WebReader] = None) -> None:
        self._browser_runtime = browser_runtime
        self._web_reader = web_reader

    def parameters(self) -> dict:
        """Return the public tool parameter schema.

        The schema is consumed by the LLM runtime and should remain
        backward-compatible.  Add optional fields for extensions whenever
        possible, and keep validation rules mirrored in execute().
        """
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to open and extract from.",
                },
                "question": {
                    "type": "string",
                    "description": "The focused question to answer from this page.",
                },
            },
            "required": ["url", "question"],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Open a URL and extract structured facts."""
        url = normalize_search_result_url(arguments["url"])
        if not url:
            return {"error": f"browser_extract requires a resolved http(s) URL, got {arguments.get('url')!r}"}
        question = arguments["question"]
        from src.ai.web_researcher import WebResearcher

        researcher = WebResearcher(
            runtime=self._browser_runtime, web_reader=self._web_reader
        )
        return await researcher.research_article(url, question=question)


class WebToolProvider:
    """Provides web and browser agent tools.

    Aggregates all AgentTool implementations from the web domain
    and returns instantiated instances via get_tools().
    """

    def __init__(
        self,
        web_reader: Optional[WebReader] = None,
        browser: Optional[Browser] = None,
        browser_runtime: Optional[BrowserRuntime] = None,
        settings_manager: Optional[SettingsManager] = None,
        database: Any = None,
        category_registry: Any = None,
        prompt_scheduler: Any = None,
        searxng_manager: Any = None,
        llm_client: Any = None,
    ) -> None:
        """Initialize with optional dependencies.

        Args:
            web_reader: WebReader instance.
            browser: Browser instance.
            browser_runtime: BrowserRuntime instance.
            settings_manager: SettingsManager for web-search provider config.
        """
        self._web_reader = web_reader
        self._browser = browser
        self._browser_runtime = browser_runtime
        self._settings_manager = settings_manager
        self._database = database
        self._category_registry = category_registry
        self._prompt_scheduler = prompt_scheduler
        self._searxng_manager = searxng_manager
        self._llm_client = llm_client

    def get_tools(self) -> list:
        """Return instantiated web/browser tool instances.

        Returns:
            List of AgentTool-compatible tool instances.
        """
        return [
            ReadWebPageTool(web_reader=self._web_reader),
            BrowsePageTool(browser=self._browser, browser_runtime=self._browser_runtime),
            WebSearchTool(browser_runtime=self._browser_runtime, settings_manager=self._settings_manager, searxng_manager=self._searxng_manager),
            WebResearchTool(web_reader=self._web_reader, settings_manager=self._settings_manager, database=self._database, searxng_manager=self._searxng_manager),
            CategoryWebResearchTool(
                web_reader=self._web_reader,
                settings_manager=self._settings_manager,
                database=self._database,
                category_registry=self._category_registry,
                searxng_manager=self._searxng_manager,
                llm_client=self._llm_client,
            ),
            CreateWebInformationWatchTool(
                web_reader=self._web_reader,
                settings_manager=self._settings_manager,
                database=self._database,
                category_registry=self._category_registry,
                prompt_scheduler=self._prompt_scheduler,
                llm_client=self._llm_client,
            ),
            RunWebInformationWatchTool(
                web_reader=self._web_reader,
                settings_manager=self._settings_manager,
                database=self._database,
                category_registry=self._category_registry,
                llm_client=self._llm_client,
            ),
            ListWebInformationWatchesTool(database=self._database),
            DisableWebInformationWatchTool(database=self._database),
            BrowserOpenTool(browser_runtime=self._browser_runtime),
            BrowserReadSelectedTool(browser_runtime=self._browser_runtime),
            ResearchReviewsTool(browser_runtime=self._browser_runtime, web_reader=self._web_reader),
            ResearchReleaseInfoTool(
                browser_runtime=self._browser_runtime,
                web_reader=self._web_reader,
                settings_manager=self._settings_manager,
                database=self._database,
            ),
            BrowserFindLinksTool(browser_runtime=self._browser_runtime),
            BrowserEvidenceReportTool(browser_runtime=self._browser_runtime),
            BrowserExtractTool(browser_runtime=self._browser_runtime, web_reader=self._web_reader),
        ]
