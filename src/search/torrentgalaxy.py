"""
TorrentGalaxy search provider for LJS.

Scrapes torrentgalaxy.to for general torrent results. Uses httpx as a
fast path and falls back to an injected TorrentBrowserStrategy when httpx
fails for any reason (Cloudflare, rate limits, DNS errors, timeouts).
"""

import urllib.parse
import httpx
from loguru import logger
from bs4 import BeautifulSoup
from src.search.base import SearchProvider
from src.search.http_utils import is_cloudflare_block, classify_error
from src.core.models import SearchResult


class TorrentGalaxySearch(SearchProvider):
    """Coordinate the TorrentGalaxySearch responsibility in the LJS architecture.

    This class owns a focused slice of behavior and should be extended
    through injected collaborators or narrow override methods rather than
    cross-module state access.  Keep public methods stable because they are
    used by tests, tools, routers, or integration adapters.
    """
    timeout_seconds = 8
    """Searches torrentgalaxy.to for torrents.

    TorrentGalaxy is a general-purpose public tracker with good coverage
    of movies and TV shows. Falls back to a TorrentBrowserStrategy for
    any httpx failure (429, Cloudflare, DNS, timeout).
    """

    BASE_URL = 'https://torrentgalaxy.to'

    @property
    def name(self) -> str:
        """Return the stable provider name used in diagnostics and UI labels.

        Keep this value short and unchanged unless migrating cached provider
        metadata and user-facing configuration at the same time.
        """
        return 'TorrentGalaxy'

    async def search(self, query: str) -> list[SearchResult]:
        """Search TorrentGalaxy for torrents matching the query.

        Tries httpx first (fast). Falls back to browser strategy for ANY
        failure including rate limits, Cloudflare, DNS errors, and timeouts.
        """
        params = {'q': query}
        url = f'{self.BASE_URL}/torrents.php?' + urllib.parse.urlencode(params)
        headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            )
        }
        logger.info(f'[{self.name}] Searching for: {query}')

        try:
            async with httpx.AsyncClient(timeout=7.0, follow_redirects=True, verify=False) as client:
                response = await client.get(url, headers=headers)

                if is_cloudflare_block(response.status_code, response.text):
                    return await self._browser_search(query)

                if response.status_code == 429:
                    logger.info(f'[{self.name}] httpx rate-limited (429), trying browser strategy')
                    return await self._browser_search(query)

                response.raise_for_status()
                results = self._parse_results(response.text)
                logger.info(f'[{self.name}] Found {len(results)} results')
                return results
        except httpx.ConnectError as e:
            self.record_error_category(classify_error(self.name, e))
            return await self._browser_search(query)
        except Exception as e:
            status = getattr(e.response, 'status_code', None) if hasattr(e, 'response') else None
            self.record_error_category(classify_error(self.name, e, status))
            return await self._browser_search(query)

    async def _browser_search(self, query: str) -> list[SearchResult]:
        """Use the browser strategy to scrape TorrentGalaxy.

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
                url=c.detail_url,
            ))
        return results

    async def health_check(self) -> bool:
        """Check if TorrentGalaxy is reachable."""
        try:
            async with httpx.AsyncClient(timeout=5.0, verify=False) as client:
                response = await client.get(self.BASE_URL)
                if is_cloudflare_block(response.status_code, response.text):
                    return self._browser is not None and self._browser.available
                return response.status_code == 200
        except Exception:
            return self._browser is not None and self._browser.available

    def _parse_results(self, html: str) -> list[SearchResult]:
        """Parse TorrentGalaxy HTML into SearchResult objects."""
        try:
            soup = BeautifulSoup(html, 'html.parser')
        except Exception as e:
            logger.error(f'[{self.name}] HTML parse failed: {e}')
            return []

        results = []
        rows = soup.find_all('div', class_='tgxtable')
        if not rows:
            rows = soup.find_all('div', class_='tgxtablerow')

        for row in rows:
            try:
                title_link = row.find('a', href=lambda h: h and '/torrent/' in str(h))
                if not title_link:
                    continue
                title = title_link.get_text(strip=True)
                if not title:
                    continue

                magnet = None
                magnet_link = row.find('a', href=lambda h: h and str(h).startswith('magnet:'))
                if magnet_link:
                    magnet = magnet_link['href']

                size = 'Unknown'
                size_span = row.find('span', class_='badge-secondary')
                if size_span:
                    size = size_span.get_text(strip=True)

                seeders = None
                seeder_elem = row.find('span', class_='badge-success')
                if seeder_elem:
                    try:
                        seeders = int(seeder_elem.get_text(strip=True).replace(',', ''))
                    except ValueError:
                        pass

                results.append(SearchResult(
                    title=title,
                    magnet=magnet,
                    size=size,
                    seeders=seeders,
                    source=self.name,
                    url=(self.BASE_URL + title_link['href']) if title_link.get('href') else None,
                ))
            except Exception as e:
                logger.debug(f'[{self.name}] Row parse error: {e}')
                continue

        return results
