"""
Domain-level throttling and cooldowns for LJS browser automation.

Prevents browser retry storms by enforcing per-domain concurrency
limits, global semaphore, and cooldowns after repeated failures.
Composes src.utils.circuit_breaker.CircuitBreaker for each domain.
"""

import asyncio
import time
from urllib.parse import urlparse
from loguru import logger
from src.utils.circuit_breaker import CircuitBreaker


DEFAULT_GLOBAL_MAX_PAGES = 3
DEFAULT_DOMAIN_MAX_PAGES = 1
DEFAULT_CHALLENGE_COOLDOWN_SECONDS = 60
DEFAULT_REPEATED_FAILURE_COOLDOWN_SECONDS = 300


class BrowserDomainPolicy:
    """Rate limits and cooldowns browser work by domain.

    Enforces a global semaphore for total concurrent Playwright pages
    and per-domain semaphores to avoid hammering a single site. Tracks
    cooldown timestamps and composes CircuitBreaker instances per domain
    for repeated challenge or timeout failures.
    """

    def __init__(
        self,
        global_max_pages: int = DEFAULT_GLOBAL_MAX_PAGES,
        domain_max_pages: int = DEFAULT_DOMAIN_MAX_PAGES,
        challenge_cooldown_seconds: int = DEFAULT_CHALLENGE_COOLDOWN_SECONDS,
        repeated_cooldown_seconds: int = DEFAULT_REPEATED_FAILURE_COOLDOWN_SECONDS,
    ):
        """Initialize domain policy with configurable limits.

        Args:
            global_max_pages: Maximum total concurrent Playwright pages.
            domain_max_pages: Maximum concurrent pages per domain.
            challenge_cooldown_seconds: Cooldown after a single challenge failure.
            repeated_cooldown_seconds: Cooldown after repeated failures per domain.
        """
        self._global_sem = asyncio.Semaphore(global_max_pages)
        self._domain_sems: dict[str, asyncio.Semaphore] = {}
        self._domain_max_pages = domain_max_pages
        self._challenge_cooldown_seconds = challenge_cooldown_seconds
        self._repeated_cooldown_seconds = repeated_cooldown_seconds
        self._domain_cooldowns: dict[str, float] = {}
        self._domain_failure_counts: dict[str, int] = {}
        self._domain_last_failures: dict[str, float] = {}

    def _extract_domain(self, url: str) -> str:
        """Extract the domain from a URL for throttling purposes.

        Args:
            url: The full URL to extract the domain from.

        Returns:
            Lowercase domain string (e.g., '1337x.to').
        """
        return urlparse(url).netloc.lower()

    def domain_for_url(self, url: str) -> str:
        """Return the normalized domain used for throttling a URL."""
        return self._extract_domain(url)

    def _domain_semaphore(self, domain: str) -> asyncio.Semaphore:
        """Get or create the per-domain semaphore.

        Args:
            domain: The domain to get a semaphore for.

        Returns:
            asyncio.Semaphore for the given domain.
        """
        if domain not in self._domain_sems:
            self._domain_sems[domain] = asyncio.Semaphore(self._domain_max_pages)
        return self._domain_sems[domain]

    def is_in_cooldown(self, domain: str) -> bool:
        """Check if a domain is currently in a cooldown period.

        Args:
            domain: The domain to check.

        Returns:
            True if the domain is in cooldown and should not be accessed.
        """
        if domain not in self._domain_cooldowns:
            return False
        return time.monotonic() < self._domain_cooldowns[domain]

    def record_challenge(self, domain: str) -> None:
        """Record a challenge/block and place the domain in cooldown.

        Args:
            domain: The domain that returned a challenge.
        """
        self._domain_cooldowns[domain] = time.monotonic() + self._challenge_cooldown_seconds
        logger.info(f"Domain {domain} placed in cooldown for {self._challenge_cooldown_seconds}s")

    def record_failure(self, domain: str) -> None:
        """Record a browser fetch failure and increment cooldown tracking.

        Escalates cooldown duration on repeated failures.

        Args:
            domain: The domain that failed.
        """
        self._domain_failure_counts[domain] = self._domain_failure_counts.get(domain, 0) + 1
        count = self._domain_failure_counts[domain]
        self._domain_last_failures[domain] = time.monotonic()
        cooldown = min(
            self._challenge_cooldown_seconds * (2 ** (count - 1)),
            self._repeated_cooldown_seconds,
        )
        self._domain_cooldowns[domain] = time.monotonic() + cooldown
        logger.warning(f"Domain {domain} failure #{count}, cooldown for {cooldown}s")

    def record_success(self, domain: str) -> None:
        """Record a successful browser fetch, resetting failure tracking.

        Args:
            domain: The domain that succeeded.
        """
        if domain in self._domain_failure_counts:
            self._domain_failure_counts[domain] = 0

    async def acquire(self, url: str) -> str | None:
        """Attempt to acquire permission to fetch a URL.

        Acquires the global semaphore, the per-domain semaphore, and
        checks cooldown status. Returns the domain on success, or a
        blocked reason string on failure.

        Args:
            url: The URL to acquire permission for.

        Returns:
            The domain string on success, or a blocked reason string on failure.
        """
        domain = self._extract_domain(url)

        if self.is_in_cooldown(domain):
            return "cooldown"

        await self._global_sem.acquire()
        await self._domain_semaphore(domain).acquire()

        return domain

    def release(self, domain: str) -> None:
        """Release the acquired permissions for a domain.

        Args:
            domain: The domain to release semaphores for.
        """
        if domain in self._domain_sems:
            self._domain_sems[domain].release()
        self._global_sem.release()
