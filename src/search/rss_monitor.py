"""
RSS monitor for LJS.

Polls Jackett RSS feeds periodically and matches new torrent items
against tracked items. When a match is found, triggers an immediate
download check instead of waiting for the scheduler's next interval.
This provides near-real-time detection of new releases.
"""

import asyncio
import re
from datetime import datetime, timezone
from xml.etree import ElementTree
from loguru import logger
from src.utils.log_sanitizer import redact_url
from typing import Optional, Callable
import httpx

from src.core.models import SearchResult, TaskCriticality
from src.core.categories.registry import CategoryRegistry

from src.core.task_supervisor import TaskSupervisor


class RSSMonitor:
    """Monitors RSS feeds for new torrents matching tracked items."""

    DEFAULT_POLL_INTERVAL = 900  # 15 minutes

    def __init__(self, feed_urls: list[str], item_names: list[str],
                 supervisor: TaskSupervisor,
                 on_match: Optional[Callable] = None,
                 poll_interval: int = DEFAULT_POLL_INTERVAL,
                 category_registry: CategoryRegistry | None = None,
                 item_categories: dict[str, str] | None = None):
        """Initialize RSS monitoring for tracked items.

        Args:
            feed_urls: RSS feed URLs to poll.
            item_names: Lower-priority match names from tracked category items.
            supervisor: Task supervisor that owns the polling task.
            on_match: Optional async callback for matched items.
            poll_interval: Seconds between feed polling cycles.
            category_registry: Registry used for parsing feed item titles.
            item_categories: Optional map of item name to registry category ID.
        """
        self._feed_urls = feed_urls
        self._item_names: list[str] = []
        self._item_display_names: dict[str, str] = {}
        self._item_categories: dict[str, str] = {}
        self._on_match = on_match
        self._poll_interval = poll_interval
        self._seen_magnets: set[str] = set()
        self._supervisor = supervisor
        self._categories = category_registry or CategoryRegistry.with_defaults()
        self.update_items(item_names, item_categories=item_categories)

    def update_items(self, item_names: list[str], item_categories: dict[str, str] | None = None) -> None:
        """Update the list of tracked item names."""
        self._item_names = [name.lower() for name in item_names]
        self._item_display_names = {str(name).lower(): str(name) for name in item_names if name}
        categories = item_categories or {}
        self._item_categories = {
            str(name).lower(): str(category_id)
            for name, category_id in categories.items()
            if name and category_id
        }

    def start(self) -> None:
        """Start the RSS monitor as a background task."""
        self._supervisor.spawn_restartable(
            "rss_monitor",
            self._poll_loop,
            TaskCriticality.IMPORTANT,
        )
        logger.info(
            f"RSS monitor started: {len(self._feed_urls)} feeds, "
            f"{len(self._item_names)} items, "
            f"poll interval: {self._poll_interval}s"
        )

    async def _poll_loop(self) -> None:
        """Main polling loop: fetch feeds, match, trigger callbacks."""
        try:
            while True:
                await self._poll_all_feeds()
                await asyncio.sleep(self._poll_interval)
        except asyncio.CancelledError:
            logger.debug("RSS monitor loop cancelled")
            raise
        except Exception as e:
            logger.error(f"RSS monitor loop crashed: {e}")
            raise

    async def _poll_all_feeds(self) -> None:
        """Fetch all RSS feeds and process new items."""
        for url in self._feed_urls:
            try:
                # Check for cancellation between feeds
                await asyncio.sleep(0)
                items = await self._fetch_feed(url)
                matched = self._match_items(items)
                for item_name, result, unit_label in matched:
                    logger.info(
                        f"RSS match: '{result.title}' -> item '{item_name}' (unit={unit_label})"
                    )
                    if self._on_match:
                        try:
                            await asyncio.wait_for(
                                self._on_match(item_name, unit_label), timeout=60,
                            )
                        except asyncio.TimeoutError:
                            logger.warning(f"RSS match callback timed out for {item_name}")
                        except Exception as e:
                            logger.exception(f"RSS match callback failed: {e}")
            except asyncio.CancelledError:
                logger.debug("RSS poll cancelled")
                raise
            except Exception as e:
                logger.warning(f"RSS feed {redact_url(url)} failed: {e}")

    async def _fetch_feed(self, url: str) -> list[SearchResult]:
        """Fetch and parse an RSS feed into SearchResult objects.

        Handles both standard RSS 2.0 and Atom-style feeds.
        """
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, verify=False) as client:
                response = await client.get(url)
                response.raise_for_status()
                xml_text = response.text
        except Exception as e:
            logger.error(f"RSS fetch failed for {redact_url(url)}: {e}")
            return []

        try:
            root = ElementTree.fromstring(xml_text)
        except ElementTree.ParseError as e:
            logger.error(f"RSS parse failed for {url}: {e}")
            return []

        results = []
        # Handle RSS 2.0 format
        for item in root.iter("item"):
            title = self._get_text(item, "title") or ""
            magnet = ""
            link = self._get_text(item, "link") or ""
            # Check if link is a magnet URI
            if link.startswith("magnet:"):
                magnet = link
            # Also check enclosure or magnet-specific elements
            enclosure = item.find("enclosure")
            if enclosure is not None and not magnet:
                enc_url = enclosure.get("url", "")
                if enc_url.startswith("magnet:"):
                    magnet = enc_url

            # Check for magnet in non-standard elements (common in Jackett)
            for child in item:
                if child.text and child.text.startswith("magnet:"):
                    magnet = child.text
                    break

            size_str = self._get_text(item, "size") or ""
            size_bytes = self._parse_size(size_str)

            seeders = None
            seeders_text = self._get_text(item, "seeders") or ""
            if seeders_text:
                try:
                    seeders = int(seeders_text)
                except ValueError:
                    pass

            if not title:
                continue

            results.append(SearchResult(
                title=title,
                magnet=magnet if magnet else None,
                size=size_str if size_str else "Unknown",
                size_bytes=size_bytes if size_bytes > 0 else None,
                seeders=seeders,
                source="rss",
            ))

        return results

    def _match_items(self, items: list[SearchResult]) -> list[tuple[str, SearchResult, str | None]]:
        """Match RSS items against tracked item names.

        Returns list of (item_name, result, unit_label) tuples for matches that
        haven't been seen before. ``unit_label`` is category-owned and may be
        absent when the feed item cannot be mapped to a concrete unit.
        """
        matched = []
        for item in items:
            magnet_key = item.magnet or item.title
            if magnet_key in self._seen_magnets:
                continue

            for name in self._item_names:
                category_id = self._item_categories.get(name, "")
                parsed_name, unit_label = self._parse_match_candidate(item.title, category_id)
                match_haystacks = [parsed_name.lower(), item.title.lower()]
                if any(re.search(r'\b' + re.escape(name) + r'\b', haystack) for haystack in match_haystacks):
                    self._seen_magnets.add(magnet_key)
                    original_name = self._item_display_names.get(name, name)
                    matched.append((original_name, item, unit_label))
                    break

        return matched

    def _parse_match_candidate(self, title: str, category_id: str = "") -> tuple[str, str | None]:
        """Return a category-parsed title and optional category-owned unit label."""
        category = self._categories.get(category_id) if category_id else None
        parsed = None
        if category:
            parsed = category.parse_name(title)
        else:
            classified = self._categories.classify(title)
            if classified:
                category, parsed = classified
        if not parsed:
            return title, None
        parsed_title = str(getattr(parsed, "title", "") or title)
        unit_label = category.rss_unit_label_from_parsed(parsed) if category and hasattr(category, "rss_unit_label_from_parsed") else None
        return parsed_title, unit_label

    @staticmethod
    def _get_text(element: ElementTree.Element, tag: str) -> Optional[str]:
        """Get text content of a child element, handling namespaces."""
        child = element.find(tag)
        if child is not None and child.text:
            return child.text.strip()
        return None

    @staticmethod
    def _parse_size(size_str: str) -> int:
        """Parse a size string (e.g., '1.5 GB') into bytes."""
        if not size_str:
            return 0
        size_str = size_str.strip().lower()
        try:
            import re
            m = re.search(r"(\d+(?:\.\d+)?)\s*(gb|mb|tb|kb)", size_str)
            if not m:
                # Try pure number (bytes)
                return int(float(size_str))
            value = float(m.group(1))
            unit = m.group(2)
            if unit == "tb":
                return int(value * 1024 * 1024 * 1024 * 1024)
            elif unit == "gb":
                return int(value * 1024 * 1024 * 1024)
            elif unit == "mb":
                return int(value * 1024 * 1024)
            elif unit == "kb":
                return int(value * 1024)
        except (ValueError, TypeError):
            pass
        return 0
