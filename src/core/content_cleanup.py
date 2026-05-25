"""Content cleanup for LJS.

Deletes user-approved or integration-reported library files through category
contracts.  The cleanup service is intentionally category-neutral: Plex and
other integrations provide external media-type strings, and each registered
category decides whether those strings belong to it.
"""

from pathlib import Path
from loguru import logger

from src.core.config import SettingsManager
from src.core.database import Database
from src.core.categories.registry import CategoryRegistry
from src.core.models import WatchedItem
from src.core.notifications import NotificationService
from src.integrations.plex import PlexClient


class ContentCleanup:
    """Deletes watched media files from the library on request.

    Can operate in two modes:
    1. Manual: User asks 'delete Severance S01E01' and the file is located
       and removed.
    2. Plex-driven: If Plex is configured, automatically finds watched items
       and deletes them (only when auto_delete_watched is enabled in settings).
    """

    def __init__(self, settings_manager: SettingsManager, db: Database,
                 notifications: NotificationService,
                 plex_client: PlexClient | None = None,
                 category_registry: CategoryRegistry | None = None):
        self._settings_manager = settings_manager
        self._db = db
        self._notifications = notifications
        self._plex = plex_client
        self._categories = category_registry or CategoryRegistry.with_defaults()

    async def delete_item(
        self,
        category_id: str,
        name: str,
        season: int | None = None,
        episode: int | None = None,
        year: int | None = None,
    ) -> str | None:
        """Delete one item through the registered category implementation."""
        category = self._categories.get(category_id)
        if category is None:
            return f"Category not found: {category_id}"

        matches = await self._find_matching_files(category_id, name, season, episode, year)
        deleted = category.delete(name, self._settings_manager.settings, season, episode, year)
        if not deleted:
            return f"Library file not found for '{name}' in category '{category_id}'"

        await self._record_deletion(category_id, name, matches, season, episode)
        file_names = ", ".join(Path(match["path"]).name for match in matches) or name
        return f"Deleted {name}: {file_names}"

    async def get_watched_items_from_plex(self) -> list[WatchedItem]:
        """Fetch watched items from Plex if configured.

        Returns:
            List of WatchedItem objects for content that has been
            fully watched according to Plex.
        """
        if self._plex is None:
            return []

        try:
            return await self._plex.get_watched_items()
        except Exception as e:
            logger.warning(f"Failed to fetch watched items from Plex: {e}")
            return []

    async def auto_cleanup_watched(self) -> list[str]:
        """Delete all watched items if auto_delete_watched is enabled.

        Only runs when both Plex is configured and the setting is on.
        Does NOT delete by default — must be explicitly enabled.

        Returns:
            List of deletion status messages.
        """
        settings = self._settings_manager.settings
        if not settings.auto_delete_watched:
            logger.debug("Auto-delete watched is disabled, skipping cleanup")
            return []

        watched = await self.get_watched_items_from_plex()
        if not watched:
            return []

        results = []
        for item in watched:
            category_id = self._category_id_for_watched_item(item.media_type)
            if not category_id:
                continue
            result = await self.delete_item(
                category_id,
                item.title,
                season=item.season,
                episode=item.episode,
                year=item.year,
            )
            results.append(result)

        if results:
            await self._notifications.send_message(
                f"Auto-deleted {len(results)} watched item(s):\n"
                + "\n".join(f"  - {r}" for r in results),
                title="Auto Cleanup",
            )

        return results

    async def list_library_files(self, media_type: str = "all",
                                  name_filter: str | None = None) -> list[dict]:
        """List files in the library for the AI assistant to reference.

        Args:
            media_type: Category ID to list, or 'all' for every category.
            name_filter: Optional case-insensitive filter by show or movie name.

        Returns:
            Category-owned file dictionaries. Common keys include name,
            category_id, path, size_mb, and quality; categories may add their
            own selector fields.
        """
        results = []
        for category_id in self._category_ids_for(media_type):
            category = self._categories.get(category_id)
            if category is None:
                continue
            root = category.get_root_path(self._settings_manager.settings)
            for item in await category.scan(root):
                if name_filter and name_filter.lower() not in item.name.lower():
                    continue
                results.extend(category.library_file_records_from_scan(item))
        return results

    def _category_id_for_watched_item(self, media_type: str) -> str | None:
        """Resolve a Plex watched item by asking registered categories."""
        for category_id in self._categories.list_ids():
            category = self._categories.get(category_id)
            if category and category.matches_external_media_type("plex", media_type):
                return category_id
        return None

    def _category_ids_for(self, media_type: str) -> list[str]:
        """Resolve a category filter into concrete registry category IDs."""
        if media_type == "all":
            return self._categories.list_ids()
        return [media_type]

    async def _find_matching_files(
        self,
        category_id: str,
        name: str,
        season: int | None,
        episode: int | None,
        year: int | None = None,
    ) -> list[dict]:
        """Find files that match a pending deletion for logging."""
        files = await self.list_library_files(category_id, name_filter=name)
        category = self._categories.get(category_id)
        if category is None:
            return []
        return [
            file_info for file_info in files
            if category.file_record_matches_selector(file_info, season=season, episode=episode, year=year)
        ]

    async def _record_deletion(
        self,
        category_id: str,
        name: str,
        matches: list[dict],
        season: int | None,
        episode: int | None,
    ) -> None:
        """Persist a deletion audit row using category-neutral metadata."""
        await self._db.system.add_deletion_log(
            title=", ".join(Path(match["path"]).name for match in matches) or name,
            media_type=category_id,
            item_name=name,
            season=season,
            episode=episode,
            file_path=", ".join(match["path"] for match in matches),
        )
