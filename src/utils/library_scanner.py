"""
Library scanner for LJS.

Delegates scanning to registered MediaCategory classes (TV, Movies, etc.).
Provides a unified interface for full-library synchronization.
"""

from typing import TYPE_CHECKING

from src.core.categories.registry import CategoryRegistry
from src.core.categories.identity import canonical_item_key, clean_category_item_name
from src.core.categories.types import ScannedItem
from src.core.models import LibraryScanResult, ScannedLibraryItem, ScannedMediaFile, Settings

if TYPE_CHECKING:
    from src.llm_providers.client import LLMClient


class LibraryScanner:
    """Orchestrates library scanning by delegating to media categories."""

    def __init__(self, registry: CategoryRegistry | None = None):
        self._registry = registry or CategoryRegistry()
        if not self._registry.list_ids():
            self._registry.register_defaults()
        self._llm_client = None

    def set_llm_client(self, llm_client: "LLMClient") -> None:
        """Set LLM client for categories that support it."""
        self._llm_client = llm_client

    async def full_scan(self, settings: Settings) -> LibraryScanResult:
        """Perform a full library scan across all registered categories."""
        items: list[ScannedLibraryItem] = []

        existing_keys = {item.key for item in settings.tracked_items}

        for category in self._registry.list_all():
            root_path = category.get_root_path(settings)
            scanned_items = await category.scan(root_path, existing_keys=existing_keys)

            for item in scanned_items:
                items.append(self._to_library_item(item, category))

        return self._build_result(items)

    async def item_scan(
        self,
        settings: Settings,
        *,
        category_id: str,
        item_id: str,
        changed_path: str | None = None,
    ) -> LibraryScanResult:
        """Scan one category item, used after a managed library mutation.

        Download completion and per-item UI refreshes should not trigger a full
        cross-category crawl.  The scanner still delegates physical structure to
        the owning category: categories that can identify an item folder expose
        ``scan_item``; other categories fall back to a category scan filtered by
        canonical item identity.
        """
        category = self._registry.get(category_id)
        if category is None:
            return LibraryScanResult(items=[], total_files=0, total_size_bytes=0)

        existing_keys = {item.key for item in settings.tracked_items}
        root_path = category.get_root_path(settings)
        if hasattr(category, "scan_item"):
            scanned_items = await category.scan_item(
                root_path,
                item_id=item_id,
                existing_keys=existing_keys,
                changed_path=changed_path,
            )
        else:
            wanted = canonical_item_key(clean_category_item_name(item_id, category_id))
            scanned_items = [
                item
                for item in await category.scan(root_path, existing_keys=existing_keys)
                if canonical_item_key(clean_category_item_name(item.name, category_id)) == wanted
            ]

        return self._build_result([self._to_library_item(item, category) for item in scanned_items])

    def _build_result(self, items: list[ScannedLibraryItem]) -> LibraryScanResult:
        """Return a merged LibraryScanResult for already converted scan rows."""
        merged = self._merge_duplicate_scan_items(items)
        return LibraryScanResult(
            items=merged,
            total_files=sum(item.file_count for item in merged),
            total_size_bytes=sum(item.total_size_bytes for item in merged),
        )


    @staticmethod
    def _merge_duplicate_scan_items(items: list[ScannedLibraryItem]) -> list[ScannedLibraryItem]:
        """Merge duplicate scan rows created by cleaned folder aliases."""
        merged: dict[tuple[str, str], ScannedLibraryItem] = {}
        for item in items:
            clean_name = clean_category_item_name(item.name, item.category_id)
            key = (item.category_id, canonical_item_key(clean_name))
            existing = merged.get(key)
            if not existing:
                item.name = clean_name
                merged[key] = item
                continue
            existing.files.extend(item.files)
            for season, episodes in (item.episodes or {}).items():
                values = set(existing.episodes.get(season, []))
                values.update(episodes or [])
                existing.episodes[season] = sorted(values)
            existing.seasons = len(existing.episodes)
            existing.file_count += item.file_count
            existing.total_size_bytes += item.total_size_bytes
            existing.avg_file_size_mb = round(existing.total_size_bytes / existing.file_count / 1024 / 1024, 1) if existing.file_count else 0
            existing.resolutions = sorted(set(existing.resolutions) | set(item.resolutions))
            existing.codecs = sorted(set(existing.codecs) | set(item.codecs))
            if existing.avg_bitrate_kbps and item.avg_bitrate_kbps:
                existing.avg_bitrate_kbps = int(
                    (
                        (existing.avg_bitrate_kbps * max(existing.file_count - item.file_count, 0))
                        + (item.avg_bitrate_kbps * item.file_count)
                    ) / max(existing.file_count, 1)
                )
            elif item.avg_bitrate_kbps:
                existing.avg_bitrate_kbps = item.avg_bitrate_kbps
            if not existing.year and item.year:
                existing.year = item.year
        return list(merged.values())

    @staticmethod
    def _to_library_item(item: ScannedItem, category: object | None = None) -> ScannedLibraryItem:
        """Convert a category scan dataclass into the public scan model."""
        avg_size = round(item.total_size_bytes / item.file_count / 1024 / 1024, 1) if item.file_count else 0
        avg_bitrate = None
        if category and hasattr(category, "scan_average_bitrate_kbps"):
            avg_bitrate = category.scan_average_bitrate_kbps(item)
        files = [
            ScannedMediaFile(
                season=episode.season,
                episode=episode.episode,
                file_path=episode.file_path,
                quality=episode.quality,
                size_bytes=episode.size_bytes,
                detected_language=getattr(episode, "detected_language", "") or "",
                audio_languages=list(getattr(episode, "audio_languages", []) or []),
                audio_tracks=list(getattr(episode, "audio_tracks", []) or []),
                subtitle_languages=list(getattr(episode, "subtitle_languages", []) or []),
                subtitle_tracks=list(getattr(episode, "subtitle_tracks", []) or []),
                media_probe=dict(getattr(episode, "media_probe", {}) or {}),
                local_object=dict(getattr(episode, "local_object", {}) or {}),
            )
            for episode in item.detailed_episodes
        ]
        return ScannedLibraryItem(
            name=item.name,
            category_id=item.category_id,
            files=files,
            episodes=item.episodes,
            seasons=item.seasons,
            file_count=item.file_count,
            total_size_bytes=item.total_size_bytes,
            avg_file_size_mb=avg_size,
            avg_bitrate_kbps=avg_bitrate,
            codecs=item.codecs,
            resolutions=item.resolutions,
            detected_language=item.detected_language,
            detected_languages=list(getattr(item, "detected_languages", []) or []),
            subtitle_languages=list(getattr(item, "subtitle_languages", []) or []),
            year=item.year,
            local_object_model=dict(getattr(item, "local_object_model", {}) or {}),
        )
