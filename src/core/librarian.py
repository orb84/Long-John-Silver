"""
Librarian module for LJS.

Thin dispatcher that routes organization, verification, and consolidation
to the appropriate MediaCategory. All category-specific logic lives in
the category classes under src/core/categories/.
"""

from pathlib import Path
from loguru import logger
from typing import Optional
from src.core.models import Settings
from src.core.categories.registry import CategoryRegistry
from src.core.security.command_policy import CommandPolicy


class Librarian:
    """Thin dispatcher to category-based file organization.

    Delegates to the appropriate MediaCategory for all operations.
    Adding a new media type means registering a new category with the
    registry — no changes needed here.
    """

    def __init__(self, settings: Settings, registry: Optional[CategoryRegistry] = None):
        self._settings = settings
        self._registry = registry or CategoryRegistry()
        logger.info('Librarian initialized.')

    def _get_category(self, category_id: str = ''):
        """Look up a category by ID. Falls back to classify() if unknown."""
        if category_id:
            cat = self._registry.get(category_id)
            if cat:
                return cat
        return None

    async def verify_media(self, file_path: Path, category_id: str = '') -> bool:
        """Verify a file is valid media using async ffprobe."""
        cat = self._get_category(category_id) or next(iter(self._registry.list_all()), None)
        if cat:
            return await cat.verify_media(file_path)
        return self._fallback_verify(file_path)

    @staticmethod
    def _fallback_verify(file_path: Path) -> bool:
        """Minimal ffprobe check when no category is available."""
        import json
        try:
            result = CommandPolicy().run_sync(
                ['ffprobe', '-v', 'quiet', '-print_format', 'json',
                 '-show_format', '-show_streams', str(file_path)],
                purpose='librarian.ffprobe', capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return False
            probe = json.loads(result.stdout)
            video = next((s for s in probe.get('streams', []) if s.get('codec_type') == 'video'), None)
            if not video:
                return False
            duration = float(probe.get('format', {}).get('duration', 0))
            return duration >= 60
        except Exception:
            return False

    def organize_file(self, source: Path, item_name: str = '',
                       season: int | None = None, episode: int | None = None,
                       episode_title: str | None = None,
                       year: int | None = None, is_movie: bool = False,
                       release_group: str | None = None,
                       is_anime: bool = False,
                       category_id: str = '') -> Optional[Path]:
        """Organize a file into the library via category dispatch."""
        if not category_id:
            result = self._registry.classify(source.name)
            if result:
                category_id = result[0].category_id

        cat = self._registry.get(category_id) if category_id else None

        if not cat:
            logger.warning(f'No registered category found for {source.name}; skipping organization')
            return None

        metadata = {
            'item_name': item_name,
            'season': season,
            'episode': episode,
            'episode_title': episode_title or '',
            'year': year,
            'release_group': release_group,
            'is_anime': is_anime,
        }

        # Let the category decide — it knows its own naming template and path
        result = cat.organize(source, self._settings, metadata)
        if result:
            return Path(result)
        return None

    async def consolidate_library(self, dry_run: bool = True) -> list[dict]:
        """Consolidate each registered category's library directory.

        Each category walks its own root path and renames files
        to match its current naming template.
        """
        results = []
        for cat in self._registry.list_all():
            root = cat.get_root_path(self._settings)
            if Path(root).exists():
                cat_results = cat.consolidate(root, dry_run=dry_run, settings=self._settings)
                results.extend(cat_results)
                logger.info(f'{cat.display_name}: {len(cat_results)} files processed')
        return results
