"""
TV suggestion workflow for LJS.

Builds TV-category suggestions such as missing aired episodes, catch-up
batches, quality upgrades, and related series. The generic suggestion compiler
calls this workflow only for items owned by the TV category, keeping episodic
TV assumptions inside the category boundary.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from loguru import logger

from src.core.models import CategoryItem, SuggestedActionRecord, TvShowItem

if TYPE_CHECKING:
    from src.core.database import Database
    from src.integrations.tmdb import TMDBClient
    from src.integrations.tvmaze import TVMazeClient
    from src.core.config import SettingsManager


class TvSuggestionWorkflow:
    """Compiles user-facing suggestions for TV category items."""

    INTER_ITEM_COOLDOWN: float = 3.0

    def __init__(
        self,
        db: "Database",
        tmdb_client: "TMDBClient | None" = None,
        tvmaze_client: "TVMazeClient | None" = None,
        settings_manager: "SettingsManager | None" = None,
        library_object_builder: Any | None = None,
    ) -> None:
        """Initialize the workflow with metadata clients and storage."""
        self._db = db
        self._tmdb = tmdb_client
        self._tvmaze = tvmaze_client
        self._sm = settings_manager
        self._library_objects = library_object_builder

    @property
    def tmdb(self) -> "TMDBClient | None":
        """Return a current TMDB client when the user configured a key."""
        if self._sm:
            api_key = self._sm.settings.category_service_value("tv", "tmdb", "api_key")
            if api_key and (not self._tmdb or getattr(self._tmdb, "_api_key", None) != api_key):
                from src.integrations.tmdb import TMDBClient

                self._tmdb = TMDBClient(api_key)
        return self._tmdb

    async def compile_many(self, items: list[CategoryItem]) -> int:
        """Compile suggestions for enabled TV items and persist them."""
        enabled_tv_items = [item for item in items if item.enabled and item.item_type == "tv"]
        logger.info(f"Compiling TV suggestions for {len(enabled_tv_items)} category items...")
        total = 0
        for index, item in enumerate(enabled_tv_items):
            try:
                total += await self.compile_one(item)
                if index < len(enabled_tv_items) - 1:
                    await asyncio.sleep(self.INTER_ITEM_COOLDOWN)
            except Exception as exc:
                logger.error(f"Failed to compile TV suggestions for '{item.key}': {exc}")
        logger.info(f"TV suggestion compilation complete: {total} suggestions")
        return total

    async def compile_one(self, item: CategoryItem) -> int:
        """Compile and persist suggestions for one TV item."""
        suggestions = await self.build_suggestions(item)
        await self._db.downloads.clear_suggestions_for_item("tv", item.key)
        for suggestion in suggestions:
            await self._db.downloads.upsert_suggested_action(suggestion)
        return len(suggestions)

    async def build_suggestions(self, item: CategoryItem) -> list[SuggestedActionRecord]:
        """Build suggestions for one TV item without writing them."""
        now = datetime.now(timezone.utc).isoformat()
        tv_item = await self._enrich_metadata(item)
        downloaded_set, library_evidence = await self._downloaded_episode_context(tv_item)
        missing, provider_evidence = await self._find_missing_episodes(tv_item, downloaded_set)
        suggestions: list[SuggestedActionRecord] = []

        audit_payload = {**library_evidence, **provider_evidence}
        self._log_missing_episode_audit(tv_item, audit_payload)

        frontier_keys = set()
        if missing and tv_item.is_episodic:
            missing.sort()
            suggestions.extend(self._download_batch_suggestions(tv_item, missing, downloaded_set, now, audit_payload))
            frontier_keys = {tuple(row[:2]) for row in self._frontier_missing_episodes(missing, downloaded_set)}

        for season, episode, title_hint in missing:
            is_frontier = (season, episode) in frontier_keys
            metadata = self._missing_episode_metadata(
                tv_item,
                missing=[(season, episode, title_hint)],
                evidence=audit_payload,
                reason_code="single_missing_episode",
            )
            suggestions.append(SuggestedActionRecord(
                category_id="tv",
                item_id=tv_item.key,
                item_name=tv_item.key,
                action_type="missing_episode",
                title=f"Download S{season:02d}E{episode:02d} — {title_hint}",
                description=metadata["explanation"],
                endpoint=f"/api/categories/tv/items/{tv_item.key}/actions/download_specific_episode",
                method="POST",
                body_json=json.dumps({"season": season, "episode": episode}),
                priority=88 if is_frontier else 45,
                status="pending",
                metadata_json=json.dumps({**metadata, "season": season, "episode": episode, "frontier_episode": is_frontier}, ensure_ascii=False),
                created_at=now,
            ))

        upgrades = await self._db.downloads.get_upgrade_candidates(item_id=tv_item.key, status="pending")
        for upgrade in upgrades:
            metadata = self._upgrade_metadata(tv_item, upgrade)
            suggestions.append(SuggestedActionRecord(
                category_id="tv",
                item_id=tv_item.key,
                item_name=tv_item.key,
                action_type="quality_upgrade",
                title=f"Upgrade to {upgrade.best_upgrade_resolution}",
                description=metadata["explanation"],
                endpoint=f"/api/categories/tv/items/{tv_item.key}/actions/search_upgrade",
                method="POST",
                body_json=json.dumps({"upgrade_id": upgrade.id}),
                priority=70,
                status="pending",
                metadata_json=json.dumps({**metadata, "upgrade_id": upgrade.id}, ensure_ascii=False),
                created_at=now,
            ))

        for title, description, query in await self._find_related_items(tv_item):
            metadata = self._related_metadata(tv_item, title, description, query)
            suggestions.append(SuggestedActionRecord(
                category_id="tv",
                item_id=tv_item.key,
                item_name=tv_item.key,
                action_type="related_media",
                title=title,
                description=metadata["explanation"],
                endpoint="/api/categories/tv/items",
                method="POST",
                body_json=json.dumps({"name": query, "language": getattr(tv_item, "language", "English"), "check_interval_days": 7}),
                priority=40,
                status="pending",
                metadata_json=json.dumps(metadata, ensure_ascii=False),
                created_at=now,
            ))
        return suggestions


    @staticmethod
    def _frontier_missing_episodes(
        missing: list[tuple[int, int, str]],
        downloaded_set: set[tuple[int, int]],
    ) -> list[tuple[int, int, str]]:
        """Return missing episodes after the latest local episode.

        Old historical gaps are still suggestions, but they must not bury the
        newly aired/current-season frontier that users usually care about.
        """
        if not missing or not downloaded_set:
            return []
        latest_season = max(season for season, _ in downloaded_set)
        latest_episode = max(episode for season, episode in downloaded_set if season == latest_season)
        frontier = [row for row in missing if row[0] > latest_season or (row[0] == latest_season and row[1] > latest_episode)]
        frontier.sort()
        return frontier

    def _download_batch_suggestions(
        self,
        item: CategoryItem,
        missing: list[tuple[int, int, str]],
        downloaded_set: set[tuple[int, int]],
        now: str,
        evidence: dict[str, Any],
    ) -> list[SuggestedActionRecord]:
        """Build TV missing-episode batch suggestions with explicit rationale."""
        suggestions: list[SuggestedActionRecord] = []
        frontier = self._frontier_missing_episodes(missing, downloaded_set)
        if frontier:
            latest = frontier[0]
            latest_metadata = self._missing_episode_metadata(
                item,
                missing=[latest],
                evidence=evidence,
                reason_code="latest_frontier_episode",
            )
            suggestions.append(SuggestedActionRecord(
                category_id="tv",
                item_id=item.key,
                item_name=item.key,
                action_type="download_latest_frontier",
                title=f"Download latest: S{latest[0]:02d}E{latest[1]:02d}",
                description=latest_metadata["explanation"],
                endpoint=f"/api/categories/tv/items/{item.key}/actions/download_specific_episode",
                method="POST",
                body_json=json.dumps({"season": latest[0], "episode": latest[1]}),
                priority=120,
                status="pending",
                metadata_json=json.dumps({**latest_metadata, "frontier_episode": True}, ensure_ascii=False),
                created_at=now,
            ))
        next_episode = frontier[0] if frontier else missing[0]
        next_metadata = self._missing_episode_metadata(
            item,
            missing=[next_episode],
            evidence=evidence,
            reason_code="next_missing_episode" if frontier else "oldest_missing_episode",
        )
        if not frontier:
            suggestions.append(SuggestedActionRecord(
                category_id="tv",
                item_id=item.key,
                item_name=item.key,
                action_type="download_next",
                title=f"Download oldest missing: S{next_episode[0]:02d}E{next_episode[1]:02d}",
                description=next_metadata["explanation"],
                endpoint=f"/api/categories/tv/items/{item.key}/actions/download_specific_episode",
                method="POST",
                body_json=json.dumps({"season": next_episode[0], "episode": next_episode[1]}),
                priority=55,
                status="pending",
                metadata_json=json.dumps(next_metadata, ensure_ascii=False),
                created_at=now,
            ))
        if len(missing) > 1:
            batch_metadata = self._missing_episode_metadata(
                item,
                missing=missing,
                evidence=evidence,
                reason_code="all_missing_episodes",
            )
            suggestions.append(SuggestedActionRecord(
                category_id="tv",
                item_id=item.key,
                item_name=item.key,
                action_type="download_all_missing",
                title=f"Download All {len(missing)} Missing Episodes",
                description=batch_metadata["explanation"],
                endpoint=f"/api/categories/tv/items/{item.key}/actions/download_missing_batch",
                method="POST",
                body_json=json.dumps({"episodes": [[row[0], row[1]] for row in missing]}),
                priority=40,
                status="pending",
                metadata_json=json.dumps(batch_metadata, ensure_ascii=False),
                created_at=now,
            ))
        max_season = max([row[0] for row in downloaded_set]) if downloaded_set else 0
        max_episode = max([row[1] for row in downloaded_set if row[0] == max_season]) if downloaded_set else 0
        remaining_next = [row for row in missing if (row[0] > max_season) or (row[0] == max_season and row[1] > max_episode)]
        if 1 < len(remaining_next) < len(missing):
            remaining_metadata = self._missing_episode_metadata(
                item,
                missing=remaining_next,
                evidence=evidence,
                reason_code="remaining_after_latest_local_episode",
            )
            suggestions.append(SuggestedActionRecord(
                category_id="tv",
                item_id=item.key,
                item_name=item.key,
                action_type="download_remaining_next",
                title=f"Download Remaining {len(remaining_next)} Next Episodes",
                description=remaining_metadata["explanation"],
                endpoint=f"/api/categories/tv/items/{item.key}/actions/download_missing_batch",
                method="POST",
                body_json=json.dumps({"episodes": [[row[0], row[1]] for row in remaining_next]}),
                priority=105,
                status="pending",
                metadata_json=json.dumps(remaining_metadata, ensure_ascii=False),
                created_at=now,
            ))
        return suggestions

    async def _enrich_metadata(self, item: CategoryItem) -> CategoryItem:
        """Fetch TVMaze/TMDB metadata for a TV item when missing."""
        if not isinstance(item, TvShowItem):
            item = TvShowItem(**item.model_dump())
        if item.genres and item.overview:
            return item
        if self._tvmaze and not item.tvmaze_id:
            try:
                results = await self._tvmaze.search(item.key)
                if results:
                    item.tvmaze_id = results[0].get("id")
                    if not item.genres and results[0].get("genres"):
                        item.genres = results[0]["genres"]
            except Exception as exc:
                logger.debug(f"TVMaze metadata fetch failed for '{item.key}': {exc}")
        tmdb = self.tmdb
        if tmdb and not item.tmdb_id:
            try:
                search_results = await tmdb.search(item.key, media_type="tv")
                if search_results:
                    item.tmdb_id = search_results[0].get("id")
                    details = await tmdb.get_tv_details(item.tmdb_id)
                    if details:
                        if not item.genres and details.get("genres"):
                            item.genres = details["genres"]
                        if not item.overview and details.get("overview"):
                            item.overview = details["overview"]
                        if not item.cast_names and details.get("cast"):
                            item.cast_names = [row.get("name") for row in details["cast"] if isinstance(row, dict) and row.get("name")]
            except Exception as exc:
                logger.debug(f"TMDB metadata fetch failed for '{item.key}': {exc}")
        return item

    async def _downloaded_episode_context(self, item: CategoryItem) -> tuple[set[tuple[int, int]], dict[str, Any]]:
        """Return downloaded episodes from the canonical TV library object.

        This workflow must not perform alias/progress-table detective work.  The
        canonical object is the TV category's single source of truth: scanner,
        provider metadata, downloads, UI, suggestions, and agent tools all read
        the same normalized episode units.  If this returns zero local episodes
        for a populated library, the bug is in canonical-object construction and
        should be fixed there, not patched here.
        """
        downloaded: set[tuple[int, int]] = set()
        object_summary: dict[str, Any] = {}
        if self._library_objects is not None:
            try:
                canonical = await self._library_objects.build("tv", item.key, settings_item=item)
                for unit in canonical.get("units") or []:
                    if unit.get("status") != "downloaded":
                        continue
                    # Canonical TV units are physical files.  The TV category
                    # attaches season/episode coordinates and also exposes
                    # logical episode objects under ``seasons``.  Suggestions
                    # only need the logical owned set, so dedupe coordinates.
                    if unit.get("role") != "episode_payload" and unit.get("unit_type") not in {"episode", "file"}:
                        continue
                    season = self._as_int(unit.get("season"))
                    episode = self._as_int(unit.get("episode"))
                    if season > 0 and episode > 0:
                        downloaded.add((season, episode))
                if not downloaded:
                    for season_row in canonical.get("seasons") or []:
                        for episode_row in season_row.get("episodes") or []:
                            season = self._as_int(episode_row.get("season") or season_row.get("season_number"))
                            episode = self._as_int(episode_row.get("episode"))
                            if season > 0 and episode > 0:
                                downloaded.add((season, episode))
                object_summary = canonical.get("computed") or {}
            except Exception as exc:
                logger.warning(f"Canonical TV library object unavailable for '{item.key}': {exc}")

        if self._library_objects is None:
            rows = await self._db.media.list_category_units("tv", item.key, status="downloaded")
            for row in rows:
                season = self._as_int(row.get("season"))
                episode = self._as_int(row.get("episode"))
                if season > 0 and episode > 0:
                    downloaded.add((season, episode))
            object_summary = {"downloaded_episode_count": len(downloaded), "canonical_fallback": "exact_item_units"}

        return downloaded, {
            "downloaded_episode_count": len(downloaded),
            "downloaded_episode_keys": [f"S{s:02d}E{e:02d}" for s, e in sorted(downloaded)[:80]],
            "library_evidence_source": "canonical_library_object",
            "canonical_computed": object_summary,
        }

    async def _find_missing_episodes(
        self,
        item: CategoryItem,
        downloaded_set: set[tuple[int, int]],
    ) -> tuple[list[tuple[int, int, str]], dict[str, Any]]:
        """Compare TVMaze episode list against downloaded TV units."""
        missing: list[tuple[int, int, str]] = []
        evidence: dict[str, Any] = {
            "provider": "tvmaze" if self._tvmaze else "none",
            "provider_episode_count": 0,
            "provider_aired_episode_keys": [],
            "missing_episode_count": 0,
        }
        if not self._tvmaze:
            return missing, evidence
        try:
            tvmaze_id = getattr(item, "tvmaze_id", None)
            if tvmaze_id:
                episodes = await self._tvmaze.get_episode_list(tvmaze_id)
            else:
                results = await self._tvmaze.search(item.key)
                if not results:
                    return missing, evidence
                episodes = await self._tvmaze.get_episode_list(results[0]["id"])
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            aired: list[tuple[int, int, str]] = []
            for episode in episodes or []:
                season_number = self._as_int(episode.get("season"))
                episode_number = self._as_int(episode.get("number"))
                if not season_number or not episode_number:
                    continue
                if (episode.get("airdate") or "") > today:
                    continue
                title = episode.get("name", f"Episode {episode_number}")
                aired.append((season_number, episode_number, title))
                if (season_number, episode_number) not in downloaded_set:
                    missing.append((season_number, episode_number, title))
            evidence.update({
                "provider_episode_count": len(aired),
                "provider_aired_episode_keys": [f"S{s:02d}E{e:02d}" for s, e, _ in aired[:120]],
                "missing_episode_count": len(missing),
                "missing_episode_keys": [f"S{s:02d}E{e:02d}" for s, e, _ in missing[:80]],
            })
        except Exception as exc:
            logger.warning(f"Missing-episode lookup failed for '{item.key}': {exc}")
            evidence["provider_error"] = str(exc)
        return missing, evidence

    async def _find_related_items(self, item: CategoryItem) -> list[tuple[str, str, str]]:
        """Use TMDB TV recommendations for related TV suggestions."""
        related: list[tuple[str, str, str]] = []
        tmdb = self.tmdb
        if not tmdb or not getattr(item, "tmdb_id", None):
            return related
        try:
            recs = await tmdb.get_recommendations("tv", item.tmdb_id)
            for rec in recs[:3]:
                title = rec.get("title") or rec.get("name") or ""
                if not title:
                    continue
                description = f"Recommended based on {item.key}"
                if rec.get("rating"):
                    description += f" (★ {float(rec['rating']):.1f})"
                if rec.get("year"):
                    description += f" [{rec['year']}]"
                related.append((f"Also track: {title}", description, title))
        except Exception as exc:
            logger.debug(f"Related TV lookup failed for '{item.key}': {exc}")
        return related

    def _missing_episode_metadata(
        self,
        item: CategoryItem,
        *,
        missing: list[tuple[int, int, str]],
        evidence: dict[str, Any],
        reason_code: str,
    ) -> dict[str, Any]:
        missing_keys = [f"S{s:02d}E{e:02d}" for s, e, _ in missing]
        downloaded_count = int(evidence.get("downloaded_episode_count") or 0)
        provider_count = int(evidence.get("provider_episode_count") or 0)
        source = evidence.get("library_evidence_source") or "none"
        if len(missing) == 1:
            target = missing_keys[0]
            explanation = (
                f"I found {provider_count} aired episode(s) in the TVMaze guide for {item.key}. "
                f"Your local library ledger currently shows {downloaded_count} downloaded episode(s); {target} is the first one I could not match."
            )
        else:
            explanation = (
                f"I found {provider_count} aired episode(s) in the TVMaze guide for {item.key}. "
                f"Your local library ledger matched {downloaded_count}, so these {len(missing)} episode(s) look missing: "
                f"{', '.join(missing_keys[:12])}{'…' if len(missing_keys) > 12 else ''}."
            )
        if source != "canonical_library_object":
            explanation += " The workflow had to use a fallback evidence source; canonical library object construction should be reviewed."
        return {
            "explanation": explanation,
            "confidence": "high" if source == "canonical_library_object" else "low",
            "evidence": {
                **evidence,
                "reason_code": reason_code,
                "missing_episode_keys": missing_keys,
            },
        }

    def _upgrade_metadata(self, item: CategoryItem, upgrade: Any) -> dict[str, Any]:
        current = getattr(upgrade, "current_resolution", "") or "current quality"
        target = getattr(upgrade, "best_upgrade_resolution", "") or "a better release"
        quality_improvement = getattr(upgrade, "quality_improvement", "") or f"{current} → {target}"
        return {
            "explanation": (
                f"I found an upgrade candidate for {item.key}: {quality_improvement}. "
                "This is not a missing episode; it is a quality-preference suggestion."
            ),
            "confidence": "medium",
            "evidence": {
                "reason_code": "quality_upgrade",
                "current_quality": current,
                "target_quality": target,
                "quality_improvement": quality_improvement,
            },
        }

    def _related_metadata(self, item: CategoryItem, title: str, description: str, query: str) -> dict[str, Any]:
        return {
            "explanation": (
                f"This is a discovery suggestion, not a missing-download warning. {description}. "
                f"I can start tracking it as a related TV item."
            ),
            "confidence": "low",
            "evidence": {
                "reason_code": "related_media",
                "source_item": item.key,
                "query": query,
            },
        }

    def _log_missing_episode_audit(self, item: CategoryItem, evidence: dict[str, Any]) -> None:
        """Write compact suggestion diagnostics that help explain bad suggestions."""
        provider_count = int(evidence.get("provider_episode_count") or 0)
        downloaded_count = int(evidence.get("downloaded_episode_count") or 0)
        missing_count = int(evidence.get("missing_episode_count") or 0)
        source = evidence.get("library_evidence_source") or "none"
        aliases = evidence.get("aliases_checked") or []
        msg = (
            f"TV suggestion audit {item.key!r}: provider_aired={provider_count}, "
            f"library_downloaded={downloaded_count}, missing={missing_count}, "
            f"library_source={source}, aliases_checked={aliases[:4]}"
        )
        if missing_count and provider_count and missing_count == provider_count and downloaded_count == 0:
            logger.warning(msg + " — all aired episodes appear missing; check library identity/unit sync before approving.")
        else:
            logger.debug(msg)

    @staticmethod
    def _as_int(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0
