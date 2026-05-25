"""
Torrent provider browser strategies for LJS.

Defines the TorrentBrowserStrategy ABC and per-provider implementations
that use the Playwright browser runtime for deterministic scraping.
Each strategy knows its provider's page structure, selectors, and
magnet extraction methods.
"""

from abc import ABC, abstractmethod
import time
from typing import TYPE_CHECKING
from loguru import logger

from src.core.models import BrowserFetchRequest, TorrentScrapeCandidate, TorrentScrapeResult

if TYPE_CHECKING:
    from src.utils.browser.runtime import BrowserRuntime


class TorrentBrowserStrategy(ABC):
    """Browser-backed scraping strategy for one torrent provider.

    Each implementation knows the provider's search URL format,
    selectors that prove the page loaded, result row selectors,
    detail page selectors, magnet extraction method, and
    no-results detection.
    """

    def __init__(self, runtime: "BrowserRuntime"):
        """Initialize with a shared browser runtime.

        Args:
            runtime: BrowserRuntime instance for Playwright page fetches.
        """
        self._runtime = runtime

    @abstractmethod
    async def search(self, query: str) -> TorrentScrapeResult:
        """Search provider with browser and return raw provider candidates.

        Args:
            query: The search query string.

        Returns:
            TorrentScrapeResult with candidates and diagnostics.
        """
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """The display name of the provider this strategy targets."""
        ...


