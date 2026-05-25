"""
Browser tool handlers for LJS agentic browsing.

Provides safe, bounded browser tools for the LLM agent to use during media
research tasks. General web search is delegated to the configured web search
provider abstraction; browser navigation remains session-based and bounded.
"""

from typing import Awaitable, Callable


class BrowserToolProvider:
    """Provides tool handlers for LLM browser control."""

    def __init__(self, runtime: "BrowserRuntime", web_search_config: "WebSearchConfig | None" = None):
        """Initialize with a shared browser runtime and optional search config.

        Args:
            runtime: BrowserRuntime instance for Playwright page fetches.
            web_search_config: Optional provider config used by web_search.
        """
        self._runtime = runtime
        self._web_search_config = web_search_config

    def new_session(self, session_id: str) -> "BrowserSession":
        """Create a new bounded browsing session for one agent task."""
        from src.ai.browser_session import BrowserSession

        return BrowserSession(runtime=self._runtime, session_id=session_id)

    def make_browser_open_handler(self, session: "BrowserSession") -> Callable[[str, str], Awaitable[dict]]:
        """Create a browser_open tool handler bound to a session."""
        async def _browser_open(url: str, purpose: str = "research") -> dict:
            result = await session.open(url, purpose=purpose)
            return {
                "ok": result.ok,
                "title": result.title,
                "final_url": result.final_url,
                "status": result.status,
                "text_preview": result.text[:2000] if result.text else "",
                "links": [{"text": link.text, "url": link.url} for link in result.links[:20]],
                "challenge_detected": result.challenge_detected,
                "blocked_reason": result.blocked_reason,
            }

        return _browser_open

    def make_browser_read_selected_handler(self, session: "BrowserSession") -> Callable[[int], Awaitable[dict]]:
        """Create a browser_read_selected tool handler bound to a session."""
        async def _browser_read_selected(link_index: int) -> dict:
            link = session.get_link(link_index)
            if not link:
                return {"error": f"No link at index {link_index} from current page"}
            result = await session.open(link.url, purpose="read_selected")
            return {
                "ok": result.ok,
                "title": result.title,
                "url": result.final_url,
                "text_preview": result.text[:2000] if result.text else "",
                "challenge_detected": result.challenge_detected,
            }

        return _browser_read_selected

    def make_browser_find_links_handler(self, session: "BrowserSession") -> Callable[..., Awaitable[dict]]:
        """Create a browser_find_links tool handler for semantic link filtering."""
        async def _browser_find_links(purpose: str = "reviews", max_links: int = 5) -> dict:
            links = session.current_links
            if not links:
                return {"links": [], "error": "No current page loaded. Use browser_open first."}

            purpose_lower = purpose.lower()
            scored = []
            for link in links:
                score = 0
                link_lower = (link.text + " " + link.url).lower()
                for keyword in purpose_lower.split():
                    if keyword in link_lower:
                        score += 1
                if purpose_lower in link_lower:
                    score += 3
                if score > 0:
                    scored.append({"text": link.text, "url": link.url, "score": score})

            scored.sort(key=lambda item: item["score"], reverse=True)
            return {"links": scored[:max_links]}

        return _browser_find_links

    def make_browser_evidence_report_handler(self, session: "BrowserSession") -> Callable[[], Awaitable[dict]]:
        """Create a browser_evidence_report tool handler bound to a session."""
        async def _browser_evidence_report() -> dict:
            return session.build_report()

        return _browser_evidence_report

    async def web_search(self, query: str, max_results: int = 5) -> dict:
        """Search the web through the configured WebSearchService."""
        from src.core.models import WebSearchConfig
        from src.search.web.service import WebSearchService

        config = self._web_search_config or WebSearchConfig(allow_duckduckgo_fallback=True, provider="duckduckgo_html")
        result = await WebSearchService(config).search(query, max_results=max_results)
        return {
            "query": result.query,
            "provider": result.provider,
            "ok": result.ok,
            "results": [hit.model_dump() for hit in result.hits],
            "error": result.error,
        }
