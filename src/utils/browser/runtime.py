"""
Browser runtime for LJS.

Shared Playwright runtime for scraping and agent browsing. Uses
Firefox as the primary engine (Cloudflare-resistant — no navigator.webdriver),
with Chromium as a fallback when Firefox is unavailable. Applies
stealth evasions, enforces domain throttling, waits for CSS selectors,
and retries Cloudflare challenges.
"""

import asyncio
import time
import hashlib
import random
import sys
from loguru import logger

from src.core.security.path_policy import SafePathResolver
from pathlib import Path
from typing import Optional, Literal

from src.core.models import (
    BrowserHealth,
    BrowserFetchRequest,
    BrowserFetchResult,
)
from src.utils.browser.domain_policy import BrowserDomainPolicy
from src.utils.browser.challenge_detector import ChallengeDetector

try:
    from playwright.async_api import async_playwright, Browser as PWBrowser, BrowserContext, Page
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False


Engine = Literal["firefox", "chromium"]

DEFAULT_TIMEOUT_SECONDS = 45
ARTIFACTS_DIR = Path("data/browser_artifacts")
MAX_ARTIFACT_DIR_SIZE = 250 * 1024 * 1024
ARTIFACT_TTL_DAYS = 7

SELECTOR_TIMEOUT_MS = 20000
CLOUDFLARE_RETRY_COUNT = 5
CLOUDFLARE_WAIT_SECONDS = 10

_USER_AGENTS = {
    "firefox": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:136.0) Gecko/20100101 Firefox/136.0",
    "chromium": "Mozilla/5.0 (iPad; CPU OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
}

_VIEWPORTS = [
    {"width": 1024, "height": 768},
    {"width": 1280, "height": 720},
    {"width": 1920, "height": 1080},
]


def _random_viewport() -> dict:
    return random.choice(_VIEWPORTS)


