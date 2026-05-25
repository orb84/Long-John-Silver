"""URL normalization helpers for web-search results and reader tools."""

from __future__ import annotations

from html import unescape
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

_DUCKDUCKGO_HOSTS = {
    "duckduckgo.com",
    "www.duckduckgo.com",
    "html.duckduckgo.com",
}


def normalize_search_result_url(value: Any, *, base_url: str = "https://duckduckgo.com") -> str | None:
    """Return a direct http(s) URL from a provider result URL.

    DuckDuckGo's HTML result links are commonly redirect wrappers such as
    ``//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com`` or relative
    ``/l/?uddg=...`` links.  The agent should read the destination page, not
    feed a protocol-relative DuckDuckGo redirect into ``read_web_page``.

    Args:
        value: Candidate URL value from a search provider/tool payload.
        base_url: Base URL used to resolve protocol/relative provider links.

    Returns:
        A normalized direct http(s) URL, or None when the value is not a usable
        public web URL.
    """
    if not isinstance(value, str):
        return None

    raw = unescape(value).strip()
    if not raw or raw.startswith(("javascript:", "mailto:", "#")):
        return None

    if raw.startswith("//"):
        raw = "https:" + raw
    elif raw.startswith("/"):
        raw = urljoin(base_url, raw)

    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        return None

    host = parsed.netloc.lower()
    if host in _DUCKDUCKGO_HOSTS and parsed.path.startswith("/l/"):
        query = parse_qs(parsed.query)
        target = (query.get("uddg") or [None])[0]
        normalized_target = normalize_search_result_url(target, base_url=base_url)
        if normalized_target:
            return normalized_target

    return raw


def is_http_url(value: Any) -> bool:
    """Return whether ``value`` is already a usable http(s) URL."""
    return isinstance(value, str) and value.startswith(("http://", "https://"))
