"""
HTTP response utilities for LJS search providers.

Provides common detection patterns for responses that aren't real search
results — Cloudflare challenges, rate limits, DNS failures, etc.
"""

from loguru import logger


def is_cloudflare_block(status_code: int, html: str) -> bool:
    """Check if an HTTP response is a Cloudflare anti-bot challenge page.

    Detects Cloudflare challenge pages by looking for known indicators
    in the response body. Only returns True for genuine Cloudflare blocks,
    not for rate limits (429) or network errors.

    Args:
        status_code: The HTTP status code.
        html: The response body text.

    Returns:
        True if the response appears to be a Cloudflare block.
    """
    if not html:
        return False

    html_lower = html.lower()

    # Cloudflare-specific markers in the response body
    cf_indicators = [
        "cf-browser-verification",
        "cf-challenge",
        "checking your browser",
        "just a moment",
        "avviso",
        "enable javascript",
    ]

    for indicator in cf_indicators:
        if indicator in html_lower:
            return True

    # Cloudflare error pages include "cloudflare" and a ray ID
    if "cloudflare" in html_lower and "ray id" in html_lower:
        return True

    # 403 with Cloudflare in the body is a block
    if status_code == 403 and "cloudflare" in html_lower:
        return True

    return False


def classify_error(provider_name: str, error: Exception, status_code: int | None = None,
                   html: str | None = None) -> str:
    """Classify a search error and log an appropriate message.

    Returns a category string: 'cloudflare', 'rate_limit', 'dns', or 'unknown'.
    Logs the error with actionable advice.
    """
    error_str = str(error).lower()

    # DNS resolution failure
    if "no address" in error_str or "name resolution" in error_str or "name or service not known" in error_str:
        logger.warning(
            f"[{provider_name}] DNS resolution failed — site may be down or blocked by your network. "
            f"Consider using Jackett (runs locally) or a VPN."
        )
        return "dns"

    # Rate limiting — not terminal, browser can often bypass this
    if status_code == 429 or "429" in error_str or "too many requests" in error_str:
        logger.info(
            f'[{provider_name}] Rate limited (429) via httpx — '
            f'browser may succeed where httpx is throttled.'
        )
        return "rate_limit"

    # Cloudflare block (detected from response body)
    if html and is_cloudflare_block(status_code or 0, html):
        logger.warning(
            f"[{provider_name}] Blocked by Cloudflare anti-bot protection. "
            f"Install Playwright (pip install playwright && playwright install chromium) "
            f"for browser-based bypass, or use Jackett."
        )
        return "cloudflare"

    # 403 without Cloudflare specifics
    if status_code == 403:
        logger.warning(
            f"[{provider_name}] Access forbidden (403). The site may require authentication "
            f"or be blocking automated requests. Consider using Jackett."
        )
        return "cloudflare"

    # Connection errors
    if "connection" in error_str or "timeout" in error_str:
        logger.warning(f"[{provider_name}] Connection error: {error}")
        return "dns"

    logger.error(f"[{provider_name}] Search failed: {error}")
    return "unknown"