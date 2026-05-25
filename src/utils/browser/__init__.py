"""
Browser automation package for LJS.

Provides Playwright-based browser runtime, challenge detection,
domain-level throttling, structured fetch results, and the
backwards-compatible Browser wrapper for both torrent scraping
and agentic web research.
"""

from src.utils.browser.runtime import BrowserRuntime
from src.utils.browser.challenge_detector import ChallengeDetector
from src.utils.browser.domain_policy import BrowserDomainPolicy
from src.utils.browser.browser_wrapper import Browser

try:
    import playwright  # noqa: F401
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False

__all__ = [
    "BrowserRuntime",
    "ChallengeDetector",
    "BrowserDomainPolicy",
    "Browser",
    "_PLAYWRIGHT_AVAILABLE",
]
