"""
Browser session for LJS agentic browsing.

Provides a stateful bounded browser session for an agent task.
Tracks visited URLs, current page, extracted evidence, and enforces
max pages and max runtime to prevent infinite navigation loops.
"""

import time
from loguru import logger
from typing import Optional

from src.core.models import BrowserFetchRequest, BrowserFetchResult, PageLink


class BrowserSession:
    """Stateful bounded browser session for an agent task.

    Maintains navigation state across multiple browser interactions
    within a single agent run. Enforces max pages and max runtime
    limits to prevent runaway browsing. Keeps browser state separate
    from torrent scraping state.
    """

    def __init__(
        self,
        runtime: "BrowserRuntime",
        session_id: str,
        max_pages: int = 8,
        max_seconds: int = 90,
    ):
        """Initialize a bounded browser session.

        Args:
            runtime: Shared BrowserRuntime for page fetching.
            session_id: Unique session identifier (agent run ID).
            max_pages: Maximum total page fetches for this session.
            max_seconds: Maximum total runtime for this session.
        """
        self._runtime = runtime
        self._session_id = session_id
        self._max_pages = max_pages
        self._max_seconds = max_seconds
        self._started_at = time.monotonic()
        self._page_count = 0
        self._visited_urls: list[str] = []
        self._current_page: Optional[BrowserFetchResult] = None
        self._current_links: list[PageLink] = []
        self._evidence: list[dict] = []

    @property
    def is_exhausted(self) -> bool:
        """Whether the session has exceeded page or time limits."""
        if self._page_count >= self._max_pages:
            return True
        return (time.monotonic() - self._started_at) >= self._max_seconds

    @property
    def remaining_pages(self) -> int:
        """How many page fetches remain in this session."""
        return max(0, self._max_pages - self._page_count)

    @property
    def current_links(self) -> list[PageLink]:
        """Return links extracted from the currently open page.

        Browser tools use this public seam instead of reaching into the
        session's private navigation state.  A copy is returned so callers
        cannot mutate session bookkeeping accidentally.
        """
        return list(self._current_links)

    async def open(self, url: str, purpose: str = "research") -> BrowserFetchResult:
        """Open a URL and store it as the current page.

        Fails gracefully if the session is exhausted, the URL has already
        been visited (loop prevention), or the browser fetch fails.

        Args:
            url: The URL to navigate to.
            purpose: The declared purpose for logging and budgeting.

        Returns:
            BrowserFetchResult with page content or error status.
        """
        if self.is_exhausted:
            return BrowserFetchResult(
                ok=False,
                url=url,
                final_url=url,
                status=0,
                blocked_reason="session_exhausted",
                error=f"Session exhausted: {self._page_count}/{self._max_pages} pages",
            )

        if url in self._visited_urls:
            return BrowserFetchResult(
                ok=False,
                url=url,
                final_url=url,
                status=0,
                blocked_reason="already_visited",
                error="URL already visited in this session",
            )

        self._page_count += 1
        self._visited_urls.append(url)

        request = BrowserFetchRequest(
            url=url,
            wait_seconds=2.0,
            max_content_chars=4000,
            screenshot_on_failure=True,
            purpose=purpose,
        )
        result = await self._runtime.fetch(request)

        if result.ok:
            self._current_page = result
            self._current_links = result.links
        else:
            self._current_page = None
            self._current_links = []

        return result

    def get_link(self, index: int) -> PageLink | None:
        """Get a previously opened page's link by index.

        Args:
            index: The zero-based link index from the current page.

        Returns:
            PageLink or None if index is out of range.
        """
        if not self._current_links or index < 0 or index >= len(self._current_links):
            return None
        return self._current_links[index]

    def add_evidence(self, claim: str, source: str, url: str, snippet: str = "") -> None:
        """Record a sourced claim as evidence collected in this session.

        Args:
            claim: The evidence claim text.
            source: The source name (e.g., 'Rotten Tomatoes').
            url: The source URL.
            snippet: Supporting snippet from the page.
        """
        self._evidence.append({
            "claim": claim,
            "source": source,
            "url": url,
            "snippet": snippet,
        })

    def build_report(self) -> dict:
        """Build a compact evidence report from all collected evidence.

        Returns:
            Dict with 'evidence' list and 'visited_urls' list.
        """
        return {
            "evidence": self._evidence,
            "visited_urls": list(self._visited_urls),
        }
