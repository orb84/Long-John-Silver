"""
Web page reader for LJS.

Fetches web pages and extracts clean text content using httpx
and BeautifulSoup. Strips navigation, scripts, and boilerplate
to produce readable text for the AI agent to analyze.
"""

import httpx
from loguru import logger
from typing import Optional
from bs4 import BeautifulSoup


class WebReader:
    """Reads web pages and extracts clean, readable text content.

    Strips script tags, navigation, footers, and other boilerplate
    so the AI gets the actual content, not HTML noise.
    """

    # Tags to remove entirely (boilerplate, not content)
    STRIP_TAGS = {"script", "style", "nav", "footer", "header", "aside",
                  "iframe", "noscript", "form", "button", "input"}

    # Maximum characters to return (prevents overwhelming the LLM context)
    MAX_CONTENT_CHARS = 4000

    # Some public sites reject the default python/httpx client identity with
    # HTTP 403 even for ordinary public pages.  The reader is not a crawler; it
    # is an on-demand assistant tool, so it should present a normal readable
    # request identity and return typed failures that let the agent switch to
    # the browser tool when a site still refuses lightweight fetching.
    DEFAULT_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0 Safari/537.36 LJS-WebReader/1.0"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,it;q=0.8",
    }

    async def read_url(self, url: str) -> Optional[dict]:
        """Fetch a web page and extract its title and text content.

        Args:
            url: The URL to fetch.

        Returns:
            Dict with 'title', 'content', 'domain', or None on failure.
        """
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers=self.DEFAULT_HEADERS) as client:
                response = await client.get(url)
                response.raise_for_status()
                html = response.text
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            level = logger.warning if status_code in {401, 403, 429} else logger.error
            level(f"WebReader failed to fetch {url}: HTTP {status_code} {exc}")
            return {
                "ok": False,
                "error": f"HTTP {status_code} while reading page",
                "status": status_code,
                "url": url,
                "domain": self._domain_for_url(url),
                "recoverable": status_code in {401, 403, 429},
                "next_actions": [
                    {
                        "tool": "browse_page",
                        "reason": "The lightweight HTTP reader was refused; try the browser runtime for JavaScript/challenge/cookie handling.",
                        "args_hint": {"url": url},
                    }
                ] if status_code in {401, 403, 429} else [],
            }
        except Exception as e:
            logger.error(f"WebReader failed to fetch {url}: {e}")
            return {
                "ok": False,
                "error": str(e),
                "status": None,
                "url": url,
                "domain": self._domain_for_url(url),
                "recoverable": True,
                "next_actions": [
                    {
                        "tool": "browse_page",
                        "reason": "The lightweight reader failed; the browser runtime may still load the page.",
                        "args_hint": {"url": url},
                    }
                ],
            }

        result = self.extract_from_html(html, url)
        if result is None:
            return None

        return result

    def extract_from_html(self, html: str, url: str = "") -> Optional[dict]:
        """Extract clean text and links from already-fetched HTML.

        Strips boilerplate tags, extracts readable text, normalizes
        links, and truncates to MAX_CONTENT_CHARS.

        Args:
            html: The raw HTML content to extract from.
            url: The originating URL for link resolution.

        Returns:
            Dict with 'title', 'content', 'domain', 'url', 'links',
            or None on parse failure.
        """
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception as e:
            logger.error(f"WebReader failed to parse HTML: {e}")
            return None

        for tag in self.STRIP_TAGS:
            for element in soup.find_all(tag):
                element.decompose()

        title = soup.title.string.strip() if soup.title and soup.title.string else url

        body = soup.body or soup
        text = body.get_text(separator="\n", strip=True)

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        text = "\n".join(lines)

        if len(text) > self.MAX_CONTENT_CHARS:
            text = text[:self.MAX_CONTENT_CHARS] + "\n\n[Content truncated]"

        links = self._extract_links(soup, url)

        from urllib.parse import urlparse
        domain = urlparse(url).netloc if url else ""

        return {
            "title": title,
            "content": text,
            "domain": domain,
            "url": url,
            "links": links,
        }

    @staticmethod
    def _domain_for_url(url: str) -> str:
        """Return the host part of a URL for compact diagnostics."""
        from urllib.parse import urlparse

        return urlparse(url).netloc if url else ""

    def _extract_links(self, soup: BeautifulSoup, base_url: str = "") -> list[dict]:
        """Extract normalized links from parsed BeautifulSoup.

        Filters out javascript:, mailto:, and anchor-only links.
        Resolves relative URLs against the base URL.

        Args:
            soup: Parsed BeautifulSoup document.
            base_url: Base URL for resolving relative links.

        Returns:
            List of dicts with 'text', 'url', 'rel' keys.
        """
        from urllib.parse import urljoin

        links = []
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a.get("href", "").strip()
            if not href or href.startswith(("javascript:", "mailto:", "#")):
                continue
            text = a.get_text(strip=True)
            if not text or len(text) < 2:
                continue
            full_url = urljoin(base_url, href) if base_url else href
            if full_url in seen:
                continue
            seen.add(full_url)
            links.append({
                "text": text,
                "url": full_url,
                "rel": a.get("rel")[0] if isinstance(a.get("rel"), list) and a.get("rel") else None,
            })
        return links