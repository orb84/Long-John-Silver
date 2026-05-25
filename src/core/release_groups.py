"""
Release group reputation tracker for LJS.

Tracks the success and failure rates of torrent release groups based
on download outcomes. High-reputation groups are prioritized in search
results; low-reputation or blacklisted groups are penalized or filtered.
This creates a feedback loop: better groups rise, bad groups sink.
"""

import re
from loguru import logger
from typing import Optional, TYPE_CHECKING
from src.core.database import Database

if TYPE_CHECKING:
    from src.utils.blacklist import BlacklistManager


_RELEASE_GROUP_RE = re.compile(r'[\-\[](?P<release_group>[A-Za-z0-9]+)$')


class ReleaseGroupTracker:
    """Tracks download success rates per release group and scores them.

    After each download completes or fails, the release group is extracted
    from the torrent title and its stats are updated. Search results from
    high-reputation groups get a quality boost, while blacklisted groups
    are filtered out entirely.

    Caches the blacklist and reputation data to avoid N+1 queries
    when checking multiple results in a single search.
    """

    def __init__(self, db: Database, blacklist_manager: "BlacklistManager | None" = None) -> None:
        self._db = db
        self._blacklist_manager = blacklist_manager
        self._blacklist_cache: Optional[list[str]] = None
        self._reputation_cache: dict[str, float] = {}

    async def _is_blacklisted(self, group_name: str) -> bool:
        """Check if a release group is blacklisted.

        Uses BlacklistManager if available (queries the blacklist table),
        otherwise falls back to the release_groups table blacklisted flag.
        """
        if self._blacklist_manager is not None:
            matched = self._blacklist_manager.is_blacklisted(group_name)
            return matched is not None
        # Fallback: use release_groups table blacklisted flag
        blacklisted = await self._ensure_blacklist_cache()
        return group_name in blacklisted

    async def _ensure_blacklist_cache(self) -> list[str]:
        """Load and cache the blacklisted release group names from release_groups table.

        Only used as a fallback when no BlacklistManager is provided.
        """
        if self._blacklist_cache is None:
            self._blacklist_cache = await self._db.downloads.get_blacklisted_release_groups()
        return self._blacklist_cache

    def invalidate_cache(self) -> None:
        """Clear cached data so it's re-fetched on next access.

        Call this after modifying blacklist entries or release group data.
        """
        self._blacklist_cache = None
        self._reputation_cache = {}

    async def record_outcome(self, torrent_title: str, success: bool,
                              quality_score: float | None = None) -> None:
        """Record the outcome of a download for a release group.

        Extracts the release group from the torrent title and updates
        its reputation statistics in the database.

        Args:
            torrent_title: The full torrent title string.
            success: Whether the download completed successfully.
            quality_score: The quality score of the download, if known.
        """
        group = self._extract_release_group(torrent_title)
        if not group:
            return

        await self._db.downloads.update_release_group(group, success, quality_score)
        self._reputation_cache.pop(group, None)  # Invalidate stale cache
        status = "succeeded" if success else "failed"
        logger.info(f"Release group '{group}' download {status} — reputation updated")

    async def get_reputation(self, group_name: str) -> float:
        """Get the reputation score for a release group.

        Returns a score from -1.0 (consistently bad) to 1.0 (consistently good).
        Groups with no data return 0.0 (neutral). Results are cached per
        instance to avoid repeated DB queries during a single search.

        Args:
            group_name: The release group name (e.g., "SPARKS", "YTS").

        Returns:
            Float reputation score in [-1.0, 1.0].
        """
        if group_name in self._reputation_cache:
            return self._reputation_cache[group_name]

        data = await self._db.downloads.get_release_group(group_name)
        if not data:
            self._reputation_cache[group_name] = 0.0
            return 0.0

        total = data.get("download_count", 0)
        success = data.get("success_count", 0)

        if total == 0:
            self._reputation_cache[group_name] = 0.0
            return 0.0

        # Reputation ranges from -1.0 (all failures) to 1.0 (all successes)
        # With a small-sample penalty that makes new groups start near 0
        confidence = min(total / 10.0, 1.0)  # Full confidence after 10 downloads
        raw_score = (success / total) * 2.0 - 1.0  # Map [0, 1] -> [-1, 1]

        result = raw_score * confidence
        self._reputation_cache[group_name] = result
        return result

    async def get_reputation_boost(self, torrent_title: str) -> float:
        """Get a quality score adjustment for a torrent based on its release group.

        Intended to be added to the quality score in the search aggregator.
        Groups with strong reputations get a boost of up to 0.1, while
        groups with poor reputations get a penalty of up to -0.2.

        Args:
            torrent_title: The torrent title to extract the release group from.

        Returns:
            Float adjustment to add to the quality score.
        """
        group = self._extract_release_group(torrent_title)
        if not group:
            return 0.0

        # Check blacklist first (via BlacklistManager or DB fallback)
        if await self._is_blacklisted(group):
            return -0.3  # Strong penalty for blacklisted groups

        reputation = await self.get_reputation(group)

        # Scale reputation to a quality adjustment
        # Positive reputation: small boost (max +0.1)
        # Negative reputation: larger penalty (max -0.2)
        if reputation >= 0:
            return reputation * 0.1
        else:
            return reputation * 0.2

    @staticmethod
    def _extract_release_group(title: str) -> str | None:
        """Extract release group from a torrent title using inline regex.

        Args:
            title: The torrent title string.

        Returns:
            The release group name, or None if not found.
        """
        cleaned = title.replace('.', ' ').replace('_', ' ').strip()
        m = _RELEASE_GROUP_RE.search(cleaned)
        return m.group('release_group') if m else None

    async def is_blacklisted(self, group_name: str) -> bool:
        """Check if a release group is blacklisted."""
        return await self._is_blacklisted(group_name)