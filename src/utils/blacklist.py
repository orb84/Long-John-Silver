"""
Blacklist management for LJS.

Filters out unwanted release groups, codecs, or patterns from search results.
"""

import re
from loguru import logger
from typing import Optional
from src.core.models import BlacklistEntry, SearchResult
from src.core.database import Database


class BlacklistManager:
    """Manages the blacklist of patterns to exclude from search results."""

    def __init__(self, db: Database):
        self._db = db
        self._cache: list[BlacklistEntry] = []
        self._compiled: list[re.Pattern] = []

    async def initialize(self) -> None:
        """Load blacklist from database and compile regex patterns."""
        self._cache = await self._db.downloads.get_blacklist()
        self._compiled = [re.compile(e.pattern, re.IGNORECASE) for e in self._cache]
        logger.info(f"Blacklist loaded: {len(self._cache)} patterns.")

    async def add(self, pattern: str, reason: str = "") -> BlacklistEntry:
        """Add a pattern to the blacklist and compile it."""
        entry = BlacklistEntry(pattern=pattern, reason=reason)
        await self._db.downloads.add_blacklist_entry(entry)
        self._cache.append(entry)
        self._compiled.append(re.compile(pattern, re.IGNORECASE))
        logger.info(f"Blacklisted pattern: {pattern}")
        return entry

    async def remove(self, pattern: str) -> None:
        """Remove a pattern from the blacklist."""
        await self._db.downloads.remove_blacklist_entry(pattern)
        self._cache = [e for e in self._cache if e.pattern != pattern]
        self._compiled = [re.compile(e.pattern, re.IGNORECASE) for e in self._cache]
        logger.info(f"Removed blacklist pattern: {pattern}")

    def is_blacklisted(self, title: str) -> Optional[BlacklistEntry]:
        """Check if a title matches any blacklisted pattern."""
        for i, compiled in enumerate(self._compiled):
            if compiled.search(title):
                return self._cache[i]
        return None

    def filter_results(self, results: list[SearchResult]) -> list[SearchResult]:
        """Remove blacklisted results from a list of search results."""
        filtered = []
        for result in results:
            entry = self.is_blacklisted(result.title)
            if entry:
                logger.debug(f"Blacklisted: {result.title} (matched: {entry.pattern})")
            else:
                filtered.append(result)
        return filtered

    def get_all(self) -> list[BlacklistEntry]:
        """Return all current blacklist entries."""
        return list(self._cache)