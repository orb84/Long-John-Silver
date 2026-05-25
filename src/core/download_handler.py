"""
Download completion handler for LJS.

Owns the lifecycle logic for completed downloads: exposing finished torrent
payloads in the library as soon as libtorrent reports 100%, guarded cleanup
after seeding, and startup reconciliation for older rows whose callbacks were
missed.  Keeping this logic in one object prevents the downloader, scheduler,
and web layer from each inventing their own post-download file rules.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from loguru import logger
from typing import TYPE_CHECKING, Optional, Any

from src.core.models import DownloadItem, DownloadStatus, Settings
from src.core.security.path_policy import SafePathResolver, SecurityPolicyError
from src.core.categories.identity import clean_display_title

if TYPE_CHECKING:
    from src.core.downloader import DownloadManager
    from src.core.notifications import NotificationService
    from src.core.librarian import Librarian
    from src.core.release_groups import ReleaseGroupTracker


class DownloadCompletionHandler:
    """Manages post-download file organization and cleanup.

    Registered as a callback on DownloadManager. The ready callback fires when
    a torrent first reaches 100% and is safe to expose to the user's media
    library. The completion callback fires when the seeding policy finishes and
    removes staging copies that are no longer needed.

    Extension guidance:
    - Add new library presentation modes here or in a collaborator, not inside
      ``DownloadLifecycleMonitor``. The monitor should only know about torrent
      state, not Plex/Jellyfin/library naming rules.
    - Keep operations idempotent. Startup recovery may call the same import path
      again after a crash or a missed callback.
    - Never mutate seed-in-place payloads; libtorrent must keep byte-for-byte
      ownership of those files.
    """

    def __init__(self, downloader: "DownloadManager", librarian: "Librarian",
                 notifications: "NotificationService",
                 category_registry: Optional[object] = None,
                 settings: Optional[Settings] = None,
                 download_dir: Optional[Path] = None,
                 release_group_tracker: Optional["ReleaseGroupTracker"] = None,
                 settings_manager: Optional[Any] = None):
        """Initialize the handler with its service dependencies.

        Args:
            downloader: DownloadManager for querying and updating download state.
            librarian: Category-aware library organizer.
            notifications: Notification service for user completion alerts.
            category_registry: Registry for target path planning.
            settings: Startup settings snapshot. Used as fallback only.
            download_dir: Staging download directory.
            release_group_tracker: Optional release-group reputation sink.
            settings_manager: Live SettingsManager; preferred for callbacks that
                can run long after startup.
        """
        self._downloader = downloader
        self._librarian = librarian
        self._notifications = notifications
        self._categories = category_registry
        self._settings = settings
        self._settings_manager = settings_manager
        self._dl_dir = (download_dir or Path()).resolve()
        self._release_group_tracker = release_group_tracker
        self._library_reconciler: Any | None = None

    def set_library_reconciler(self, reconciler: Any | None) -> None:
        """Attach an item-scoped library reconciler after scheduler composition.

        The completion handler is constructed before ``MediaScheduler`` in the
        application root.  A setter avoids a construction cycle while still
        letting completed downloads refresh only their affected library item.
        """
        self._library_reconciler = reconciler

    # ── Settings and path helpers ──────────────────────────────────

    def _current_settings(self) -> Settings:
        """Return the freshest settings object available for callbacks.

        Ready/completion callbacks often fire minutes or hours after service
        construction. Always prefer SettingsManager so Compass changes are
        reflected. As a final resilience fallback return default Settings
        instead of letting a missing composition parameter crash library import.
        """
        if self._settings_manager is not None:
            try:
                settings = self._settings_manager.settings
                if settings is not None:
                    return settings
            except Exception as exc:
                logger.warning(f"Download callback could not read live settings: {exc}")
        if self._settings is not None:
            return self._settings
        logger.warning("Download callback has no settings object; using safe defaults for library import")
        return Settings()

    @staticmethod
    def _path_within_root(path: Path, root: Path) -> bool:
        """Return True when ``path`` resolves inside ``root``.

        ``str.startswith`` is not safe here because ``/downloads2`` also starts
        with ``/downloads``.  ``Path.relative_to`` gives the intended filesystem
        containment check and prevents cleanup/import logic from touching a
        sibling directory by accident.
        """
        try:
            path.resolve().relative_to(root.resolve())
            return True
        except Exception:
            return False

    def _path_in_download_dir(self, path: Path) -> bool:
        """Verify that a path resolves inside the staging download directory."""
        return self._path_within_root(path, self._dl_dir)

    @staticmethod
    def _clean_source_name(path: str) -> str:
        """Return the final media filename without temporary download suffixes."""
        name = str(path or "")
        return name[:-12] if name.endswith(".downloading") else name

    def _resolve_downloaded_source(self, relative_path: str, item: object | None = None) -> Path | None:
        """Find a downloaded file, tolerating final and ``.downloading`` names.

        Args:
            relative_path: Torrent-relative file path from DownloadFileInfo.
            item: Optional DownloadItem used to include its save_path.

        Returns:
            The existing safe path, or None when the file is missing/disallowed.
        """
        roots = [self._dl_dir]
        item_save_path = getattr(item, "save_path", "") if item is not None else ""
        if item_save_path:
            roots.insert(0, Path(item_save_path))
        candidates = []
        raw = str(relative_path or "")
        if raw:
            for root in roots:
                candidates.append((root / raw).resolve())
                if raw.endswith(".downloading"):
                    candidates.append((root / raw[:-12]).resolve())
                else:
                    candidates.append((root / f"{raw}.downloading").resolve())
        for candidate in candidates:
            if candidate.exists() and self._path_allowed_for_item(candidate, item):
                return candidate
        return None

    def _path_allowed_for_item(self, path: Path, item: object | None = None) -> bool:
        """Return whether a source path belongs to staging or this torrent's save root."""
        if self._path_in_download_dir(path):
            return True
        item_save_path = getattr(item, "save_path", "") if item is not None else ""
        if not item_save_path:
            return False
        return self._path_within_root(path, Path(item_save_path))

    def _category_for_item(self, item: DownloadItem) -> object | None:
        """Look up the item's category, defaulting to TV only for legacy rows."""
        category_id = item.category_id or ""
        return self._categories.get(category_id) if self._categories else None

    @staticmethod
    def _sanitize_segment(value: str) -> str:
        """Create a conservative path segment for fallback target planning."""
        cleaned = re.sub(r"[^A-Za-z0-9._() \[\]-]+", " ", str(value or "")).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned[:120] or "Unknown"

    def _planned_item_title(self, item: DownloadItem) -> str:
        """Return the persisted provider title to use for library paths."""
        context = getattr(item, "import_context", None)
        if context and context.planning_title:
            return context.planning_title
        return item.item_name

    def _planned_item_year(self, item: DownloadItem) -> int | None:
        """Return the persisted provider year to use for library paths."""
        context = getattr(item, "import_context", None)
        if context and context.planning_year:
            return context.planning_year
        return getattr(item, "year", None)

    def _planned_episode_title(self, item: DownloadItem, source_episode_title: str | None = None) -> str:
        """Return the best episode title without provider re-querying."""
        if source_episode_title:
            return source_episode_title
        context = getattr(item, "import_context", None)
        return context.episode_title if context and context.episode_title else ""

    def _fallback_target_path(
        self,
        source: Path,
        item: DownloadItem,
        settings: Settings,
        category: object,
        *,
        file_info: object | None = None,
    ) -> Path:
        """Compute a basic library target when category planning fails.

        This is intentionally boring but reliable. It is used only as a safety
        net when a category's naming template/configuration throws; the normal
        category-owned ``compute_target_path`` remains the preferred path.
        """
        item_name = clean_display_title(self._planned_item_title(item) or source.stem)
        filename = self._clean_source_name(source.name)
        if hasattr(category, "fallback_library_path"):
            try:
                return category.fallback_library_path(
                    source,
                    item_name,
                    settings,
                    season=getattr(file_info, "season", None) or getattr(item, "season", None),
                    episode=getattr(file_info, "episode", None) or getattr(item, "episode", None),
                    source_name=filename,
                    year=self._planned_item_year(item),
                    episode_title=self._planned_episode_title(item, getattr(file_info, "episode_title", None)),
                )
            except Exception as exc:
                logger.debug(f"Category fallback target planning failed for {item.item_name}: {exc}")
        try:
            root = Path(category.get_root_path(settings))
        except Exception:
            root = Path(getattr(settings, "library_root", "./library")) / (item.category_id or "media")
        title = self._sanitize_segment(item_name)
        return root / title / filename

    def _planned_target_path(
        self,
        source: Path,
        item: DownloadItem,
        category: object,
        settings: Settings,
        *,
        file_info: object | None = None,
        source_name: str | None = None,
        episode_title: str | None = None,
    ) -> Path:
        """Ask the owning category for a target path, falling back safely.

        The completion handler deliberately does not interpret category unit
        coordinates. It passes the raw download/file context to the category,
        and the category decides how that context maps to its naming template.
        """
        try:
            metadata = {
                "title": clean_display_title(self._planned_item_title(item) or source.stem),
                "year": self._planned_item_year(item),
                "unit_title": self._planned_episode_title(item, episode_title),
            }
            if hasattr(category, "download_target_for_item"):
                return category.download_target_for_item(
                    source,
                    item,
                    settings,
                    source_name=self._clean_source_name(source_name or source.name),
                    file_info=file_info,
                    metadata=metadata,
                )
            # Compatibility only for older category objects outside the built-in registry.
            library_root = category.get_root_path(settings)
            return category.compute_target_path(
                source_name=self._clean_source_name(source_name or source.name),
                item_name=metadata["title"],
                season=getattr(file_info, "season", None) or getattr(item, "season", None) or 1,
                episode=getattr(file_info, "episode", None) or getattr(item, "episode", None) or 1,
                library_root=library_root,
                settings=settings,
                year=metadata["year"],
                episode_title=metadata["unit_title"],
            )
        except Exception as exc:
            logger.error(
                f"Category target planning failed for {item.item_name}; using fallback library path: {exc}"
            )
            return self._fallback_target_path(
                source,
                item,
                settings,
                category,
                file_info=file_info,
            )

    def _same_payload(self, source: Path, target: Path) -> bool:
        """Return whether an existing target can be reused for this source."""
        try:
            if target.samefile(source):
                return True
        except Exception:
            pass
        try:
            return source.exists() and target.exists() and source.stat().st_size == target.stat().st_size
        except Exception:
            return False

    def _unique_destination(self, resolver: SafePathResolver, target: Path, source: Path) -> tuple[Path, bool]:
        """Return a safe destination, reusing same-size targets idempotently.

        Returns:
            ``(path, already_present)``.  If a target already exists but appears
            to be the same payload, the caller can simply record it as organized.
            If it is different, a numbered sibling is chosen.
        """
        candidate = resolver.require(target, purpose="download.ready.target", must_exist=False)
        resolver.safe_mkdir(candidate.parent, purpose="download.ready.mkdir")
        if not candidate.exists():
            return candidate, False
        if self._same_payload(source, candidate):
            return candidate, True
        stem = candidate.stem
        suffix = candidate.suffix
        for index in range(2, 1000):
            next_candidate = resolver.require(
                candidate.with_name(f"{stem} ({index}){suffix}"),
                purpose="download.ready.dedupe",
                must_exist=False,
            )
            # Existing numbered siblings are normal after retries/crashes; keep
            # walking until an unused path is found instead of treating that as
            # a security failure.
            if not next_candidate.exists():
                return next_candidate, False
        raise SecurityPolicyError(f"Could not find available destination near {candidate}")

    def _resolve_safe_completion_destination(
        self,
        *,
        resolver: SafePathResolver,
        target: Path,
        source: Path,
        item: DownloadItem,
        category: object,
        settings: Settings,
        file_info: object | None = None,
    ) -> tuple[Path, bool] | None:
        """Validate a ready-time target, retrying the category fallback on block.

        The security resolver is the final authority.  When a category template
        accidentally produces a target outside its root, do not give up with a
        stranded completed torrent. Re-plan once through the category fallback,
        which preserves the source filename and category unit hierarchy.
        """
        try:
            return self._unique_destination(resolver, target, source)
        except SecurityPolicyError as exc:
            fallback = self._fallback_target_path(
                source,
                item,
                settings,
                category,
                file_info=file_info,
            )
            try:
                same_target = fallback.resolve(strict=False) == target.resolve(strict=False)
            except Exception:
                same_target = str(fallback) == str(target)
            if same_target:
                logger.error(f"Ready callback blocked unsafe target planning {source} -> {target}: {exc}")
                return None
            logger.info(
                "Ready callback recovered from an unsafe category-template target; "
                f"using fallback for {item.item_name}: {target} -> {fallback} ({exc})"
            )
            try:
                return self._unique_destination(resolver, fallback, source)
            except SecurityPolicyError as fallback_exc:
                logger.error(
                    "Ready callback blocked unsafe fallback target planning "
                    f"{source} -> {fallback}: {fallback_exc}"
                )
                return None

    # ── Ready-time library exposure ────────────────────────────────

    def _reconcile_identity_for_item(self, item: DownloadItem) -> tuple[str, str]:
        """Return category/item identity for post-import reconciliation."""
        context = getattr(item, "import_context", None)
        category_id = (getattr(context, "category_id", "") if context else "") or item.category_id or ""
        item_id = (getattr(context, "item_id", "") if context else "") or item.item_id or self._planned_item_title(item)
        return str(category_id or ""), str(item_id or "")

    def _materialize_library_file_sync(
        self,
        resolver: SafePathResolver,
        source: Path,
        safe_target: Path,
        item: DownloadItem,
    ) -> Path | None:
        """Hardlink or copy a file in a worker thread-friendly sync block."""
        try:
            resolver.safe_hardlink(source, safe_target, purpose="download.ready.hardlink")
            logger.info(f"Hardlinked '{clean_display_title(item.item_name)}' -> {safe_target}")
            return safe_target
        except OSError:
            try:
                resolver.safe_copy(source, safe_target, purpose="download.ready.copy")
                logger.info(f"Copied '{clean_display_title(item.item_name)}' -> {safe_target}")
                return safe_target
            except Exception as exc:
                logger.error(f"Failed to copy {source} -> {safe_target}: {exc}")
                return None
        except SecurityPolicyError as exc:
            logger.error(f"Ready callback blocked unsafe file operation {source} -> {safe_target}: {exc}")
            return None

    async def _reconcile_imported_library_item(self, item: DownloadItem, target: Path) -> None:
        """Refresh the affected canonical item after a ready-time import."""
        reconciler = self._library_reconciler
        if reconciler is None or not hasattr(reconciler, "reconcile_library_item_from_path"):
            return
        category_id, item_id = self._reconcile_identity_for_item(item)
        if not category_id or not item_id:
            return
        try:
            await reconciler.reconcile_library_item_from_path(
                category_id=category_id,
                item_id=item_id,
                changed_path=str(target),
                reason="download_import",
            )
        except Exception as exc:
            logger.warning(f"Item-scoped library reconciliation failed for {category_id}/{item_id}: {exc}")

    async def _link_completed_file_to_library(
        self,
        source: Path,
        item: DownloadItem,
        category: object,
        settings: Settings,
        *,
        file_info: object | None = None,
        source_name: str | None = None,
        episode_title: str | None = None,
    ) -> Path | None:
        """Create the non-mutating library copy for one completed source file.

        Download-ready organization must not move the original payload because
        libtorrent still needs it while post-download seeding continues.  This
        method hardlinks when possible, falls back to copying, and treats an
        existing same-size target as success so recovery is idempotent.
        """
        target = self._planned_target_path(
            source,
            item,
            category,
            settings,
            file_info=file_info,
            source_name=source_name,
            episode_title=episode_title,
        )
        resolver = SafePathResolver.for_category(
            category,
            settings,
            extra_roots=[self._dl_dir, Path(getattr(item, "save_path", "") or self._dl_dir)],
        )
        destination = self._resolve_safe_completion_destination(
            resolver=resolver,
            target=target,
            source=source,
            item=item,
            category=category,
            settings=settings,
            file_info=file_info,
        )
        if destination is None:
            return None
        safe_target, already_present = destination
        reconciler = self._library_reconciler
        category_id, item_id = self._reconcile_identity_for_item(item)
        mutation_marked = False
        if reconciler is not None and hasattr(reconciler, "begin_managed_library_mutation"):
            try:
                reconciler.begin_managed_library_mutation(category_id=category_id, item_id=item_id)
                mutation_marked = True
            except Exception:
                mutation_marked = False
        try:
            if already_present:
                logger.info(f"Library target already present for '{item.item_name}': {safe_target}")
                await self._reconcile_imported_library_item(item, safe_target)
                return safe_target

            result = await asyncio.to_thread(
                self._materialize_library_file_sync,
                resolver,
                source,
                safe_target,
                item,
            )
            if result is not None:
                await self._reconcile_imported_library_item(item, result)
            return result
        finally:
            if mutation_marked and reconciler is not None and hasattr(reconciler, "end_managed_library_mutation"):
                try:
                    reconciler.end_managed_library_mutation(category_id=category_id, item_id=item_id)
                except Exception:
                    pass

    async def on_download_ready(self, download_id: str) -> None:
        """Expose completed files in the library when a torrent reaches 100%.

        Args:
            download_id: The download row whose torrent just became a seed.

        The method is deliberately idempotent.  It may run after startup
        recovery for torrents that completed while callbacks were not yet wired.
        """
        item = await self._downloader.get_download(download_id)
        if not item:
            return
        await self._expose_item_to_library(item)

    async def _expose_item_to_library(self, item: DownloadItem) -> bool:
        """Implementation for ready-time library exposure.

        Returns:
            True when at least one file was marked with an organized path.
        """
        settings = self._current_settings()
        category = self._category_for_item(item)

        if item.sharing_enabled and getattr(settings, "sharing", None) and settings.sharing.mode == "seed_in_place":
            changed = False
            for df in item.files:
                source = self._resolve_downloaded_source(df.file_path, item)
                if source and (df.status in {"complete", "organized"} or item.progress >= 0.999):
                    df.organized_path = str(source)
                    df.status = "organized"
                    changed = True
            if not item.files and item.file_path:
                item.file_path = str(Path(item.file_path).resolve())
                changed = True
            if changed:
                await self._downloader.update_download(item)
            logger.info(f"Seed-in-place ready: leaving '{item.item_name}' torrent payload in library for sharing")
            return changed

        if not category:
            logger.warning(f"Ready callback: no category for {item.item_name}; leaving file in staging")
            return False

        changed = False
        if not item.files:
            if not item.file_path:
                logger.warning(f"Ready callback: cannot organize {item.item_name}; no file path recorded")
                return False
            source = Path(item.file_path).resolve()
            if source.exists() and self._path_allowed_for_item(source, item):
                target = await self._link_completed_file_to_library(source, item, category, settings)
                if target:
                    item.file_path = str(source)
                    changed = True
            else:
                logger.warning(f"Ready callback: single-file source missing or disallowed for {item.item_name}: {source}")
            if changed:
                await self._downloader.update_download(item)
            return changed

        for df in item.files:
            if df.organized_path:
                continue
            if df.status not in {"complete", "organized"} and item.progress < 0.999:
                continue
            source = self._resolve_downloaded_source(df.file_path, item)
            if not source:
                attempted = (Path(item.save_path or self._dl_dir) / str(df.file_path)).resolve()
                logger.warning(f"Ready callback: skipping missing source for {item.item_name}: {attempted}")
                continue
            target = await self._link_completed_file_to_library(
                source,
                item,
                category,
                settings,
                file_info=df,
                source_name=df.file_path,
                episode_title=df.episode_title,
            )
            if target:
                df.status = "organized"
                df.organized_path = str(target)
                changed = True

        if changed:
            await self._downloader.update_download(item)
        return changed

    # ── Seeding-complete cleanup and fallback import ───────────────

    def _move_completed_file_to_library(
        self,
        source: Path,
        item: DownloadItem,
        *,
        season: int | None = None,
        episode: int | None = None,
        episode_title: str | None = None,
    ) -> Path | None:
        """Move a completed staging file into the library after seeding ends."""
        if not source.exists() or not self._path_allowed_for_item(source, item):
            return None
        try:
            result = self._librarian.organize_file(
                source=source,
                item_name=self._planned_item_title(item),
                season=season or item.season,
                episode=episode or item.episode,
                episode_title=self._planned_episode_title(item, episode_title),
                year=self._planned_item_year(item),
                category_id=item.category_id or "",
            )
            if result:
                logger.info(f"Organized '{item.item_name}' -> {result}")
                return Path(result)
        except Exception as exc:
            logger.error(f"Failed to organize {item.item_name} from {source}: {exc}")
        return None

    async def _organize_missing_staging_payloads(self, item: DownloadItem) -> bool:
        """Move any unorganized completed staging files into the library.

        This is the safety net for cases where the ready-time hardlink/copy was
        missed or failed.  Multi-file torrents are handled per file; older code
        only tried the largest file, which left season-pack episodes stranded in
        the download folder.
        """
        changed = False
        if item.files:
            for df in item.files:
                if df.organized_path:
                    continue
                if df.status not in {"complete", "organized"} and item.progress < 0.999:
                    continue
                source = self._resolve_downloaded_source(df.file_path, item)
                if not source:
                    continue
                target = await asyncio.to_thread(
                    self._move_completed_file_to_library,
                    source,
                    item,
                    season=df.season or item.season,
                    episode=df.episode or item.episode,
                    episode_title=df.episode_title,
                )
                if target:
                    df.organized_path = str(target)
                    df.status = "organized"
                    changed = True
            if changed:
                await self._downloader.update_download(item)
            return changed

        if item.file_path:
            source = Path(item.file_path).resolve()
            if source.exists() and self._path_allowed_for_item(source, item):
                target = await asyncio.to_thread(self._move_completed_file_to_library, source, item)
                if target:
                    item.file_path = str(target)
                    await self._downloader.update_download(item)
                    return True
        return False

    async def on_download_complete(self, download_id: str) -> None:
        """Clean up staging copies and send notification after seeding.

        Args:
            download_id: The ID of the download whose seeding policy completed.
        """
        item = await self._downloader.get_download(download_id)
        if not item:
            return
        await self._complete_item(item, send_notification=True, record_release_group=True)

    async def _complete_item(
        self,
        item: DownloadItem,
        *,
        send_notification: bool,
        record_release_group: bool,
    ) -> None:
        """Internal completion flow with optional notification side effects."""
        if record_release_group and self._release_group_tracker and item.torrent_title:
            await self._release_group_tracker.record_outcome(item.torrent_title, success=True)

        settings = self._current_settings()
        if item.sharing_enabled and getattr(settings, "sharing", None) and settings.sharing.mode == "seed_in_place":
            logger.info(f"Seed-in-place complete: retaining library payload for '{item.item_name}'")
            if send_notification:
                await self._notifications.send_download_complete(item.item_name, item.season, item.episode)
            return

        # If ready-time exposure failed, do a final move now before cleaning up
        # staging files. This keeps completed downloads from getting stranded.
        await self._organize_missing_staging_payloads(item)
        item = await self._downloader.get_download(item.id) or item

        if item.files:
            for df in item.files:
                if df.organized_path:
                    source = self._resolve_downloaded_source(df.file_path, item)
                    if source and self._path_in_download_dir(source) and self._safe_unlink(source):
                        logger.debug(f"Cleaned up download copy: {source}")

        if send_notification:
            await self._notifications.send_download_complete(item.item_name, item.season, item.episode)

    async def reconcile_completed_imports(self, limit: int = 200) -> int:
        """Repair completed downloads that missed their library import callback.

        Args:
            limit: Maximum number of recent download rows to inspect.

        Returns:
            Number of rows where the handler created or repaired a library path.

        This method is safe to run at startup. It handles both seeding rows
        (hardlink/copy, non-mutating) and terminal complete rows (move leftover
        staging files). It does not send duplicate notifications.
        """
        repaired = 0
        for item in await self._downloader.get_recent_downloads(limit=limit):
            if item.status not in {DownloadStatus.SEEDING, DownloadStatus.COMPLETE}:
                continue
            if not self._needs_library_import(item):
                continue
            try:
                if item.status == DownloadStatus.SEEDING:
                    if await self._expose_item_to_library(item):
                        repaired += 1
                elif await self._organize_missing_staging_payloads(item):
                    repaired += 1
            except Exception as exc:
                logger.error(f"Startup library import repair failed for {item.id}/{item.item_name}: {exc}")
        if repaired:
            logger.info(f"Repaired library imports for {repaired} completed download(s)")
        return repaired

    def _needs_library_import(self, item: DownloadItem) -> bool:
        """Return whether a completed row still lacks a library-facing path."""
        settings = self._current_settings()
        if item.sharing_enabled and getattr(settings, "sharing", None) and settings.sharing.mode == "seed_in_place":
            # Seed-in-place files are already in their library root. They only
            # need repair when file rows exist but are missing organized_path.
            return any(not df.organized_path for df in item.files)
        if item.files:
            return any(not df.organized_path for df in item.files if df.status in {"complete", "organized"} or item.progress >= 0.999)
        return bool(item.file_path and self._path_allowed_for_item(Path(item.file_path), item))

    def _safe_unlink(self, path: Path) -> bool:
        """Quarantine a staging file only if it is inside the download directory."""
        if not path.exists() or not self._path_in_download_dir(path):
            return False
        try:
            settings = self._current_settings()
            resolver = SafePathResolver(allowed_roots=[self._dl_dir], config=settings.security)
            resolver.safe_unlink(path, purpose="download.cleanup", move_to_trash=True)
            return True
        except Exception as exc:
            logger.warning(f"Failed to clean up {path}: {exc}")
            return False
