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

    def __init__(self, browser_runtime: Optional[BrowserRuntime] = None, settings_manager: Optional[SettingsManager] = None) -> None:
        self._browser_runtime = browser_runtime
        self._settings_manager = settings_manager

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
            },
            "required": ["query"],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Search the web using the configured provider service."""
        query = arguments["query"]
        max_results = arguments.get("max_results", 5)
        from src.core.models import WebSearchConfig
        from src.search.web.service import WebSearchService

        config = self._settings_manager.settings.web_search if self._settings_manager else WebSearchConfig()
        result = await WebSearchService(config).search(query, max_results=max_results)
        payload = {
            "query": result.query,
            "provider": result.provider,
            "ok": result.ok,
            "results": [hit.model_dump() for hit in result.hits],
        }
        if not result.ok:
            payload["error"] = result.error or (
                f"Web search provider '{result.provider}' returned no usable results. "
                "Check web-search settings/API key or enable an explicit fallback provider."
            )
        elif result.error:
            payload["warning"] = result.error
        return payload


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

        researcher = WebResearcher(
            runtime=self._browser_runtime, web_reader=self._web_reader
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

    def get_tools(self) -> list:
        """Return instantiated web/browser tool instances.

        Returns:
            List of AgentTool-compatible tool instances.
        """
        return [
            ReadWebPageTool(web_reader=self._web_reader),
            BrowsePageTool(browser=self._browser, browser_runtime=self._browser_runtime),
            WebSearchTool(browser_runtime=self._browser_runtime, settings_manager=self._settings_manager),
            BrowserOpenTool(browser_runtime=self._browser_runtime),
            BrowserReadSelectedTool(browser_runtime=self._browser_runtime),
            ResearchReviewsTool(browser_runtime=self._browser_runtime, web_reader=self._web_reader),
            ResearchReleaseInfoTool(browser_runtime=self._browser_runtime, web_reader=self._web_reader),
            BrowserFindLinksTool(browser_runtime=self._browser_runtime),
            BrowserEvidenceReportTool(browser_runtime=self._browser_runtime),
            BrowserExtractTool(browser_runtime=self._browser_runtime, web_reader=self._web_reader),
        ]
