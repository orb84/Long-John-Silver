"""
Jackett JSON search provider for LJS.

Jackett is the production torrent-search backend for LJS. Although Jackett can
also expose Torznab-compatible feeds, its native JSON results endpoint is the
most direct integration for a managed local Jackett instance and avoids hiding
Jackett behind confusing provider names in logs and UI.
"""

from __future__ import annotations

from typing import Any

import httpx
from loguru import logger

from src.core.models import SearchResult
from src.search.base import SearchProvider


class JackettSearch(SearchProvider):
    """Search Jackett's aggregate JSON API across all configured indexers."""

    def __init__(self, url: str, api_key: str, timeout: float = 30.0) -> None:
        """Create a Jackett provider.

        Args:
            url: Base Jackett URL, for example ``http://127.0.0.1:9117``.
            api_key: Jackett API key.
            timeout: Per-request timeout in seconds.
        """
        super().__init__()
        self._url = url.rstrip('/')
        self._api_key = api_key
        self._timeout = timeout

    @property
    def name(self) -> str:
        """Return the user-facing provider name."""
        return 'Jackett'

    @property
    def supported_categories(self) -> list[str]:
        """Jackett can search all category IDs; indexers decide coverage."""
        return ['*']

    async def search(self, query: str, category: str | None = None) -> list[SearchResult]:
        """Search all configured Jackett indexers for a query.

        Args:
            query: Torrent search query.
            category: Optional LJS category hint. Jackett's JSON endpoint uses
                tracker/indexer configuration for category matching, so this is
                currently used only for logging and future category tuning.

        Returns:
            Parsed torrent candidates with magnet links whenever available.
        """
        endpoint = f'{self._url}/api/v2.0/indexers/all/results'
        params = {'apikey': self._api_key, 'Query': query}
        logger.info(f'[Jackett] Searching for: {query}')
        try:
            async with httpx.AsyncClient(timeout=self._timeout, verify=False) as client:
                response = await client.get(endpoint, params=params, follow_redirects=False)
                if self._is_login_redirect(response):
                    self.record_error_category('auth_redirect')
                    logger.warning('[Jackett] API redirected to UI login; check saved API key/admin auth. Falling back if configured.')
                    return []
                response.raise_for_status()
            results = self._parse_payload(response.json())
            logger.info(f'[Jackett] Found {len(results)} results.')
            self.record_error_category('')
            return results
        except httpx.ConnectError:
            self.record_error_category('connection')
            logger.error(f'[Jackett] Connection refused — is Jackett running on {self._url}?')
        except httpx.HTTPStatusError as exc:
            self.record_error_category(f'http_{exc.response.status_code}')
            logger.error(f'[Jackett] HTTP {exc.response.status_code} from Jackett API')
        except Exception as exc:
            self.record_error_category('unknown')
            logger.error(f'[Jackett] Search failed: {exc}')
        return []

    async def health_check(self) -> bool:
        """Check that the Jackett API is reachable with the configured key."""
        try:
            async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
                response = await client.get(
                    f'{self._url}/api/v2.0/server/config',
                    params={'apikey': self._api_key},
                    follow_redirects=False,
                )
            return response.status_code == 200 and not self._is_login_redirect(response)
        except Exception:
            return False


    @staticmethod
    def _is_login_redirect(response: httpx.Response) -> bool:
        """Return whether Jackett redirected an API request to the UI login."""
        if response.status_code not in {301, 302, 303, 307, 308}:
            return False
        location = str(response.headers.get('location') or '').lower()
        return '/ui/login' in location

    def _parse_payload(self, payload: Any) -> list[SearchResult]:
        """Parse Jackett JSON response variants into SearchResult objects."""
        if isinstance(payload, dict):
            rows = payload.get('Results') or payload.get('results') or []
        elif isinstance(payload, list):
            rows = payload
        else:
            rows = []
        parsed: list[SearchResult] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            result = self._parse_row(row)
            if result:
                parsed.append(result)
        return parsed

    def _parse_row(self, row: dict[str, Any]) -> SearchResult | None:
        """Parse one Jackett result row."""
        title = str(row.get('Title') or row.get('title') or '').strip()
        if not title:
            return None
        magnet = row.get('MagnetUri') or row.get('MagnetUrl') or row.get('magnet')
        # Jackett's native JSON endpoint often exposes private/indexer download
        # URLs in Link while MagnetUri is empty.  The DownloadManager already
        # resolves HTTP(S) torrent URLs to magnets, so keep Link as a queueable
        # candidate instead of dropping hundreds of otherwise valid results.
        link = row.get('Link') or ''
        guid = row.get('Guid') or ''
        details = row.get('Details') or row.get('Comments') or ''
        if not magnet and isinstance(link, str):
            if link.startswith('magnet:') or link.startswith(('http://', 'https://')):
                magnet = link
        if not magnet and isinstance(guid, str) and guid.startswith('magnet:'):
            magnet = guid
        detail_url = details or (None if str(link).startswith(('magnet:', 'http://', 'https://')) else link) or guid
        size_bytes = self._safe_int(row.get('Size') or row.get('size'))
        seeders = self._safe_int(row.get('Seeders') or row.get('seeders'))
        peers = self._safe_int(row.get('Peers') or row.get('Peers'))
        tracker = row.get('Tracker') or row.get('TrackerId') or row.get('Indexer') or ''
        result = SearchResult(
            title=title,
            magnet=str(magnet) if magnet else None,
            size=str(size_bytes or row.get('Size') or 'Unknown'),
            seeders=seeders,
            source=f'Jackett:{tracker}' if tracker else 'Jackett',
            url=str(detail_url) if detail_url else None,
        )
        if size_bytes is not None:
            result.size_bytes = size_bytes
        return result

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        """Convert Jackett numeric fields to integers when possible."""
        try:
            if value is None or value == '':
                return None
            return int(float(value))
        except (TypeError, ValueError):
            return None
