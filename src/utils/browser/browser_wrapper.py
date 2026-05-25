"""
Playwright browser automation for LJS.

Provides a shared browser instance that can navigate web pages, handle
JavaScript rendering, bypass Cloudflare challenges, and extract content.
Used by search providers for Cloudflare-protected sites and by the AI
assistant as a web browsing tool.

Playwright is an optional dependency — if not installed, all browser
features degrade gracefully (search providers fall back to httpx,
the browser tool returns an error message).

This class is a backwards-compatible wrapper around BrowserRuntime
in src/utils/browser/runtime.py. New code should use BrowserRuntime
directly; this wrapper exists for existing callers that depend on
the fetch_page() / scrape_html() / available API surface.
"""

import asyncio
from loguru import logger
from typing import Optional

from src.utils.browser.runtime import BrowserRuntime, ChallengeDetector, BrowserDomainPolicy
from src.core.models import BrowserFetchRequest, BrowserHealth

try:
    from playwright.async_api import async_playwright
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False


class Browser:
    """Backwards-compatible wrapper around BrowserRuntime.

    Delegates to BrowserRuntime for all browser operations while
    preserving the fetch_page() / scrape_html() / available API
    that existing search providers and tool registries depend on.

    If Playwright is not installed, all methods return empty/error results
    and log a warning.
    """

    MAX_CONTENT_CHARS = 8000
    DEFAULT_TIMEOUT = 30

    def __init__(self, runtime: Optional[BrowserRuntime] = None):
        self._runtime: Optional[BrowserRuntime] = runtime
        self._runtime_lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        """Whether Playwright is installed and usable.

        Kept for backward compatibility. Prefer BrowserRuntime.health_check()
        for detailed readiness status.
        """
        return _PLAYWRIGHT_AVAILABLE

    async def _ensure_runtime(self) -> Optional[BrowserRuntime]:
        """Create and health-check the BrowserRuntime on first use.

        If the Browser was constructed with a shared runtime, uses it directly.
        Otherwise creates its own runtime with default policy and detector.

        Returns:
            BrowserRuntime instance or None if unavailable.
        """
        if not _PLAYWRIGHT_AVAILABLE:
            return None

        if self._runtime:
            return self._runtime

        async with self._runtime_lock:
            if self._runtime:
                return self._runtime

            domain_policy = BrowserDomainPolicy()
            challenge_detector = ChallengeDetector()
            self._runtime = BrowserRuntime(
                domain_policy=domain_policy,
                challenge_detector=challenge_detector,
            )
            logger.info("Playwright browser runtime created (standalone)")
            return self._runtime

    async def fetch_page(self, url: str, wait_seconds: float = 2.0) -> Optional[dict]:
        """Navigate to a URL and extract the rendered page content.

        Delegates to BrowserRuntime.fetch() and converts the structured
        result back to a dict for backward compatibility.

        Args:
            url: The URL to navigate to.
            wait_seconds: Extra seconds to wait after page load for
                JavaScript to finish rendering (default 2.0).

        Returns:
            Dict with 'title', 'content', 'html', 'url', 'status' keys,
            or None on failure.
        """
        runtime = await self._ensure_runtime()
        if not runtime:
            logger.warning("Browser not available — install playwright")
            return None

        request = BrowserFetchRequest(
            url=url,
            wait_seconds=wait_seconds,
            max_content_chars=self.MAX_CONTENT_CHARS,
        )
        result = await runtime.fetch(request)

        if not result.ok and result.blocked_reason:
            logger.warning(f"Browser fetch blocked for {url}: {result.blocked_reason}")

        if result.error and result.blocked_reason == "fetch_error":
            return None

        return {
            "title": result.title or url,
            "content": result.text,
            "html": result.html,
            "url": result.final_url,
            "status": result.status,
        }

    async def scrape_html(self, url: str, wait_seconds: float = 2.0) -> Optional[str]:
        """Navigate to a URL and return the fully rendered HTML.

        Useful for search providers that need HTML (not just text) to
        parse structured data like torrent result tables.

        Args:
            url: The URL to navigate to.
            wait_seconds: Extra seconds to wait after page load.

        Returns:
            The full HTML content of the page, or None on failure.
        """
        result = await self.fetch_page(url, wait_seconds=wait_seconds)
        if result:
            return result.get("html")
        return None

    async def close(self) -> None:
        """Shut down the browser and Playwright."""
        if self._runtime:
            await self._runtime.close()
            self._runtime = None
