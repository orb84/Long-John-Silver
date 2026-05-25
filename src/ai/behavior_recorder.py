"""
Behavior recorder for LJS implicit preference learning.

Records user actions (downloads, rejections, searches) to the database
so the behavior tracker can aggregate them into behavioral profiles.
AIAssistant delegates recording to this class.
"""

from typing import Any, Optional

from loguru import logger

from src.core.database import Database


class BehaviorRecorder:
    """Records user behavior events for implicit preference learning.

    Owns the recording side of behavior tracking. The companion
    BehaviorTracker class aggregates recorded events into profiles.
    This split keeps recording concerns separate from analysis.
    """

    def __init__(self, db: Optional[Database] = None) -> None:
        """Initialize with optional database dependency.

        Args:
            db: Database instance for persisting behavior events.
                 If None, all record calls are no-ops.
        """
        self._db = db

    async def record_download(
        self, user_id: str, item_name: str,
        resolution: Optional[str] = None,
        codec: Optional[str] = None,
        release_group: Optional[str] = None,
        file_size_mb: Optional[float] = None,
        quality_score: Optional[float] = None,
    ) -> None:
        """Record a download action by the user.

        Args:
            user_id: The user who performed the download.
            item_name: Name of the category item downloaded.
            resolution: Video resolution (e.g. '1080p').
            codec: Video codec (e.g. 'h265').
            release_group: Release group name.
            file_size_mb: File size in megabytes.
            quality_score: Inferred quality score.
        """
        if not self._db:
            return
        await self._record(user_id, 'download', item_name=item_name,
                          resolution=resolution, codec=codec,
                          release_group=release_group,
                          file_size_mb=file_size_mb,
                          quality_score=quality_score)

    async def record_reject(
        self, user_id: str, item_name: str,
        resolution: Optional[str] = None,
        codec: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        """Record a rejection action by the user.

        Args:
            user_id: The user who rejected the content.
            item_name: Name of the category item rejected.
            resolution: Video resolution that was rejected.
            codec: Video codec that was rejected.
            reason: Why the content was rejected.
        """
        if not self._db:
            return
        await self._record(user_id, 'reject', item_name=item_name,
                          resolution=resolution, codec=codec,
                          reason=reason)

    async def record_search(
        self, user_id: str, query: str,
        result_count: Optional[int] = None,
    ) -> None:
        """Record a search action by the user.

        Args:
            user_id: The user who performed the search.
            query: The search query string.
            result_count: Number of results returned.
        """
        if not self._db:
            return
        await self._record(user_id, 'search', query=query,
                          result_count=result_count)

    async def record_action(
        self, user_id: str, action: str,
        item_name: Optional[str] = None,
        reason: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        """Record a generic user action for behavior learning.

        Flexible method for recording preference-revealing actions that
        do not neatly fit into download/reject/search categories.  The
        *action* string is stored as-is and can represent any UI action
        name (e.g. ``category_item_pause``, ``settings_update_quality``).

        Args:
            user_id: The user who performed the action.
            action:  Arbitrary action identifier stored in the behavior log.
            item_name: Optional item name the action relates to.
            reason:   Optional reason for the action.
        """
        if not self._db:
            return
        await self._record(user_id, action, item_name=item_name, reason=reason, **kwargs)

    async def _record(self, user_id: str, action: str, **kwargs) -> None:
        """Internal: persist a behavior event to the database.

        Args:
            user_id: The user who performed the action.
            action: Action type identifier (e.g. 'download', 'reject', 'search').
            **kwargs: Optional event details passed through to the DB.
        """
        await self._db.system.log_behavior(user_id, action, **kwargs)
        logger.debug(
            f"Behavior recorded: user={user_id} action={action} "
            f"details={kwargs}"
        )