class Search1337xBrowserStrategy(TorrentBrowserStrategy):
    """Browser-based scraping strategy for 1337x.to.

    Opens search page, extracts result rows, then fetches detail
    pages for each result to acquire magnet links. This strategy
    handles the full pipeline that the httpx path does — the
    key difference from the old browser fallback which skipped
    detail page fetching entirely.
    """

    BASE_URL = 'https://1337x.to'
    MAX_DETAIL_FETCHES = 10

    @property
    def provider_name(self) -> str:
        """Execute the public Search1337xBrowserStrategy.provider_name behavior.

        This method is a supported extension point for callers outside the
        class.  Keep its input/output contract stable and move specialized
        logic into collaborators or protected helpers as the feature grows.
        """
        return '1337x'

    async def search(self, query: str) -> TorrentScrapeResult:
        """Search 1337x with browser and return candidates with magnets.

        Opens the search page through the browser runtime, extracts
        result rows, then fetches detail pages to acquire magnet links.

        Args:
            query: The search query string.

        Returns:
            TorrentScrapeResult with candidates and diagnostics.
        """
        import urllib.parse
        start_time = time.monotonic()
        search_url = f'{self.BASE_URL}/search/{urllib.parse.quote_plus(query)}/1/'
        logger.info(f'[1337x-browser] Searching for: {query}')

        request = self._make_request(search_url, wait_for_selector='table.table-list')
        result = await self._runtime.fetch(request)

        if not result.ok:
            elapsed = int((time.monotonic() - start_time) * 1000)
            return TorrentScrapeResult(
                provider=self.provider_name,
                query=query,
                ok=False,
                error=result.error,
                blocked_reason=result.blocked_reason,
                elapsed_ms=elapsed,
            )

        rows = self._extract_search_rows(result.html)
        if not rows:
            elapsed = int((time.monotonic() - start_time) * 1000)
            return TorrentScrapeResult(
                provider=self.provider_name,
                query=query,
                ok=True,
                candidates=[],
                elapsed_ms=elapsed,
            )

        candidates = await self._enrich_with_magnets(rows)
        elapsed = int((time.monotonic() - start_time) * 1000)

        return TorrentScrapeResult(
            provider=self.provider_name,
            query=query,
            ok=True,
            candidates=candidates,
            elapsed_ms=elapsed,
        )

    def _extract_search_rows(self, html: str) -> list[TorrentScrapeCandidate]:
        """Parse 1337x search result table from rendered HTML.

        Args:
            html: The rendered search page HTML.

        Returns:
            List of TorrentScrapeCandidate objects from the listing.
        """
        from bs4 import BeautifulSoup

        try:
            soup = BeautifulSoup(html, 'html.parser')
        except Exception as e:
            logger.error(f'[1337x-browser] HTML parse failed: {e}')
            return []

        candidates = []
        table = soup.find('table', class_='table-list')
        if not table:
            return candidates

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

                candidates.append(TorrentScrapeCandidate(
                    title=title,
                    detail_url=detail_url,
                    magnet=None,
                    size=size,
                    seeders=seeders,
                    source=self.provider_name,
                    extraction_method='browser_listing',
                    extraction_confidence=0.5,
                    missing_fields=['magnet'],
                ))
            except Exception as e:
                logger.debug(f'[1337x-browser] Row parse error: {e}')
                continue

        return candidates

    async def _enrich_with_magnets(
        self, candidates: list[TorrentScrapeCandidate],
    ) -> list[TorrentScrapeCandidate]:
        """Fetch detail pages through the browser runtime to extract magnet links.

        Limits detail page fetches to MAX_DETAIL_FETCHES. Drops candidates
        whose detail pages fail to load or have no magnet.

        Args:
            candidates: Candidates from the search listing page.

        Returns:
            Candidates enriched with magnet links from detail pages.
        """
        to_fetch = [c for c in candidates if c.detail_url][:self.MAX_DETAIL_FETCHES]
        enriched = []

        for candidate in to_fetch:
            try:
                request = self._make_request(
                    candidate.detail_url,
                    wait_for_selector='a[href^="magnet:"]',
                )
                result = await self._runtime.fetch(request)

                if not result.ok or result.challenge_detected:
                    candidate.extraction_confidence = 0.0
                    candidate.missing_fields.append('detail_blocked')
                    enriched.append(candidate)
                    continue

                magnet = self._extract_magnet_from_html(result.html)
                if magnet:
                    candidate.magnet = magnet
                    candidate.extraction_method = 'browser_detail_page'
                    candidate.extraction_confidence = 0.9
                    candidate.missing_fields = [f for f in candidate.missing_fields if f != 'magnet']
                else:
                    candidate.extraction_confidence = 0.2
                enriched.append(candidate)

            except Exception as e:
                logger.debug(f'[1337x-browser] Detail page fetch failed for {candidate.title}: {e}')
                candidate.extraction_confidence = 0.0
                candidate.missing_fields.append('detail_fetch_error')
                enriched.append(candidate)

        return enriched

    @staticmethod
    def _extract_magnet_from_html(html: str) -> str | None:
        """Extract magnet link from a 1337x detail page HTML.

        Args:
            html: Rendered detail page HTML.

        Returns:
            Magnet URI string or None.
        """
        from bs4 import BeautifulSoup

        try:
            soup = BeautifulSoup(html, 'html.parser')
            magnet_link = soup.find('a', href=lambda h: h and str(h).startswith('magnet:'))
            if magnet_link:
                return magnet_link['href']
        except Exception:
            pass
        return None

    @staticmethod
    def _make_request(url: str, wait_for_selector: str = None) -> BrowserFetchRequest:
        """Build a BrowserFetchRequest with sensible defaults for 1337x.

        Args:
            url: The URL to fetch.
            wait_for_selector: Optional CSS selector to wait for.

        Returns:
            Configured BrowserFetchRequest.
        """
        return BrowserFetchRequest(
            url=url,
            wait_seconds=2.0,
            wait_for_selector=wait_for_selector,
            max_content_chars=8000,
            screenshot_on_failure=True,
            purpose='torrent_search',
        )


