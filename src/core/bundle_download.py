"""Generic torrent bundle handling for LJS.

A bundle is any torrent payload that may contain more than the exact requested
category item or unit: TV season packs, movie collections, game bundles, book
anthologies, discographies, DLC bundles, and similar releases.  This module is
category-neutral.  It waits for torrent metadata, asks the owning category to
identify files, and applies libtorrent priorities so only useful files are
fetched when the category can make that decision.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from loguru import logger

from src.core.categories.registry import CategoryRegistry


class BundleDownloadHandler:
    """Configure selective downloads for category-owned torrent bundles.

    The handler does not know what a season, movie collection, game version, or
    book volume is.  Categories provide descriptors for the target and for each
    file inside the torrent; this class only compares through category hooks and
    writes libtorrent priorities.
    """

    def __init__(self, category_registry: CategoryRegistry | None = None) -> None:
        """Initialize bundle tracking with the available category registry."""
        self._active_bundle_downloads: dict[str, dict[str, Any]] = {}
        self._categories = category_registry or CategoryRegistry.with_defaults()

    def describe_candidate(
        self,
        title: str,
        *,
        category_id: str = "",
        result: Any | None = None,
        item: Any | None = None,
        unit_label: str | None = None,
    ) -> dict[str, Any] | None:
        """Return category-owned bundle hints for a torrent title/result."""
        category = self._category(category_id)
        if category and hasattr(category, "torrent_bundle_candidate_context"):
            try:
                probe = result or type("BundleProbe", (), {"title": title})()
                return category.torrent_bundle_candidate_context(probe, item=item, unit_label=unit_label)
            except Exception as exc:
                logger.debug(f"Bundle candidate context failed for {category_id}:{title}: {exc}")
        return None

    def compute_per_unit_limit_mb(
        self,
        total_size_bytes: int,
        title: str,
        *,
        category_id: str = "",
        profile_max_mb: int | None = None,
        unit_count: int | None = None,
        bundle_context: dict[str, Any] | None = None,
        target_descriptor: dict[str, Any] | None = None,
    ) -> int | None:
        """Return the per-useful-unit/file limit for a bundle candidate.

        Explicit user/profile limits are already expressed per useful payload
        file/unit and therefore pass through unchanged.  Without an explicit
        limit, the owning category estimates useful unit size; otherwise we fall
        back to total-size divided by explicit unit count.
        """
        if profile_max_mb is not None:
            return profile_max_mb
        if not total_size_bytes or total_size_bytes <= 0:
            return None
        category = self._category(category_id)
        context = dict(bundle_context or {})
        if unit_count and "unit_count" not in context:
            context["unit_count"] = unit_count
        if category and hasattr(category, "estimate_bundle_unit_size_mb"):
            try:
                estimate = category.estimate_bundle_unit_size_mb(
                    total_size_bytes=total_size_bytes,
                    title=title,
                    bundle_context=context,
                    target_descriptor=target_descriptor,
                )
                return int(estimate) if estimate and estimate > 0 else None
            except Exception as exc:
                logger.debug(f"Bundle unit-size estimate failed for {category_id}:{title}: {exc}")
        count = int(unit_count or context.get("unit_count") or 0)
        total_mb = total_size_bytes / (1024 * 1024)
        return int(total_mb / count) if count > 0 else int(total_mb)

    async def configure_selective_download(
        self,
        download_id: str,
        handle: object,
        *,
        category_id: str = "",
        target_descriptors: list[dict[str, Any]] | None = None,
        target_descriptor: dict[str, Any] | None = None,
        target_episodes: list[int] | None = None,
        target_season: int | None = None,
    ) -> bool:
        """Configure libtorrent file priorities for a category bundle.

        ``target_episodes`` and ``target_season`` are accepted only as a bridge
        for callers still using the old public queue parameters.  They are
        converted through ``category.unit_descriptor_from_agent_args`` and are
        never interpreted in this generic class.
        """
        targets = self._normalize_targets(
            category_id=category_id,
            target_descriptors=target_descriptors,
            target_descriptor=target_descriptor,
            target_episodes=target_episodes,
            target_season=target_season,
        )
        if not targets:
            return False

        try:
            # Import lazily so development environments without libtorrent can
            # still import and test the rest of the project.
            import libtorrent as lt  # noqa: F401

            for _ in range(30):
                if handle.has_metadata():
                    break
                await asyncio.sleep(1)
            else:
                logger.warning(f"Download {download_id}: metadata not received, cannot configure selective bundle download")
                return False

            torrent_info = handle.torrent_file()
            if not torrent_info:
                return False

            category = self._category(category_id)
            num_files = torrent_info.num_files()
            priorities = [0] * num_files
            matched: list[dict[str, Any]] = []

            for index in range(num_files):
                file_path = torrent_info.files().file_path(index)
                parsed = self._parse_file(category, file_path)
                descriptor = self._file_descriptor(category, file_path, parsed)
                selected = self._file_matches(category, file_path, parsed, descriptor, targets)
                priority = self._file_priority(category, file_path, parsed, descriptor, selected)
                priorities[index] = max(0, min(7, int(priority)))
                if priorities[index] > 0:
                    matched.append({
                        "file_index": index,
                        "file_path": file_path,
                        "priority": priorities[index],
                        "unit_descriptor": descriptor,
                    })

            if not matched:
                logger.warning(
                    f"Download {download_id}: no files matched selective bundle target(s) "
                    f"for category={category_id or 'unknown'}"
                )
                return False

            handle.prioritize_files(priorities)
            self._active_bundle_downloads[download_id] = {
                "category_id": category_id,
                "target_descriptors": targets,
                "matched_files": matched,
                "total_files": num_files,
            }
            logger.info(
                f"Selective bundle download configured for {download_id}: "
                f"{len(matched)}/{num_files} file(s) selected"
            )
            return True
        except Exception as exc:
            logger.error(f"Failed to configure selective bundle download for {download_id}: {exc}")
            return False

    def clear_bundle_download(self, download_id: str) -> None:
        """Remove tracking for a completed/cancelled bundle download."""
        self._active_bundle_downloads.pop(download_id, None)

    def get_bundle_download_info(self, download_id: str) -> dict[str, Any] | None:
        """Return info about an active selective bundle download."""
        return self._active_bundle_downloads.get(download_id)

    # ── Internals ─────────────────────────────────────────────────

    def _category(self, category_id: str) -> Any | None:
        if not category_id:
            return None
        return self._categories.get(category_id)

    def _normalize_targets(
        self,
        *,
        category_id: str,
        target_descriptors: list[dict[str, Any]] | None,
        target_descriptor: dict[str, Any] | None,
        target_episodes: list[int] | None,
        target_season: int | None,
    ) -> list[dict[str, Any]]:
        targets: list[dict[str, Any]] = []
        for descriptor in target_descriptors or []:
            if isinstance(descriptor, dict) and descriptor:
                targets.append(dict(descriptor))
        if isinstance(target_descriptor, dict) and target_descriptor:
            targets.append(dict(target_descriptor))
        if (target_episodes or target_season is not None) and not targets:
            category = self._category(category_id)
            if category and hasattr(category, "unit_descriptor_from_agent_args"):
                if target_episodes:
                    for episode in target_episodes:
                        targets.append(category.unit_descriptor_from_agent_args(season=target_season, episode=episode))
                else:
                    targets.append(category.unit_descriptor_from_agent_args(season=target_season, episode=None))
        clean_targets: list[dict[str, Any]] = []
        for descriptor in targets:
            if descriptor and descriptor not in clean_targets:
                clean_targets.append(descriptor)
        return clean_targets

    @staticmethod
    def _parse_file(category: Any | None, file_path: str) -> Any | None:
        if not category or not hasattr(category, "parse_name"):
            return None
        try:
            return category.parse_name(Path(file_path).stem)
        except Exception:
            return None

    @staticmethod
    def _file_descriptor(category: Any | None, file_path: str, parsed: Any | None) -> dict[str, Any]:
        if category and hasattr(category, "unit_descriptor_from_file"):
            try:
                return category.unit_descriptor_from_file(file_path, parsed)
            except Exception as exc:
                logger.debug(f"Category file descriptor failed for {file_path}: {exc}")
        label = Path(file_path).name
        return {"granularity": "file", "label": label, "stable_key": label, "sort_key": [label], "coordinates": {}}

    @staticmethod
    def _file_matches(
        category: Any | None,
        file_path: str,
        parsed: Any | None,
        descriptor: dict[str, Any],
        targets: list[dict[str, Any]],
    ) -> bool:
        if category and hasattr(category, "torrent_file_matches_target"):
            try:
                return bool(category.torrent_file_matches_target(
                    file_path=file_path,
                    parsed=parsed,
                    file_descriptor=descriptor,
                    target_descriptors=targets,
                ))
            except Exception as exc:
                logger.debug(f"Category file match failed for {file_path}: {exc}")
        key = str((descriptor or {}).get("stable_key") or "")
        wanted = {str((target or {}).get("stable_key") or "") for target in targets}
        return bool(key and key in wanted)

    @staticmethod
    def _file_priority(category: Any | None, file_path: str, parsed: Any | None, descriptor: dict[str, Any], selected: bool) -> int:
        if category and hasattr(category, "torrent_file_priority"):
            try:
                return int(category.torrent_file_priority(
                    file_path=file_path,
                    parsed=parsed,
                    file_descriptor=descriptor,
                    selected=selected,
                ))
            except Exception as exc:
                logger.debug(f"Category file priority failed for {file_path}: {exc}")
        if "sample" in str(file_path or "").lower():
            return 0
        return 4 if selected else 0
