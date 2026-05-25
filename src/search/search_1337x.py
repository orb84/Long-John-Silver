"""
1337x search provider for LJS.

Scrapes 1337x.to for general torrent results. Uses httpx as a fast
path and falls back to an injected TorrentBrowserStrategy when httpx
fails for any reason (Cloudflare, rate limits, DNS errors, timeouts).
The browser strategy fetches detail pages to extract magnet links.
"""

import urllib.parse
import httpx
from loguru import logger
from bs4 import BeautifulSoup
from src.search.base import SearchProvider
from src.search.http_utils import is_cloudflare_block, classify_error
from src.core.models import SearchResult


MAX_DETAIL_FETCHES = 10


class Search1337x(SearchProvider):
    """Coordinate the Search1337x responsibility in the LJS architecture.

    This class owns a focused slice of behavior and should be extended
    through injected collaborators or narrow override methods rather than
    cross-module state access.  Keep public methods stable because they are
    used by tests, tools, routers, or integration adapters.
    """
    timeout_seconds = 8
    """Searches 1337x.to for torrents.

    1337x is a popular general-purpose public tracker. The search page
    lists titles, seeders, and sizes but not magnet links — we follow
    detail pages to extract magnets. Falls back to an injected
    TorrentBrowserStrategy for any httpx failure.
    """

    BASE_URL = 'https://1337x.to'

    def __init__(self):
        super().__init__()
        self._browser_strategy = None

    @property
    def name(self) -> str:
        """Return the stable provider name used in diagnostics and UI labels.

        Keep this value short and unchanged unless migrating cached provider
        metadata and user-facing configuration at the same time.
        """
        return '1337x'

    def set_browser_strategy(self, strategy: "TorrentBrowserStrategy | None") -> None:
        """Inject a TorrentBrowserStrategy for browser-based scraping.

        Args:
            strategy: A TorrentBrowserStrategy instance (e.g., Search1337xBrowserStrategy).
        """
        self._browser_strategy = strategy

    async def search(self, query: str) -> list[SearchResult]:
        """Search 1337x for torrents matching the query.

        Tries httpx first (fast, with magnet extraction). Falls back
        to browser strategy for ANY failure.
        """
        search_url = f'{self.BASE_URL}/search/{urllib.parse.quote_plus(query)}/1/'
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
                response = await client.get(search_url, headers=headers)

                if is_cloudflare_block(response.status_code, response.text):
                    return await self._browser_search(query)

                if response.status_code == 429:
                    logger.info(f'[{self.name}] httpx rate-limited (429), trying browser strategy')
                    return await self._browser_search(query)

                response.raise_for_status()
                results = self._parse_results(response.text)

                results = await self._fetch_magnets(client, headers, results)

                logger.info(f'[{self.name}] Found {len(results)} results (with magnets)')
                return results
        except httpx.ConnectError as e:
            classify_error(self.name, e)
            return await self._browser_search(query)
        except Exception as e:
            status = getattr(e.response, 'status_code', None) if hasattr(e, 'response') else None
            classify_error(self.name, e, status)
            return await self._browser_search(query)

    async def _browser_search(self, query: str) -> list[SearchResult]:
        """Use the browser strategy to scrape 1337x.

        Args:
            query: The search query.

        Returns:
            List of SearchResult objects.
        """
        if self._browser_strategy:
            scrape_result = await self._browser_strategy.search(query)
            if scrape_result.ok and scrape_result.candidates:
                return self._convert_candidates(scrape_result.candidates)
            logger.warning(f'[{self.name}] Browser strategy failed: {scrape_result.error or scrape_result.blocked_reason}')
            return []
        return []

    def _convert_candidates(self, candidates) -> list[SearchResult]:
        """Convert TorrentScrapeCandidate objects to SearchResult objects.

        Args:
            candidates: List of TorrentScrapeCandidate objects.

        Returns:
            List of SearchResult objects.
        """
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
        """Check if 1337x is reachable."""
        try:
            async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
                response = await client.get(self.BASE_URL, headers={'User-Agent': 'Mozilla/5.0'})
                if is_cloudflare_block(response.status_code, response.text):
                    return self._browser is not None and self._browser.available
                return response.status_code == 200
        except Exception:
            return self._browser is not None and self._browser.available

    def _parse_results(self, html: str) -> list[SearchResult]:
        """Parse 1337x HTML table into SearchResult objects."""
        try:
            soup = BeautifulSoup(html, 'html.parser')
        except Exception as e:
            logger.error(f'[{self.name}] HTML parse failed: {e}')
            return []

        results = []
        table = soup.find('table', class_='table-list')
        if not table:
            return results

        rows = table.find_all('tr')
        for row in rows:
            try:
                cells = row.find_all('td')
                if len(cells) < 4:
                    continue

                title_cell = cells[0]
                title_link = title_cell.find('a', href=lambda h: h and '/torrent/' in str(h))
                if not title_link:
                    continue
                title = title_link.get_text(strip=True)
                detail_path = title_link.get('href', '')
                if not title:
                    continue

                seeders = None
                try:
                    seeders = int(cells[-2].get_text(strip=True).replace(',', ''))
                except (ValueError, IndexError):
                    pass

                size = 'Unknown'
                try:
                    size_cell = cells[1].find('span', class_='size')
                    if size_cell:
                        size = size_cell.get_text(strip=True)
                    else:
                        size = cells[1].get_text(strip=True)
                except (IndexError, AttributeError):
                    pass

                detail_url = (self.BASE_URL + detail_path) if detail_path.startswith('/') else None

                results.append(SearchResult(
                    title=title,
                    magnet=None,
                    size=size,
                    seeders=seeders,
                    source=self.name,
                    url=detail_url,
                ))
            except Exception as e:
                logger.debug(f'[{self.name}] Row parse error: {e}')
                continue

        return results

    def _parse_results_with_magnets(self, html: str) -> list[SearchResult]:
        """Parse 1337x HTML from browser — includes magnets if present.

        Browser-rendered pages sometimes include magnet links directly.
        Results without magnets are filtered out since they're unusable.
        """
        results = self._parse_results(html)
        # Also try to extract magnets directly from the listing page
        try:
            soup = BeautifulSoup(html, 'html.parser')
            for a in soup.find_all('a', href=lambda h: h and str(h).startswith('magnet:')):
                magnet = a['href']
                # Try to match magnet to a result by title proximity
                for r in results:
                    if r.magnet is None and r.title and r.title in a.get_text():
                        r.magnet = magnet
                        break
        except Exception:
            pass

        usable = [r for r in results if r.magnet]
        if len(usable) < len(results):
            logger.debug(
                f'[{self.name}] {len(results) - len(usable)} results '
                f'without magnets from browser'
            )
        return usable

    async def _fetch_magnets(
        self, client: httpx.AsyncClient, headers: dict,
        results: list[SearchResult],
    ) -> list[SearchResult]:
        """Fetch magnet links from each result's detail page.

        1337x only includes magnet links on the torrent detail page.
        We fetch up to MAX_DETAIL_FETCHES pages to keep latency reasonable.
        """
        to_fetch = [r for r in results if r.url][:MAX_DETAIL_FETCHES]

        for result in to_fetch:
            try:
                detail_response = await client.get(result.url, headers=headers, timeout=10.0)
                detail_response.raise_for_status()
                magnet = self._extract_magnet(detail_response.text)
                if magnet:
                    result.magnet = magnet
            except Exception as e:
                logger.debug(f'[{self.name}] Detail page fetch failed for {result.url}: {e}')

        # Filter out results that still have no magnet — they're unusable
        usable = [r for r in results if r.magnet]
        if len(usable) < len(results):
            logger.debug(
                f'[{self.name}] {len(results) - len(usable)} results '
                f'dropped (no magnet link found)'
            )
        return usable

    @staticmethod
    def _extract_magnet(detail_html: str) -> str | None:
        """Extract the magnet link from a 1337x torrent detail page."""
        try:
            soup = BeautifulSoup(detail_html, 'html.parser')
            magnet_link = soup.find('a', href=lambda h: h and str(h).startswith('magnet:'))
            if magnet_link:
                return magnet_link['href']
        except Exception:
            pass
        return None