class BTDiggBrowserStrategy(TorrentBrowserStrategy):
    """Browser-based scraping strategy for btdig.com.

    BTDigg search pages include magnet links directly in the listing,
    so no detail page fetching is needed. The strategy handles URL
    construction, browser fetch, and HTML parsing.
    """

    BASE_URL = 'https://btdig.com'

    @property
    def provider_name(self) -> str:
        """Execute the public BTDiggBrowserStrategy.provider_name behavior.

        This method is a supported extension point for callers outside the
        class.  Keep its input/output contract stable and move specialized
        logic into collaborators or protected helpers as the feature grows.
        """
        return 'BTDigg'

    async def search(self, query: str) -> TorrentScrapeResult:
        """Search BTDigg with browser and return candidates with magnets.

        Args:
            query: The search query string.

        Returns:
            TorrentScrapeResult with candidates and diagnostics.
        """
        import urllib.parse
        from bs4 import BeautifulSoup

        start_time = time.monotonic()
        url = f'{self.BASE_URL}/search?q={urllib.parse.quote_plus(query)}'
        logger.info(f'[BTDigg-browser] Searching for: {query}')

        request = BrowserFetchRequest(
            url=url,
            wait_seconds=2.0,
            wait_for_selector='div.one_result',
            max_content_chars=8000,
            screenshot_on_failure=True,
            purpose='torrent_search',
        )
        result = await self._runtime.fetch(request)

        if not result.ok:
            elapsed = int((time.monotonic() - start_time) * 1000)
            return TorrentScrapeResult(
                provider=self.provider_name,
                query=query,
                ok=False,
                error=result.error,
                blocked_reason=result.blocked_reason,
                elapsed_ms=elapsed,
            )

        candidates = self._parse_results(result.html)
        elapsed = int((time.monotonic() - start_time) * 1000)

        return TorrentScrapeResult(
            provider=self.provider_name,
            query=query,
            ok=True,
            candidates=candidates,
            elapsed_ms=elapsed,
        )

    def _parse_results(self, html: str) -> list[TorrentScrapeCandidate]:
        """Parse BTDigg HTML into TorrentScrapeCandidate objects.

        Args:
            html: Rendered search page HTML.

        Returns:
            List of TorrentScrapeCandidate objects.
        """
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, 'html.parser')
        candidates = []

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
                    candidates.append(TorrentScrapeCandidate(
                        title=title,
                        magnet=magnet,
                        size=size,
                        source=self.provider_name,
                        extraction_method='browser_listing',
                        extraction_confidence=0.9,
                        missing_fields=[],
                    ))
            except Exception as e:
                logger.debug(f'[{self.provider_name}-browser] Error parsing result: {e}')
                continue

        return candidates


class TorrentGalaxyBrowserStrategy(TorrentBrowserStrategy):
    """Browser-based scraping strategy for torrentgalaxy.to.

    TorrentGalaxy search pages include magnet links directly in the
    listing rows, so no detail page fetching is needed.
    """

    BASE_URL = 'https://torrentgalaxy.to'

    @property
    def provider_name(self) -> str:
        """Execute the public TorrentGalaxyBrowserStrategy.provider_name behavior.

        This method is a supported extension point for callers outside the
        class.  Keep its input/output contract stable and move specialized
        logic into collaborators or protected helpers as the feature grows.
        """
        return 'TorrentGalaxy'

    async def search(self, query: str) -> TorrentScrapeResult:
        """Search TorrentGalaxy with browser and return candidates with magnets.

        Args:
            query: The search query string.

        Returns:
            TorrentScrapeResult with candidates and diagnostics.
        """
        import urllib.parse

        start_time = time.monotonic()
        params = {'q': query}
        url = f'{self.BASE_URL}/torrents.php?' + urllib.parse.urlencode(params)
        logger.info(f'[TorrentGalaxy-browser] Searching for: {query}')

        request = BrowserFetchRequest(
            url=url,
            wait_seconds=2.0,
            wait_for_selector='div.tgxtablerow, div.tgxtable',
            max_content_chars=8000,
            screenshot_on_failure=True,
            purpose='torrent_search',
        )
        result = await self._runtime.fetch(request)

        if not result.ok:
            elapsed = int((time.monotonic() - start_time) * 1000)
            return TorrentScrapeResult(
                provider=self.provider_name,
                query=query,
                ok=False,
                error=result.error,
                blocked_reason=result.blocked_reason,
                elapsed_ms=elapsed,
            )

        candidates = self._parse_results(result.html)
        elapsed = int((time.monotonic() - start_time) * 1000)

        return TorrentScrapeResult(
            provider=self.provider_name,
            query=query,
            ok=True,
            candidates=candidates,
            elapsed_ms=elapsed,
        )

    def _parse_results(self, html: str) -> list[TorrentScrapeCandidate]:
        """Parse TorrentGalaxy HTML into TorrentScrapeCandidate objects.

        Args:
            html: Rendered search page HTML.

        Returns:
            List of TorrentScrapeCandidate objects.
        """
        from bs4 import BeautifulSoup

        try:
            soup = BeautifulSoup(html, 'html.parser')
        except Exception as e:
            logger.error(f'[{self.provider_name}-browser] HTML parse failed: {e}')
            return []

        candidates = []
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

                candidates.append(TorrentScrapeCandidate(
                    title=title,
                    magnet=magnet,
                    size=size,
                    seeders=seeders,
                    source=self.provider_name,
                    extraction_method='browser_listing',
                    extraction_confidence=0.9 if magnet else 0.3,
                    missing_fields=[] if magnet else ['magnet'],
                ))
            except Exception as e:
                logger.debug(f'[{self.provider_name}-browser] Row parse error: {e}')
                continue

        return candidates


