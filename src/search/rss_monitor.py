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
from collections.abc import Sequence
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
                 item_categories: dict[str, str] | None = None,
                 feed_targets: dict[str, Sequence[str]] | None = None,
                 max_feeds_per_cycle: int = 8):
        """Initialize RSS monitoring for tracked items.

        Args:
            feed_urls: RSS feed URLs to poll.
            item_names: Lower-priority match names from tracked category items.
            supervisor: Task supervisor that owns the polling task.
            on_match: Optional async callback for matched items.
            poll_interval: Seconds between feed polling cycles.
            category_registry: Registry used for parsing feed item titles.
            item_categories: Optional map of item name to registry category ID.
            feed_targets: Optional map limiting which tracked names may match each feed URL.
                This prevents broad Jackett RSS feeds from matching unrelated releases.
            max_feeds_per_cycle: Maximum number of feed URLs to poll each cycle.
                Per-item Jackett feeds are rotated across cycles so startup does not hammer
                every configured indexer for every tracked show at once.
        """
        self._feed_urls = feed_urls
        self._item_names: list[str] = []
        self._item_display_names: dict[str, str] = {}
        self._item_categories: dict[str, str] = {}
        self._on_match = on_match
        self._poll_interval = poll_interval
        self._seen_magnets: set[str] = set()
        self._fetch_error_log_state: dict[str, tuple[str, float]] = {}
        self._supervisor = supervisor
        self._categories = category_registry or CategoryRegistry.with_defaults()
        self._feed_targets: dict[str, list[str]] = {}
        self._poll_cursor = 0
        self._max_feeds_per_cycle = max(1, int(max_feeds_per_cycle or 1))
        self.update_items(item_names, item_categories=item_categories)
        if feed_targets:
            self._feed_targets = {
                str(url): [str(name).lower() for name in names if str(name).strip()]
                for url, names in feed_targets.items()
            }

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


    def update_feeds(
        self,
        feed_urls: list[str],
        *,
        feed_targets: dict[str, Sequence[str]] | None = None,
        item_categories: dict[str, str] | None = None,
        item_names: list[str] | None = None,
    ) -> None:
        """Replace RSS feed targets at runtime.

        Category item changes must not require an app restart.  The owning
        scheduler recomputes category watch policies and calls this method with
        the new provider feed set.
        """
        self._feed_urls = list(feed_urls or [])
        if item_names is not None:
            self.update_items(item_names, item_categories=item_categories)
        elif item_categories is not None:
            self.update_items([self._item_display_names.get(name, name) for name in self._item_names], item_categories=item_categories)
        self._feed_targets = {
            str(url): [str(name).lower() for name in names if str(name).strip()]
            for url, names in (feed_targets or {}).items()
        }
        self._poll_cursor = 0
        logger.info(
            "RSS monitor feed set updated: %s feeds, %s tracked item names",
            len(self._feed_urls), len(self._item_names),
        )

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
        """Fetch a bounded rotating slice of RSS feeds and process new items.

        Earlier versions polled Jackett's ``/all`` RSS endpoint with an empty
        query, then substring-matched every returned release against every
        tracked show. That produced false positives (for example a title ending
        in "Beyond the Wire" matching the show "The Wire") and heavy event-loop
        stalls. Feeds are now expected to be item-scoped, and matching is limited
        to the names associated with the feed being polled.
        """
        urls = self._feeds_for_cycle()
        for url in urls:
            try:
                # Check for cancellation between feeds
                await asyncio.sleep(0)
                items = await self._fetch_feed(url)
                target_names = self._feed_targets.get(url)
                matched = self._match_items(items, candidate_names=target_names)
                for item_name, result, unit_label in matched:
                    logger.info(
                        f"RSS match: '{result.title}' -> item '{item_name}' (unit={unit_label})"
                    )
                    if self._on_match:
                        try:
                            await asyncio.wait_for(
                                self._on_match(item_name, result, unit_label=unit_label), timeout=60,
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

    def _feeds_for_cycle(self) -> list[str]:
        """Return a rotating bounded feed slice for the current poll cycle."""
        feeds = list(self._feed_urls)
        if len(feeds) <= self._max_feeds_per_cycle:
            return feeds
        start = self._poll_cursor % len(feeds)
        end = start + self._max_feeds_per_cycle
        selected = feeds[start:end]
        if len(selected) < self._max_feeds_per_cycle:
            selected.extend(feeds[: self._max_feeds_per_cycle - len(selected)])
        self._poll_cursor = (start + self._max_feeds_per_cycle) % len(feeds)
        logger.debug(
            f"RSS monitor polling bounded feed slice: {len(selected)}/{len(feeds)} feeds (cursor={self._poll_cursor})"
        )
        return selected

    def _log_fetch_failure(self, url: str, exc: Exception) -> None:
        """Log repeated RSS fetch failures without flooding the main log."""
        safe_url = redact_url(url)
        detail = repr(exc) if not str(exc) else str(exc)
        key = safe_url
        now = asyncio.get_event_loop().time()
        previous = self._fetch_error_log_state.get(key)
        if previous is None or previous[0] != detail:
            logger.warning(f"RSS fetch failed for {safe_url}: {detail}")
            self._fetch_error_log_state[key] = (detail, now)
            return
        if now - previous[1] >= 1800.0:
            logger.debug(f"RSS fetch still failing for {safe_url}: {detail}")
            self._fetch_error_log_state[key] = (detail, now)

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
            self._log_fetch_failure(url, e)
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

    def _match_items(self, items: list[SearchResult], candidate_names: Sequence[str] | None = None) -> list[tuple[str, SearchResult, str | None]]:
        """Match RSS items against tracked item names.

        Returns list of (item_name, result, unit_label) tuples for matches that
        haven't been seen before. ``unit_label`` is category-owned and may be
        absent when the feed item cannot be mapped to a concrete unit.
        """
        matched = []
        names = [str(name).lower() for name in (candidate_names or self._item_names) if str(name).strip()]
        for item in items:
            magnet_key = item.magnet or item.title
            if magnet_key in self._seen_magnets:
                continue

            for name in names:
                category_id = self._item_categories.get(name, "")
                parsed_name, unit_label = self._parse_match_candidate(item.title, category_id)
                if self._is_item_match(name, parsed_name, item.title, bool(category_id)):
                    self._seen_magnets.add(magnet_key)
                    original_name = self._item_display_names.get(name, name)
                    matched.append((original_name, item, unit_label))
                    break

        return matched

    def _is_item_match(self, name: str, parsed_name: str, raw_title: str, category_scoped: bool) -> bool:
        """Return whether a parsed RSS title belongs to a tracked item.

        Category-scoped feeds must match the parser's extracted title, not an
        arbitrary substring in the full release title. This avoids false
        positives such as "Wicked Attraction ... Beyond the Wire" matching the
        show "The Wire".
        """
        if category_scoped:
            return self._canonical_title(parsed_name) == self._canonical_title(name)
        return re.search(r'\b' + re.escape(name) + r'\b', raw_title.lower()) is not None

    @staticmethod
    def _canonical_title(value: str) -> str:
        text = re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()
        if text.startswith("the "):
            text = text[4:]
        return re.sub(r"\s+", " ", text)

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
