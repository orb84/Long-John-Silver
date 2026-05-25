"""
Torznab search provider for LJS.

Provides a generic TorznabSearch that works with any Torznab-compatible
server (Jackett, Prowlarr, bitmagnet, etc.) using the standard Torznab
API specification. Supports both XML and JSON response formats.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

import httpx
from loguru import logger

from src.core.models import SearchResult
from src.search.base import SearchProvider


class TorznabSearch(SearchProvider):
    """Search any Torznab-compatible indexer via structured API.

    Works with Jackett, Prowlarr, bitmagnet, or any server implementing
    the Torznab specification. Supports XML and JSON response formats,
    category filtering, and extended attributes.

    Args:
        url: Base URL of the Torznab server (e.g. ``http://localhost:9117``).
        api_key: API key for the Torznab server.
        categories: Optional provider category IDs to apply to every search.
        category_filters: Optional registry-category-to-provider-category map.
            This keeps Torznab filtering configurable instead of baking
            application category semantics into the provider adapter.
        preferred_format: Response format — ``"xml"`` (default) or ``"json"``.
    """

    def __init__(
        self,
        url: str,
        api_key: str,
        categories: list[int] | None = None,
        category_filters: dict[str, str | int | list[int] | tuple[int, ...]] | None = None,
        preferred_format: str = "xml",
    ) -> None:
        super().__init__()
        self._url = url.rstrip("/")
        self._api_key = api_key
        self._categories = categories
        self._category_filters = self._normalize_category_filters(category_filters or {})
        self._format = preferred_format if preferred_format in ("xml", "json") else "xml"
        self._timeout: float = 30.0

    @property
    def name(self) -> str:
        """Return the stable provider name used in diagnostics and UI labels.

        Keep this value short and unchanged unless migrating cached provider
        metadata and user-facing configuration at the same time.
        """
        return "Torznab"

    @property
    def supported_categories(self) -> list[str]:
        """Return the requested supported categories value.

        This public accessor should normalize missing or optional data at the
        boundary and avoid leaking storage/provider internals to callers.
        """
        return ["*"]

    async def search(self, query: str, category: str | None = None) -> list[SearchResult]:
        """Search the Torznab indexer for the given query.

        Args:
            query: The search string.
            category: Optional registry category hint. If ``category_filters``
                was configured, the matching provider category list is applied.

        Returns:
            A list of SearchResult objects. Empty list on error.
        """
        params = {
            "apikey": self._api_key,
            "t": "search",
            "q": query,
            "extended": "1",
        }

        if self._format == "json":
            params["json"] = "true"

        if self._categories:
            params["cat"] = ",".join(str(c) for c in self._categories)
        elif category and category in self._category_filters:
            params["cat"] = self._category_filters[category]

        endpoint = f"{self._url}/api"
        logger.info(f"[{self.name}] Searching for: {query}")

        try:
            async with httpx.AsyncClient(timeout=self._timeout, verify=False) as client:
                response = await client.get(endpoint, params=params)
                response.raise_for_status()

            if self._format == "json":
                results = self._parse_json(response.json())
            else:
                results = self._parse_xml(response.text)

            logger.info(f"[{self.name}] Found {len(results)} results.")
            return results

        except httpx.ConnectError:
            logger.error(f"[{self.name}] Connection refused — is the server running on {self._url}?")
            return []
        except httpx.HTTPStatusError as e:
            logger.error(f"[{self.name}] HTTP {e.response.status_code} from Torznab API")
            return []
        except ET.ParseError as e:
            logger.error(f"[{self.name}] Failed to parse XML response: {e}")
            return []
        except Exception as e:
            logger.error(f"[{self.name}] Search failed: {e}")
            return []

    @staticmethod
    def _normalize_category_filters(filters: dict[str, str | int | list[int] | tuple[int, ...]]) -> dict[str, str]:
        """Normalize configured Torznab category filters to comma strings."""
        normalized: dict[str, str] = {}
        for category_id, value in filters.items():
            key = str(category_id or "").strip()
            if not key:
                continue
            if isinstance(value, (list, tuple)):
                cat_value = ",".join(str(item) for item in value if item is not None)
            else:
                cat_value = str(value or "")
            if cat_value:
                normalized[key] = cat_value
        return normalized

    async def health_check(self) -> bool:
        """Check if the Torznab server is reachable.

        Sends a minimal API request. Any successful response (including
        error about missing parameters) confirms the server is alive.
        """
        try:
            async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
                response = await client.get(f"{self._url}/api", params={"apikey": self._api_key, "t": "search", "q": ""})
                return response.status_code < 500
        except Exception:
            return False

    def _parse_xml(self, xml_text: str) -> list[SearchResult]:
        """Parse a Torznab XML RSS response into SearchResult objects.

        Handles the standard Torznab RSS format with torznab:attr
        elements for seeders, peers, etc.
        """
        results: list[SearchResult] = []
        root = ET.fromstring(xml_text)

        ns = {"torznab": "http://torznab.com/schemas/2015/feed"}
        channel = root.find("channel")
        if channel is None:
            return results

        for item in channel.findall("item"):
            try:
                title_el = item.find("title")
                title = title_el.text if title_el is not None else "Unknown"

                link_el = item.find("link")
                link = link_el.text if link_el is not None else ""

                size_el = item.find("size")
                size = size_el.text if size_el is not None else "0"

                magnet = link if link and link.startswith("magnet:") else None
                seeders: int | None = None

                for attr in item.findall("torznab:attr", ns):
                    attr_name = attr.get("name", "")
                    attr_value = attr.get("value", "")
                    if attr_name == "seeders":
                        try:
                            seeders = int(attr_value)
                        except (ValueError, TypeError):
                            pass

                results.append(SearchResult(
                    title=title,
                    magnet=magnet,
                    size=size,
                    seeders=seeders,
                    source=self.name,
                    url=link if not magnet else None,
                ))
            except Exception as e:
                logger.debug(f"[{self.name}] Error parsing XML item: {e}")
                continue

        return results

    def _parse_json(self, data: dict[str, Any]) -> list[SearchResult]:
        """Parse a Torznab JSON response into SearchResult objects.

        Handles the JSON format supported by many Torznab implementations
        (Jackett, Prowlarr) when ``json=true`` is passed.
        """
        results: list[SearchResult] = []
        channel = data.get("channel", {})
        items = channel.get("item", [])

        if not isinstance(items, list):
            items = [items] if items else []

        for item in items:
            try:
                title = item.get("title", "Unknown")
                link = item.get("link", "")
                size = str(item.get("size", 0))

                magnet = link if link and link.startswith("magnet:") else None
                seeders: int | None = None

                for attr in item.get("attr", []):
                    if attr.get("name") == "seeders":
                        try:
                            seeders = int(attr.get("value", 0))
                        except (ValueError, TypeError):
                            pass

                results.append(SearchResult(
                    title=title,
                    magnet=magnet,
                    size=size,
                    seeders=seeders,
                    source=self.name,
                    url=link if not magnet else None,
                ))
            except Exception as e:
                logger.debug(f"[{self.name}] Error parsing JSON item: {e}")
                continue

        return results
