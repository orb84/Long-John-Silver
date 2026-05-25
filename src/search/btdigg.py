"""
BTDigg search provider for LJS.

Scrapes btdig.com to find torrent magnet links. Uses httpx as a fast
path and falls back to an injected TorrentBrowserStrategy when httpx
fails for any reason (Cloudflare, rate limits, DNS errors, timeouts).
"""

import urllib.parse
import httpx
from loguru import logger
from bs4 import BeautifulSoup
from src.search.base import SearchProvider
from src.search.http_utils import is_cloudflare_block, classify_error
from src.core.models import SearchResult


class BTDiggSearch(SearchProvider):
    """Coordinate the BTDiggSearch responsibility in the LJS architecture.

    This class owns a focused slice of behavior and should be extended
    through injected collaborators or narrow override methods rather than
    cross-module state access.  Keep public methods stable because they are
    used by tests, tools, routers, or integration adapters.
    """
    timeout_seconds = 8
    """Scrapes BTDigg for torrent search results.

    Uses httpx first (fast). Falls back to a TorrentBrowserStrategy for
    any failure — 429 rate limits, Cloudflare challenges, DNS errors, and
    connection timeouts are all retried through the strategy.
    """

    BASE_URL = 'https://btdig.com'

    def __init__(self):
        super().__init__()
        self._headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            )
        }

    @property
    def name(self) -> str:
        """Return the stable provider name used in diagnostics and UI labels.

        Keep this value short and unchanged unless migrating cached provider
        metadata and user-facing configuration at the same time.
        """
        return 'BTDigg'

    async def search(self, query: str) -> list[SearchResult]:
        """Search BTDigg for torrents matching the query.

        Tries httpx first (fast). Falls back to browser strategy for ANY
        failure including 429 rate limits, Cloudflare blocks, DNS errors,
        and timeouts.
        """
        url = f'{self.BASE_URL}/search?q={urllib.parse.quote_plus(query)}'
        logger.info(f'[{self.name}] Searching for: {query}')

        try:
            async with httpx.AsyncClient(timeout=7.0, verify=False) as client:
                response = await client.get(url, headers=self._headers, follow_redirects=True)

                if is_cloudflare_block(response.status_code, response.text):
                    return await self._browser_search(query)

                if response.status_code == 429:
                    logger.info(f'[{self.name}] httpx rate-limited (429), trying browser strategy')
                    return await self._browser_search(query)

                response.raise_for_status()
                results = self._parse_results(response.text)
                logger.info(f'[{self.name}] Found {len(results)} results.')
                return results
        except httpx.ConnectError as e:
            self.record_error_category(classify_error(self.name, e))
            return await self._browser_search(query)
        except httpx.HTTPError as e:
            status = getattr(e.response, 'status_code', None) if hasattr(e, 'response') else None
            self.record_error_category(classify_error(self.name, e, status))
            return await self._browser_search(query)

    async def _browser_search(self, query: str) -> list[SearchResult]:
        """Use the browser strategy to scrape BTDigg.

        Args:
            query: The search query.

        Returns:
            List of SearchResult objects.
        """
        if self._browser_strategy:
            scrape_result = await self._browser_strategy.search(query)
            if scrape_result.ok and scrape_result.candidates:
                return self._convert_candidates(scrape_result.candidates)
            logger.warning(
                f'[{self.name}] Browser strategy failed: '
                f'{scrape_result.error or scrape_result.blocked_reason}'
            )
            return []
        return []

    def _convert_candidates(self, candidates) -> list[SearchResult]:
        """Convert TorrentScrapeCandidate objects to SearchResult objects."""
        results = []
        for c in candidates:
            results.append(SearchResult(
                title=c.title,
                magnet=c.magnet,
                size=c.size,
                seeders=c.seeders,
                source=self.name,
            ))
        return results

    async def health_check(self) -> bool:
        """Check if BTDigg is reachable."""
        try:
            async with httpx.AsyncClient(timeout=5.0, verify=False) as client:
                response = await client.get(self.BASE_URL, headers=self._headers)
                if is_cloudflare_block(response.status_code, response.text):
                    return self._browser is not None and self._browser.available
                return response.status_code == 200
        except Exception:
            return self._browser is not None and self._browser.available

    def _parse_results(self, html: str) -> list[SearchResult]:
        """Parse BTDigg HTML into SearchResult objects."""
        soup = BeautifulSoup(html, 'html.parser')
        results = []

        items = soup.find_all('div', class_='one_result')
        for item in items:
            try:
                title_elem = item.find('div', class_='attr_name')
                if title_elem:
                    title_elem = title_elem.find('a')
                if not title_elem:
                    continue
                title = title_elem.text.strip()

                magnet_elem = item.find('a', href=lambda x: x and x.startswith('magnet:'))
                magnet = magnet_elem['href'] if magnet_elem else None

                size_elem = item.find('span', class_='attr_val')
                size = size_elem.text.strip() if size_elem else 'Unknown'

                if title and magnet:
                    results.append(SearchResult(
                        title=title,
                        magnet=magnet,
                        size=size,
                        source=self.name,
                    ))
            except Exception as e:
                logger.debug(f'[{self.name}] Error parsing result: {e}')
                continue

        return results
