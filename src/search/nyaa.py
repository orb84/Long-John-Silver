"""
Nyaa.si search provider for LJS.

Scrapes nyaa.si for anime torrent results. Uses httpx as a fast path
and falls back to an injected TorrentBrowserStrategy when httpx fails
for any reason (Cloudflare, rate limits, DNS errors, timeouts). Triggered
when the media parser detects anime naming patterns.
"""

import httpx
import urllib.parse
from loguru import logger
from bs4 import BeautifulSoup
from src.search.base import SearchProvider
from src.search.http_utils import is_cloudflare_block, classify_error
from src.core.models import SearchResult


class NyaaSearch(SearchProvider):
    """Coordinate the NyaaSearch responsibility in the LJS architecture.

    This class owns a focused slice of behavior and should be extended
    through injected collaborators or narrow override methods rather than
    cross-module state access.  Keep public methods stable because they are
    used by tests, tools, routers, or integration adapters.
    """
    timeout_seconds = 8
    """Searches nyaa.si for anime torrents.

    Nyaa is the primary anime torrent source. Falls back to a
    TorrentBrowserStrategy for any httpx failure.
    """

    BASE_URL = 'https://nyaa.si'

    @property
    def name(self) -> str:
        """Return the stable provider name used in diagnostics and UI labels.

        Keep this value short and unchanged unless migrating cached provider
        metadata and user-facing configuration at the same time.
        """
        return 'nyaa'

    async def search(self, query: str) -> list[SearchResult]:
        """Search nyaa.si for anime torrents.

        Tries httpx first (fast). Falls back to browser strategy for ANY
        failure including rate limits, Cloudflare, DNS errors, and timeouts.
        """
        url = f'{self.BASE_URL}/?f=0&q={urllib.parse.quote_plus(query)}&s=seeders&o=desc'
        logger.info(f'[{self.name}] Searching for: {query}')

        try:
            async with httpx.AsyncClient(timeout=7.0, follow_redirects=True, verify=False) as client:
                response = await client.get(url)

                if is_cloudflare_block(response.status_code, response.text):
                    return await self._browser_search(query)

                if response.status_code == 429:
                    logger.info(f'[{self.name}] httpx rate-limited (429), trying browser strategy')
                    return await self._browser_search(query)

                response.raise_for_status()
                return self._parse_results(response.text)
        except httpx.ConnectError as e:
            self.record_error_category(classify_error(self.name, e))
            return await self._browser_search(query)
        except Exception as e:
            self.record_error_category(classify_error(self.name, e))
            return await self._browser_search(query)

    async def _browser_search(self, query: str) -> list[SearchResult]:
        """Use the browser strategy to scrape Nyaa.

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
        """Check if nyaa.si is reachable."""
        try:
            async with httpx.AsyncClient(timeout=5.0, verify=False) as client:
                response = await client.get(self.BASE_URL)
                if is_cloudflare_block(response.status_code, response.text):
                    return self._browser is not None and self._browser.available
                return response.status_code == 200
        except Exception:
            return self._browser is not None and self._browser.available

    def _parse_results(self, html: str) -> list[SearchResult]:
        """Parse nyaa.si HTML table into SearchResult objects."""
        try:
            soup = BeautifulSoup(html, 'html.parser')
        except Exception as e:
            logger.error(f'[{self.name}] HTML parse failed: {e}')
            return []

        results = []
        table = soup.find('table', class_='torrent-list')
        if not table:
            return results

        rows = table.find_all('tr')
        for row in rows:
            try:
                cells = row.find_all('td')
                if len(cells) < 6:
                    continue

                link_cell = cells[1]
                title_link = link_cell.find('a', title=True)
                if not title_link:
                    continue

                title = title_link.get_text(strip=True)
                detail_path = title_link.get('href', '')
                if not title:
                    continue

                magnet = ''
                icon_links = cells[2].find_all('a') if len(cells) > 2 else []
                for link in icon_links:
                    href = link.get('href', '')
                    if href.startswith('magnet:'):
                        magnet = href
                        break

                size = cells[3].get_text(strip=True) if len(cells) > 3 else 'Unknown'
                seeders = None
                if len(cells) > 5:
                    try:
                        seeders = int(cells[5].get_text(strip=True).replace(',', ''))
                    except ValueError:
                        pass

                results.append(SearchResult(
                    title=title,
                    magnet=magnet if magnet else None,
                    size=size,
                    seeders=seeders,
                    source=self.name,
                    url=f'{self.BASE_URL}{detail_path}' if detail_path else None,
                ))

            except Exception as e:
                logger.debug(f'[{self.name}] Row parse error: {e}')
                continue

        logger.info(f'[{self.name}] returned {len(results)} results')
        return results