class NyaaBrowserStrategy(TorrentBrowserStrategy):
    """Browser-based scraping strategy for nyaa.si.

    Nyaa search pages include magnet links directly in result rows.
    Sorted by seeders descending for best results first.
    """

    BASE_URL = 'https://nyaa.si'

    @property
    def provider_name(self) -> str:
        """Execute the public NyaaBrowserStrategy.provider_name behavior.

        This method is a supported extension point for callers outside the
        class.  Keep its input/output contract stable and move specialized
        logic into collaborators or protected helpers as the feature grows.
        """
        return 'nyaa'

    async def search(self, query: str) -> TorrentScrapeResult:
        """Search Nyaa with browser and return candidates with magnets.

        Args:
            query: The search query string.

        Returns:
            TorrentScrapeResult with candidates and diagnostics.
        """
        import urllib.parse

        start_time = time.monotonic()
        url = f'{self.BASE_URL}/?f=0&q={urllib.parse.quote_plus(query)}&s=seeders&o=desc'
        logger.info(f'[Nyaa-browser] Searching for: {query}')

        request = BrowserFetchRequest(
            url=url,
            wait_seconds=2.0,
            wait_for_selector='table.torrent-list',
            max_content_chars=8000,
            screenshot_on_failure=True,
            purpose='torrent_search',
        )
        result = await self._runtime.fetch(request)

        if not result.ok:
            elapsed = int((time.monotonic() - start_time) * 1000)
            return TorrentScrapeResult(
                provider=self.provider_name,
                query=query,
                ok=False,
                error=result.error,
                blocked_reason=result.blocked_reason,
                elapsed_ms=elapsed,
            )

        candidates = self._parse_results(result.html)
        elapsed = int((time.monotonic() - start_time) * 1000)

        return TorrentScrapeResult(
            provider=self.provider_name,
            query=query,
            ok=True,
            candidates=candidates,
            elapsed_ms=elapsed,
        )

    def _parse_results(self, html: str) -> list[TorrentScrapeCandidate]:
        """Parse Nyaa HTML table into TorrentScrapeCandidate objects.

        Args:
            html: Rendered search page HTML.

        Returns:
            List of TorrentScrapeCandidate objects.
        """
        from bs4 import BeautifulSoup

        try:
            soup = BeautifulSoup(html, 'html.parser')
        except Exception as e:
            logger.error(f'[{self.provider_name}-browser] HTML parse failed: {e}')
            return []

        candidates = []
        table = soup.find('table', class_='torrent-list')
        if not table:
            return candidates

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

                candidates.append(TorrentScrapeCandidate(
                    title=title,
                    magnet=magnet if magnet else None,
                    size=size,
                    seeders=seeders,
                    source=self.provider_name,
                    extraction_method='browser_listing',
                    extraction_confidence=0.9 if magnet else 0.3,
                    missing_fields=[] if magnet else ['magnet'],
                ))

            except Exception as e:
                logger.debug(f'[{self.provider_name}-browser] Row parse error: {e}')
                continue

        return candidates