class BrowserRuntime:
    """Shared Playwright runtime with Firefox-first engine selection.

    Firefox headless doesn't expose navigator.webdriver, making it
    far less detectable by Cloudflare than Chromium-based engines.
    Falls back to Chromium when Firefox is not installed.

    Applies stealth evasions at the context level (before any page
    loads), enforces domain throttling, actually waits for requested
    CSS selectors, and retries Cloudflare challenges.
    """

    def __init__(
        self,
        domain_policy: BrowserDomainPolicy,
        challenge_detector: ChallengeDetector,
        max_pages: int = 3,
        preferred_engine: Engine | None = None,
    ):
        self._domain_policy = domain_policy
        self._challenge_detector = challenge_detector
        self._max_pages = max_pages
        self._playwright = None
        self._browser: Optional[PWBrowser] = None
        self._context: Optional[BrowserContext] = None
        self._lock = asyncio.Lock()
        self._health_cache: Optional[BrowserHealth] = None
        self._engine: Engine = preferred_engine or "firefox"

    async def health_check(self) -> BrowserHealth:
        """Run the public lifecycle operation health_check.

        This method is part of runtime orchestration.  Keep it idempotent
        where practical, clean up owned resources, and delegate specialized
        work to collaborators to preserve testability.
        """
        if not _PLAYWRIGHT_AVAILABLE:
            return BrowserHealth(
                package_installed=False,
                last_error="Playwright Python package not installed",
            )
        if self._health_cache and self._health_cache.launch_ok:
            return self._health_cache

        health = BrowserHealth(package_installed=True)
        try:
            self._playwright = await async_playwright().start()
        except Exception as e:
            health.last_error = f"Failed to start Playwright: {e}"
            self._health_cache = health
            return health

        engine = await self._launch_best_engine(self._engine)
        if not engine:
            health.last_error = "No browser engine available — install playwright firefox or playwright chromium"
            self._health_cache = health
            return health

        health.browser_installed = True
        health.launch_ok = True

        try:
            self._context = await self._browser.new_context(
                user_agent=_USER_AGENTS.get(self._engine, _USER_AGENTS["chromium"]),
                viewport=_random_viewport(),
                locale="en-US",
                timezone_id="America/New_York",
                ignore_https_errors=True,
            )
            if self._engine == "chromium":
                await self._apply_stealth_chromium(self._context)
            page = await self._context.new_page()
            await page.goto("about:blank", wait_until="domcontentloaded")
            health.navigation_ok = True
            await page.close()
        except Exception as e:
            health.last_error = f"Navigation test failed: {e}"

        self._health_cache = health
        return health

    @staticmethod
    def _detect_system_chrome() -> bool:
        """Check if Google Chrome is installed on this system."""
        if sys.platform == "darwin":
            return Path("/Applications/Google Chrome.app").exists()
        if sys.platform == "win32":
            return bool(
                Path("C:/Program Files/Google/Chrome/Application/chrome.exe").exists()
                or Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe").exists()
            )
        return bool(
            Path("/usr/bin/google-chrome").exists()
            or Path("/usr/bin/chromium-browser").exists()
            or Path("/usr/bin/chromium").exists()
        )

    @staticmethod
    async def _resolve_doh_host_rules() -> str | None:
        """Resolve torrent site domains via DNS-over-HTTPS to bypass ISP DNS blocks.

        Returns a Chrome --host-resolver-rules string mapping each domain to
        the first IPv4 address returned by Cloudflare DoH, or None on failure.
        """
        try:
            import httpx
            domains = ["btdig.com", "1337x.to", "torrentgalaxy.to", "nyaa.si"]
            mappings = []
            async with httpx.AsyncClient(timeout=5) as client:
                for domain in domains:
                    try:
                        r = await client.get(
                            "https://cloudflare-dns.com/dns-query",
                            params={"name": domain, "type": "A"},
                            headers={"accept": "application/dns-json"},
                        )
                        data = r.json()
                        ips = [a["data"] for a in data.get("Answer", []) if a.get("type") == 1]
                        if ips:
                            mappings.append(f"MAP {domain} {ips[0]}")
                    except Exception:
                        continue
            if mappings:
                return ",".join(mappings)
        except Exception:
            pass
        return None

    async def _launch_best_engine(self, preferred: Engine | None = None) -> bool:
        """Try the preferred engine first, then the other, then give up.

        On macOS/Windows, Playwright's bundled Chromium may be sandbox-restricted
        from making network connections. System Chrome (if installed) avoids this.

        Resolves torrent site domains via DNS-over-HTTPS and injects IP mappings
        via --host-resolver-rules to bypass ISP-level DNS blocking.

        Args:
            preferred: If set, try this engine first. Falls back to the other.

        Returns True if a browser was successfully launched.
        """
        has_system_chrome = self._detect_system_chrome()

        # Resolve torrent domains via DoH to bypass ISP DNS blocks
        host_rules = await self._resolve_doh_host_rules()
        if host_rules:
            logger.debug(f"DNS-over-HTTPS host rules: {host_rules}")

        chromium_args = [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-blink-features=AutomationControlled",
            "--ignore-certificate-errors",
            "--allow-running-insecure-content",
            "--enable-features=NetworkService,NetworkServiceInProcess",
        ]
        if host_rules:
            chromium_args.append(f"--host-resolver-rules={host_rules}")

        # If system Chrome is available, use it first — bundled Chromium
        # often gets sandbox-blocked on macOS/Windows
        if has_system_chrome:
            try:
                self._browser = await self._playwright.chromium.launch(
                    headless=True, channel="chrome", args=chromium_args,
                )
                self._engine = "chromium"
                logger.info("Browser engine: system Chrome")
                return True
            except Exception:
                pass

        # Try firefox then bundled chromium
        engines = (preferred,) if preferred else ("firefox", "chromium")
        if preferred:
            fallback = "chromium" if preferred == "firefox" else "firefox"
            engines = (preferred, fallback)
        else:
            engines = ("firefox", "chromium")

        for engine in engines:
            try:
                browser_type = (
                    self._playwright.firefox if engine == "firefox"
                    else self._playwright.chromium
                )
                self._browser = await browser_type.launch(
                    headless=True,
                    args=chromium_args if engine == "chromium" else [],
                )
                self._engine = engine
                logger.info(f"Browser engine: {engine}")
                return True
            except Exception:
                continue

        return False

    async def _apply_stealth_chromium(self, context: BrowserContext) -> None:
        """Apply anti-detection evasions to a Chromium context.

        Only needed for Chromium — Firefox headless doesn't expose
        navigator.webdriver at all.
        """
        try:
            from playwright_stealth import Stealth
            await Stealth().apply_stealth_async(context)
            logger.debug("Chromium stealth evasions applied")
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"Stealth apply failed (non-fatal): {e}")

    async def _ensure_browser(self) -> bool:
        if not _PLAYWRIGHT_AVAILABLE:
            return False
        if self._browser and self._browser.is_connected():
            return True
        async with self._lock:
            if self._browser and self._browser.is_connected():
                return True
            health = await self.health_check()
            return health.launch_ok

    async def fetch(self, request: BrowserFetchRequest) -> BrowserFetchResult:
        """Fetch or extract provider data for BrowserRuntime.

        Keep network and parsing errors contained so the caller can continue
        with fallback providers.  Extend by adding parser helpers instead of
        changing the public return contract.
        """
        start_time = time.monotonic()

        if not await self._ensure_browser():
            elapsed = int((time.monotonic() - start_time) * 1000)
            return BrowserFetchResult(
                ok=False, url=request.url, final_url=request.url, status=0,
                error="Playwright not available", blocked_reason="playwright_unavailable",
                elapsed_ms=elapsed,
            )

        domain_result = await self._domain_policy.acquire(request.url)
        expected_domain = self._domain_policy.domain_for_url(request.url)
        if domain_result != expected_domain:
            elapsed = int((time.monotonic() - start_time) * 1000)
            return BrowserFetchResult(
                ok=False, url=request.url, final_url=request.url, status=0,
                blocked_reason=domain_result, elapsed_ms=elapsed,
            )

        domain = domain_result
        page: Optional[Page] = None

        try:
            page = await self._context.new_page()
            response = await page.goto(
                request.url,
                wait_until="domcontentloaded",
                timeout=DEFAULT_TIMEOUT_SECONDS * 1000,
            )

            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass

            selector_found = True
            if request.wait_for_selector:
                try:
                    await page.wait_for_selector(
                        request.wait_for_selector,
                        timeout=SELECTOR_TIMEOUT_MS,
                    )
                except Exception:
                    selector_found = False

            await asyncio.sleep(request.wait_seconds)

            title = await page.title()
            body_text = await page.evaluate("() => document.body?.innerText || ''")
            status = response.status if response else 0

            cf_result = await self._handle_cloudflare_with_retry(page, title, body_text, request)
            if cf_result:
                return cf_result

            # After Cloudflare retry (possibly resolved via reload), re-read
            # the page state — title and body_text may have changed
            title = await page.title()
            body_text = await page.evaluate("() => document.body?.innerText || ''")
            html = await page.content()
            final_url = page.url

            if len(body_text) > request.max_content_chars:
                body_text = body_text[:request.max_content_chars] + "\n\n[Content truncated]"

            challenge_detection = self._challenge_detector.inspect(
                BrowserFetchResult(ok=True, url=request.url, final_url=final_url,
                                    status=status, title=title, text=body_text)
            )

            elapsed = int((time.monotonic() - start_time) * 1000)

            fetch_ok = (
                not challenge_detection.is_challenge
                and selector_found
                and status < 400
            )

            blocked_reason = None
            if not fetch_ok:
                if challenge_detection.is_challenge:
                    blocked_reason = challenge_detection.challenge_type
                elif not selector_found:
                    blocked_reason = "selector_not_found"
                elif status >= 400:
                    blocked_reason = f"http_{status}"

            result = BrowserFetchResult(
                ok=fetch_ok,
                url=request.url,
                final_url=final_url,
                status=status,
                title=title or request.url,
                text=body_text,
                html=html,
                challenge_detected=challenge_detection.is_challenge,
                captcha_detected=challenge_detection.challenge_type == "captcha",
                selector_found=selector_found,
                blocked_reason=blocked_reason,
                elapsed_ms=elapsed,
            )

            if challenge_detection.is_challenge:
                self._domain_policy.record_challenge(domain)
            else:
                self._domain_policy.record_success(domain)

            return result

        except Exception as e:
            self._domain_policy.record_failure(domain)
            elapsed = int((time.monotonic() - start_time) * 1000)
            screenshot_path = None
            if request.screenshot_on_failure and page and not page.is_closed():
                screenshot_path = await self._save_screenshot(page, request.url, str(e))
            logger.error(f"BrowserRuntime.fetch failed for {request.url}: {e}")
            return BrowserFetchResult(
                ok=False,
                url=request.url,
                final_url=request.url if not page or page.is_closed() else page.url,
                status=response.status if "response" in dir() and response else 0,
                error=str(e),
                blocked_reason="fetch_error",
                elapsed_ms=elapsed,
                screenshot_path=screenshot_path,
            )
        finally:
            if page and not page.is_closed():
                await page.close()
            self._domain_policy.release(domain)

    async def _handle_cloudflare_with_retry(
        self, page: Page, title: str, body_text: str, request: BrowserFetchRequest,
    ) -> Optional[BrowserFetchResult]:
        cf_indicators = ["checking your browser", "just a moment", "cloudflare"]
        combined = (title + " " + body_text).lower()
        if not any(ind in combined for ind in cf_indicators):
            return None

        for attempt in range(CLOUDFLARE_RETRY_COUNT):
            logger.info(
                f"Cloudflare challenge on {request.url} — "
                f"attempt {attempt + 1}/{CLOUDFLARE_RETRY_COUNT}, waiting {CLOUDFLARE_WAIT_SECONDS}s..."
            )
            await asyncio.sleep(CLOUDFLARE_WAIT_SECONDS)

            body_text = await page.evaluate("() => document.body?.innerText || ''")
            title = await page.title()
            combined = (title + " " + body_text).lower()

            if not any(ind in combined for ind in cf_indicators):
                logger.info(f"Cloudflare challenge resolved for {request.url}")
                if request.wait_for_selector:
                    try:
                        await page.wait_for_selector(request.wait_for_selector, timeout=10000)
                    except Exception:
                        pass
                return None

            if request.wait_for_selector:
                try:
                    await page.wait_for_selector(request.wait_for_selector, timeout=5000)
                    logger.info(f"Selector appeared during Cloudflare retry on {request.url}")
                    return None
                except Exception:
                    pass

        logger.warning(f"Cloudflare challenge persisted on {request.url} after {CLOUDFLARE_RETRY_COUNT} waits")
        html = await page.content()
        return BrowserFetchResult(
            ok=False, url=request.url, final_url=page.url, status=200,
            title=title, text=body_text, html=html,
            challenge_detected=True, blocked_reason="cloudflare", elapsed_ms=0,
        )

    async def _save_screenshot(self, page: Page, url: str, error: str) -> str | None:
        try:
            ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
            url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
            filename = f"{int(time.time())}_{url_hash}.png"
            path = ARTIFACTS_DIR / filename
            await page.screenshot(path=str(path), full_page=False)
            self._enforce_artifact_limits()
            return str(path)
        except Exception as e:
            logger.debug(f"Screenshot save failed: {e}")
            return None

    def _enforce_artifact_limits(self) -> None:
        try:
            artifacts = sorted(ARTIFACTS_DIR.glob("*.png"), key=lambda f: f.stat().st_mtime)
            cutoff = time.time() - (ARTIFACT_TTL_DAYS * 86400)
            total_size = 0
            surviving = []
            for a in artifacts:
                try:
                    s = a.stat()
                    if s.st_mtime < cutoff:
                        SafePathResolver.for_application(extra_roots=[ARTIFACTS_DIR]).safe_unlink(
                            a, purpose="browser.artifact_expire", move_to_trash=False,
                        )
                        continue
                    total_size += s.st_size
                    surviving.append(a)
                except FileNotFoundError:
                    continue
            while total_size > MAX_ARTIFACT_DIR_SIZE and surviving:
                oldest = surviving.pop(0)
                try:
                    total_size -= oldest.stat().st_size
                    SafePathResolver.for_application(extra_roots=[ARTIFACTS_DIR]).safe_unlink(
                        oldest, purpose="browser.artifact_prune", move_to_trash=False,
                    )
                except FileNotFoundError:
                    pass
        except Exception:
            pass

    async def close(self) -> None:
        """Run the public lifecycle operation close.

        This method is part of runtime orchestration.  Keep it idempotent
        where practical, clean up owned resources, and delegate specialized
        work to collaborators to preserve testability.
        """
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
        self._browser = None
        self._context = None
        self._playwright = None
        logger.info("Playwright browser runtime closed")
