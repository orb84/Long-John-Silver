"""TV-owned assistant search and ranking behavior."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from loguru import logger


class TvAgentSearchMixin:
    """Translate TV episode intent into precise torrent search behavior.

    The scheduler and assistant call category hooks; this mixin owns TV-specific
    episode inference, aired-date safeguards, language filtering, and season-pack
    ranking rules.
    """

    async def _aired_episode_numbers_for_season(self, title: str, season: int, context: object) -> set[int]:
        """Return episode numbers from a season that have actually aired.

        TV owns air-date semantics.  TVMaze is preferred for episode schedules;
        TMDB season details are used as a fallback.  If neither source is
        configured or reachable, an empty set is returned so callers can take a
        conservative local-range fallback instead of inventing future episodes.
        """
        today = datetime.now(timezone.utc).date()
        aired: set[int] = set()

        settings = getattr(context, "settings", None)
        tvmaze = getattr(context, "metadata_clients", {}).get("tvmaze") if hasattr(context, "metadata_clients") else None
        if tvmaze and self.metadata_provider_enabled(settings, "tvmaze", True):
            try:
                show_id = await self._tvmaze_show_id_for(title, context, tvmaze)
                if show_id:
                    episodes = await tvmaze.get_episode_list(show_id)
                    for ep in episodes or []:
                        try:
                            if int(ep.get("season") or 0) != int(season):
                                continue
                            number = int(ep.get("number") or 0)
                        except (TypeError, ValueError):
                            continue
                        if number <= 0:
                            continue
                        airdate = str(ep.get("airdate") or "")
                        # Treat missing/blank air dates as unknown, not aired.
                        if airdate and self._date_has_aired(airdate, today):
                            aired.add(number)
                    if aired:
                        return aired
            except Exception as exc:
                logger.debug(f"TVMaze aired-episode lookup failed for {title}: {exc}")

        enricher = getattr(context, "metadata_enricher", None)
        tmdb = getattr(enricher, "client", None) if enricher else None
        if tmdb and self.metadata_provider_enabled(settings, "tmdb", True):
            try:
                tmdb_id = await self._tmdb_id_for(title, context, tmdb)
                if tmdb_id and hasattr(tmdb, "get_tv_season_details"):
                    details = await tmdb.get_tv_season_details(int(tmdb_id), int(season))
                    for ep in (details or {}).get("episodes") or []:
                        try:
                            number = int(ep.get("episode_number") or 0)
                        except (TypeError, ValueError):
                            continue
                        if number <= 0:
                            continue
                        airdate = str(ep.get("air_date") or "")
                        # Treat missing/blank air dates as unknown, not aired.
                        if airdate and self._date_has_aired(airdate, today):
                            aired.add(number)
            except Exception as exc:
                logger.debug(f"TMDB aired-episode lookup failed for {title}: {exc}")
        return aired

    async def _tvmaze_show_id_for(self, title: str, context: object, tvmaze: object) -> int | None:
        rows = await context.db.media.get_category_metadata(self.category_id, title) if getattr(context, "db", None) else []
        for row in rows or []:
            metadata = row.get("metadata") or {}
            for key in ("tvmaze_id", "id"):
                try:
                    if metadata.get(key):
                        return int(metadata.get(key))
                except (TypeError, ValueError):
                    pass
            tvmaze_payload = metadata.get("tvmaze")
            if isinstance(tvmaze_payload, dict):
                try:
                    return int(tvmaze_payload.get("id"))
                except (TypeError, ValueError):
                    pass
        results = await tvmaze.search(title) if hasattr(tvmaze, "search") else []
        if results:
            try:
                return int(results[0].get("id"))
            except (TypeError, ValueError):
                return None
        return None

    async def _tmdb_id_for(self, title: str, context: object, tmdb: object) -> int | None:
        rows = await context.db.media.get_category_metadata(self.category_id, title) if getattr(context, "db", None) else []
        for row in rows or []:
            metadata = row.get("metadata") or {}
            try:
                if metadata.get("tmdb_id"):
                    return int(metadata.get("tmdb_id"))
            except (TypeError, ValueError):
                pass
        results = await tmdb.search(title, media_type="tv") if hasattr(tmdb, "search") else []
        if results:
            try:
                return int(results[0].get("id"))
            except (TypeError, ValueError):
                return None
        return None

    @staticmethod
    def _date_has_aired(value: str, today) -> bool:
        try:
            return datetime.fromisoformat(value[:10]).date() <= today
        except Exception:
            return False


    async def _expected_episode_count(self, title: str, season: int, arguments: dict[str, object], context: object) -> int | None:
        """Resolve the expected number of episodes for a season.

        The TV category owns this decision.  It first trusts an explicit user or
        tool argument, then looks at category metadata cached from providers such
        as TMDB, and finally tries to refresh metadata if an enricher is
        available.  Generic scheduler/assistant code should not infer TV season
        structure itself.
        """
        explicit = arguments.get("episode_count") or arguments.get("up_to_episode")
        if explicit:
            try:
                return int(explicit)
            except (TypeError, ValueError):
                return None

        count = await self._expected_episode_count_from_metadata(title, season, context)
        if count is not None:
            return count

        enricher = getattr(context, "metadata_enricher", None)
        settings = getattr(context, "settings", None)
        if enricher and self.metadata_provider_enabled(settings, "tmdb", True):
            try:
                record = await enricher.enrich_series(title)
                metadata = self.normalize_taste_metadata_payload(self.create_item(title), record, "tmdb_tv")
                if metadata:
                    metadata = await self.cache_metadata_artwork(self.create_item(title), metadata, context, provider="tmdb_tv")
                    await context.db.media.upsert_category_metadata(
                        self.category_id, title, metadata.get("provider", "tmdb_tv"), metadata,
                        str(metadata.get("external_id") or metadata.get("tmdb_id") or metadata.get("id") or ""),
                    )
                    return self._episode_count_from_payload(metadata, season)
            except Exception as exc:
                logger.debug(f"Unable to refresh TV metadata for missing episode count: {exc}")
        return None

    async def _expected_episode_count_from_metadata(self, title: str, season: int, context: object) -> int | None:
        """Read expected season length from cached category metadata."""
        if not getattr(context, "db", None):
            return None
        rows = await context.db.media.get_category_metadata(self.category_id, title)
        for row in rows:
            count = self._episode_count_from_payload(row.get("metadata") or {}, season)
            if count is not None:
                return count
        return None

    @staticmethod
    def _episode_count_from_payload(payload: dict[str, object], season: int) -> int | None:
        """Extract a season episode count from a provider metadata payload."""
        seasons = payload.get("seasons") or []
        if isinstance(seasons, list):
            for season_payload in seasons:
                if not isinstance(season_payload, dict):
                    continue
                try:
                    season_number = int(season_payload.get("season_number") or season_payload.get("number") or -1)
                except (TypeError, ValueError):
                    continue
                if season_number != season:
                    continue
                try:
                    return int(season_payload.get("episode_count") or season_payload.get("episodes") or 0)
                except (TypeError, ValueError):
                    return None
        return None






    def accepts_agent_unit_args(self, *, season: int | None = None, episode: int | None = None, **_: Any) -> bool:
        """TV understands season/episode arguments from the agent schema."""
        return season is not None or episode is not None

    async def build_agent_search_labels(
        self,
        item: Any,
        *,
        season: int | None = None,
        episode: int | None = None,
        language: str | None = None,
        search_scope: str | None = None,
        context: Any | None = None,
    ) -> list[str | None]:
        """TV-owned search fanout for interactive agent requests.

        ``search_scope`` is a category-neutral phase hint from the assistant.
        TV interprets it inside the category boundary: pack-preferred requests
        search a season pack before fanning out to individual episodes; pack-only
        requests never fan out.
        """
        scope = str(search_scope or "default").strip().lower()
        if season is not None and episode is not None:
            return [f"S{int(season):02d}E{int(episode):02d}"]
        if season is None:
            if scope in {"bundle_only", "bundle_preferred", "season_pack_only", "season_pack_preferred"}:
                resolved = await self.resolve_agent_pack_season(item, context) if context is not None else None
                if resolved is not None:
                    season = int(resolved)
                else:
                    return [None]
            else:
                return [None]

        season_label = f"Season {int(season)}"
        if scope in {"bundle_only", "bundle_preferred", "season_pack_only", "season_pack_preferred"}:
            return [season_label]
        if scope == "individual_units_only":
            labels: list[str | None] = []
        else:
            labels = [season_label]
        for ep in await self._infer_missing_or_likely_episodes_for_agent(item, int(season), context):
            labels.append(f"S{int(season):02d}E{int(ep):02d}")
        return labels or [season_label]

    async def search_agent_candidates(
        self,
        item: Any,
        *,
        season: int | None = None,
        episode: int | None = None,
        language: str | None = None,
        search_scope: str | None = None,
        context: Any,
    ) -> tuple[list[Any], str]:
        """Run TV interactive search with staged pack-preferred fallback.

        When the user asks to prefer a season pack, TV first searches only the
        season-pack label. If that yields no acceptable ranked candidate, it then
        falls back to the inferred individual episode labels. This keeps pack-first
        UX without pretending packs are always available.
        """
        scope = str(search_scope or "default").strip().lower()
        if scope in {"bundle_preferred", "bundle_only", "season_pack_preferred", "season_pack_only"} and season is None and episode is None:
            resolved = await self.resolve_agent_pack_season(item, context)
            if resolved is not None:
                season = int(resolved)

        if scope in {"bundle_preferred", "season_pack_preferred"} and season is not None and episode is None:
            pack_results, pack_summary = await self._run_agent_pack_queries(
                item, int(season), language=language, context=context, summary_suffix="pack preferred",
            )
            if pack_results:
                return pack_results, pack_summary
            episode_labels = []
            for ep in await self._infer_missing_or_likely_episodes_for_agent(item, int(season), context):
                episode_labels.append(f"S{int(season):02d}E{int(ep):02d}")
            if not episode_labels:
                return [], f"Season {int(season)} pack (no acceptable pack; no episode fallback targets)"
            return await self._run_agent_labels(item, episode_labels, language=language, season=season, episode=episode, context=context, summary_suffix="pack unavailable; individual episodes")
        if scope in {"bundle_only", "season_pack_only"} and season is not None and episode is None:
            return await self._run_agent_pack_queries(
                item, int(season), language=language, context=context, summary_suffix="pack only",
            )
        if season is not None and episode is not None:
            exact_label = f"S{int(season):02d}E{int(episode):02d}"
            exact_results, exact_summary = await self._run_agent_labels(
                item, [exact_label], language=language, season=season, episode=episode, context=context,
            )
            if exact_results:
                return exact_results, exact_summary
            pack_results, pack_summary = await self._run_agent_episode_pack_fallback(
                item, int(season), int(episode), language=language, context=context,
            )
            summary = f"{exact_summary}; {pack_summary}" if pack_summary else exact_summary
            return pack_results, summary

        labels = await self.build_agent_search_labels(
            item, season=season, episode=episode, language=language, search_scope=search_scope, context=context,
        )
        return await self._run_agent_labels(item, labels, language=language, season=season, episode=episode, context=context)

    async def _run_agent_labels(
        self,
        item: Any,
        labels: list[str | None],
        *,
        language: str | None,
        season: int | None,
        episode: int | None,
        context: Any,
        summary_suffix: str | None = None,
    ) -> tuple[list[Any], str]:
        """Run pipeline searches for already-decided TV labels and rank once."""
        merged: list[Any] = []
        seen: set[str] = set()
        for label in labels or [None]:
            results = await context.pipeline.run_search(item, label, mode="llm", language=language)
            for result in results or []:
                magnet = getattr(result, "magnet", None) or ""
                identity = str(magnet or f"{getattr(result, 'source', '')}|{getattr(result, 'title', '')}").lower()
                if identity in seen:
                    continue
                seen.add(identity)
                merged.append(result)
        ranked = await self.rank_agent_search_results(
            merged, item=item, language=language, season=season, episode=episode, context=context,
        )
        label_summary = ", ".join(str(label) for label in labels if label) or item.key
        if summary_suffix:
            label_summary = f"{label_summary} ({summary_suffix})"
        return ranked, label_summary

    async def _run_agent_episode_pack_fallback(
        self,
        item: Any,
        season: int,
        episode: int,
        *,
        language: str | None,
        context: Any,
    ) -> tuple[list[Any], str]:
        """Search season/series packs when an exact TV episode has no row.

        This keeps the fallback inside the TV category.  Generic search only sees
        category-owned candidates and unit descriptors; TV owns the idea that a
        requested SxxEyy can be satisfied by file-selecting one payload from a
        season pack.
        """
        queries = await self.agent_pack_search_queries(item, season, language=None, context=context)
        merged: list[Any] = []
        seen: set[str] = set()
        quality_profile = getattr(item, "quality", None)
        for query in queries:
            try:
                results = await context.aggregator.search(
                    query,
                    category=self.category_id,
                    quality_profile=quality_profile,
                    preferred_language=language,
                )
            except Exception as exc:
                logger.debug(f"TV episode pack fallback query failed for {item.key}: {query}: {exc}")
                continue
            for result in results or []:
                if not getattr(result, "magnet", None):
                    continue
                if not self._title_matches_requested_series(str(getattr(result, "title", "") or ""), str(getattr(item, "key", "") or "")):
                    continue
                contains = False
                try:
                    contains = bool(self._bundle_contains_episode(str(getattr(result, "title", "") or ""), season, episode))
                except Exception:
                    contains = False
                if not contains:
                    continue
                identity = str(getattr(result, "magnet", None) or f"{getattr(result, 'source', '')}|{getattr(result, 'title', '')}").lower()
                if identity in seen:
                    continue
                seen.add(identity)
                merged.append(result)
        ranked = await self.rank_agent_search_results(
            merged, item=item, language=language, season=season, episode=episode, context=context,
        )
        return ranked, f"S{season:02d}E{episode:02d} exact unavailable; searched season-pack fallback: " + " | ".join(queries[:8])


    async def _run_agent_pack_queries(
        self,
        item: Any,
        season: int,
        *,
        language: str | None,
        context: Any,
        summary_suffix: str | None = None,
    ) -> tuple[list[Any], str]:
        """Search TV season packs using category-declared release schemas.

        The generic search pipeline can search one opaque label at a time.
        Season packs need several release-name schemas: bare season tokens,
        complete/full/pack words, episode-range notation derived from provider
        episode counts, and whole-series containers that can later be file-
        selected after torrent metadata arrives.  TV owns those schemas here.
        """
        queries = await self.agent_pack_search_queries(item, season, language=language, context=context)
        merged: list[Any] = []
        seen: set[str] = set()
        quality_profile = getattr(item, "quality", None)
        for query in queries:
            try:
                results = await context.aggregator.search(
                    query,
                    category=self.category_id,
                    quality_profile=quality_profile,
                    preferred_language=language,
                )
            except Exception as exc:
                logger.debug(f"TV pack query failed for {item.key}: {query}: {exc}")
                continue
            for result in results or []:
                if not getattr(result, "magnet", None):
                    continue
                if not self._is_relevant_season_pack_result(result, season, item=item):
                    continue
                identity = str(getattr(result, "magnet", None) or f"{getattr(result, 'source', '')}|{getattr(result, 'title', '')}").lower()
                if identity in seen:
                    continue
                seen.add(identity)
                merged.append(result)
        ranked = await self.rank_agent_search_results(
            merged, item=item, language=language, season=season, episode=None, context=context,
        )
        summary = f"Season {int(season)} pack queries: " + " | ".join(queries[:8])
        if summary_suffix:
            summary = f"{summary} ({summary_suffix})"
        return ranked, summary

    async def resolve_agent_pack_season(self, item: Any, context: Any | None) -> int | None:
        """Resolve the season implied by a latest/last-season pack request.

        The answer is category-owned: prefer provider metadata, then cached item
        progress.  Shared planner code must not guess that a TV show is on a
        particular season.
        """
        if context is None:
            return self._safe_positive_int(getattr(item, "last_season", None))
        latest = await self._latest_season_from_metadata(getattr(item, "key", ""), context)
        if latest is not None:
            return latest
        enricher = getattr(context, "metadata_enricher", None)
        settings = getattr(context, "settings", None)
        if enricher and self.metadata_provider_enabled(settings, "tmdb", True):
            try:
                record = await enricher.enrich_series(getattr(item, "key", ""))
                metadata = self.normalize_taste_metadata_payload(self.create_item(getattr(item, "key", "")), record, "tmdb_tv")
                latest = self._latest_season_from_payload(metadata or {})
                if latest is not None:
                    return latest
            except Exception as exc:
                logger.debug(f"Unable to refresh TV metadata for latest season: {exc}")
        if getattr(context, "db", None):
            try:
                progress = await context.db.media.get_item_progress(self.category_id, getattr(item, "key", "")) or {}
                progress_season = self._safe_positive_int(progress.get("last_season"))
                if progress_season:
                    return progress_season
            except Exception:
                pass
        return self._safe_positive_int(getattr(item, "last_season", None))

    async def _latest_season_from_metadata(self, title: str, context: Any) -> int | None:
        if not getattr(context, "db", None):
            return None
        try:
            rows = await context.db.media.get_category_metadata(self.category_id, title)
        except Exception:
            rows = []
        seasons: list[int] = []
        for row in rows or []:
            latest = self._latest_season_from_payload(row.get("metadata") or {})
            if latest is not None:
                seasons.append(latest)
        return max(seasons) if seasons else None

    @staticmethod
    def _latest_season_from_payload(payload: dict[str, object]) -> int | None:
        seasons = payload.get("seasons") or []
        values: list[int] = []
        if isinstance(seasons, list):
            for season_payload in seasons:
                if not isinstance(season_payload, dict):
                    continue
                try:
                    number = int(season_payload.get("season_number") or season_payload.get("number") or -1)
                except (TypeError, ValueError):
                    continue
                if number > 0:
                    values.append(number)
        for key in ("latest_season", "number_of_seasons", "last_season"):
            try:
                value = int(payload.get(key) or 0)
            except (TypeError, ValueError, AttributeError):
                value = 0
            if value > 0:
                values.append(value)
        return max(values) if values else None

    async def agent_pack_search_queries(
        self,
        item: Any,
        season: int,
        *,
        language: str | None,
        context: Any | None,
    ) -> list[str]:
        """Return TV-owned pack search schemas for a season request.

        Episode-range schemas are derived from provider metadata for that
        particular season.  If metadata cannot tell us the terminal episode,
        only broad season/series schemas are emitted.
        """
        title = getattr(item, "key", "")
        s = int(season)
        episode_count = await self._expected_episode_count(title, s, {}, context) if context is not None else None
        latest_season = await self.resolve_agent_pack_season(item, context) if context is not None else None
        raw: list[str] = [
            f"{title} S{s:02d}",
            f"{title} Season {s}",
            f"{title} S{s:02d} Complete",
            f"{title} Season {s} Complete",
            f"{title} S{s:02d} Pack",
            f"{title} Season {s} Pack",
            f"{title} S{s:02d} Full",
        ]
        if episode_count and episode_count > 1:
            raw.extend([
                f"{title} S{s:02d}E01-E{int(episode_count):02d}",
                f"{title} S{s:02d}E01-{int(episode_count):02d}",
                f"{title} {s}x01-{int(episode_count):02d}",
            ])
        if latest_season and int(latest_season) >= s:
            raw.extend([
                f"{title} S01-S{int(latest_season):02d}",
                f"{title} Complete Series",
                f"{title} All Seasons",
            ])
        seen: set[str] = set()
        queries: list[str] = []
        for query in raw:
            query = self.search._append_language(query, language)
            normalized = query.strip().lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            queries.append(query.strip())
        return queries[:12]

    def _is_relevant_season_pack_result(self, result: Any, season: int, item: Any | None = None) -> bool:
        """Return whether a detected TV bundle can contain the requested season."""
        from src.core.categories.tv_bundle import TVBundleKnowledge

        title = str(getattr(result, "title", "") or "")
        if item is not None and not self._title_matches_requested_series(title, str(getattr(item, "key", "") or "")):
            return False
        pack = TVBundleKnowledge.detect_season_pack(title)
        if not pack:
            return False
        if pack.get("pack_type") == "series_complete":
            return True
        start = self._safe_positive_int(pack.get("season_start")) or self._safe_positive_int(pack.get("season"))
        end = self._safe_positive_int(pack.get("season_end")) or start
        return bool(start is not None and end is not None and int(start) <= int(season) <= int(end))

    @staticmethod
    def _title_matches_requested_series(result_title: str, requested_title: str) -> bool:
        """Return true when a torrent title actually names the requested show.

        Short TV names such as "The Boys" are easy to over-match against
        unrelated shows like "The Hardy Boys" if ranking only checks a common
        noun. Require the requested title phrase at a token boundary before
        considering season-pack semantics.
        """
        import re
        requested = re.sub(r"[^a-z0-9]+", " ", str(requested_title or "").lower()).strip()
        result = re.sub(r"[^a-z0-9]+", " ", str(result_title or "").lower()).strip()
        if not requested:
            return True
        if re.search(rf"(?:^| ){re.escape(requested)}(?: |$)", result):
            return True
        # Conservative fallback for titles with a leading article removed by an
        # indexer. Do not allow single-token fallback; it caused Hardy Boys to
        # match The Boys.
        tokens = requested.split()
        if tokens and tokens[0] in {"the", "a", "an"}:
            tokens = tokens[1:]
        if len(tokens) >= 2:
            phrase = " ".join(tokens)
            return bool(re.search(rf"(?:^| ){re.escape(phrase)}(?: |$)", result))
        return False

    @staticmethod
    def _is_season_pack_result(result: Any) -> bool:
        """Return True when a TV result title is a bundle/season pack."""
        from src.core.categories.tv_bundle import TVBundleKnowledge

        return bool(TVBundleKnowledge.detect_season_pack(str(getattr(result, "title", "") or "")))

    async def _infer_missing_or_likely_episodes_for_agent(self, item: Any, season: int, context: Any | None) -> list[int]:
        """Infer TV episode fanout targets from category metadata and library state."""
        if context is None or not getattr(context, "db", None):
            # Without metadata/library state, do not invent a 10-episode season.
            return []

        def _as_int(value: object) -> int:
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        # Canonical TV objects store physical local files as units; logical
        # episodes are built by the TV category from those file facts.  Do not
        # query the retired ``episode`` unit type here or the agent may miss
        # files produced by the canonical library-object pipeline.
        downloaded = await context.db.media.list_category_units(
            self.category_id,
            item.key,
            status="downloaded",
        )
        downloaded_set = {
            _as_int(unit.get("episode"))
            for unit in downloaded or []
            if _as_int(unit.get("season")) == season and _as_int(unit.get("episode")) > 0
        }
        try:
            aired = await self._aired_episode_numbers_for_season(item.key, season, context)
            if aired:
                return [ep for ep in sorted(aired) if ep not in downloaded_set]
        except Exception as exc:
            logger.debug(f"Unable to infer aired TV episodes for {item.key} S{season:02d}: {exc}")

        # Do not fan out to provider-declared episode counts unless air dates
        # are known. Provider season metadata often includes future/unaired
        # episodes; treating those as downloadable made the assistant search for
        # episodes that do not exist yet. With no air-date data, only search
        # gaps inside the locally observed range.
        progress = await context.db.media.get_item_progress(self.category_id, item.key) or {}
        last_season = _as_int(progress.get("last_season") or season)
        last_episode = _as_int(progress.get("last_episode")) if last_season == season else 0
        observed_count = max(last_episode, max(downloaded_set or {0}))
        return [ep for ep in range(1, int(observed_count) + 1) if ep not in downloaded_set]

    async def rank_agent_search_results(
        self,
        results: list[Any],
        *,
        item: Any,
        language: str | None = None,
        season: int | None = None,
        episode: int | None = None,
        context: Any | None = None,
    ) -> list[Any]:
        """Apply TV hard filters and bitrate-aware pre-ranking before LLM prose.

        This is not the final semantic choice.  The goal is to keep obviously
        wrong rows out and present the LLM/user with candidates ordered by the
        user's existing per-show quality profile: language, exact unit/pack
        coverage, resolution, bitrate target, seeders.  Smaller replacements
        must search within the preferred bitrate/resolution band first; they
        must not implicitly mean "downgrade to 720p".
        """
        from src.utils.quality import QualityAnalyzer, extract_quality_tags

        item_quality = getattr(item, "quality", None)
        global_quality = getattr(getattr(context, "settings", None), "default_quality", None)
        preferred_resolution = getattr(item_quality, "preferred_resolution", None)
        global_resolution = getattr(global_quality, "preferred_resolution", None)
        if global_resolution and (
            not preferred_resolution
            or QualityAnalyzer.rank_resolution(global_resolution) > QualityAnalyzer.rank_resolution(preferred_resolution)
        ):
            preferred_resolution = global_resolution
        max_file_size_mb = getattr(item_quality, "max_file_size_mb", None)
        preferred_bitrate_kbps = self._preferred_bitrate_for_agent(item, context)
        max_bitrate_kbps = self._max_bitrate_for_agent(item, context)
        constraints = self._search_constraints_from_context(context)
        if constraints.get("target_bitrate_kbps"):
            preferred_bitrate_kbps = float(constraints.get("target_bitrate_kbps") or 0) or preferred_bitrate_kbps
        if constraints.get("preferred_bitrate_kbps"):
            preferred_bitrate_kbps = float(constraints.get("preferred_bitrate_kbps") or 0) or preferred_bitrate_kbps
        if constraints.get("current_bitrate_kbps") and not preferred_bitrate_kbps:
            preferred_bitrate_kbps = float(constraints.get("current_bitrate_kbps") or 0) or preferred_bitrate_kbps
        if constraints.get("max_bitrate_kbps"):
            max_bitrate_kbps = float(constraints.get("max_bitrate_kbps") or 0) or max_bitrate_kbps
        preferred = (language or "").lower()
        constraint_resolution = constraints.get("required_resolution") or constraints.get("preferred_resolution")
        if constraint_resolution:
            preferred_resolution = str(constraint_resolution).lower()
        target_episode_size_mb = constraints.get("target_size_mb") or self._target_episode_size_from_context(item, season, context)
        max_constraint_size_mb = constraints.get("max_size_mb")
        min_constraint_size_mb = constraints.get("min_size_mb")
        current_size_mb = constraints.get("current_size_mb")
        size_mode = str(constraints.get("size_mode") or "").lower()
        preserve_resolution = bool(constraints.get("preserve_resolution") or constraints.get("prefer_current_resolution") or constraints.get("smaller_than_current"))

        eligible: list[tuple[Any, dict[str, Any], int]] = []
        for result in results or []:
            if not getattr(result, "magnet", None):
                continue
            tags = extract_quality_tags(getattr(result, "title", "") or "")
            if tags.get("content_blacklisted") or tags.get("release_type") in {"cam", "ts", "hdcam", "camrip", "tsrip", "hdts"}:
                continue
            languages = {self._canonical_language_token(lang) for lang in (tags.get("languages") or [])}
            title_lower = (getattr(result, "title", "") or "").lower()
            preferred_token = self._canonical_language_token(preferred) if preferred else ""
            title_has_preferred = bool(preferred_token and self._title_has_language_token(title_lower, preferred_token))
            # Language evidence is not reliable enough to be a hard display
            # filter.  Unknown/non-preferred releases should be shown to the LLM
            # and user with blockers/warnings, not hidden as "no results".
            # Queueing remains guarded later by candidate annotations and
            # explicit user confirmation.
            resolution = tags.get("resolution")
            if preferred_resolution and resolution:
                result_rank = QualityAnalyzer.rank_resolution(resolution)
                preferred_rank = QualityAnalyzer.rank_resolution(preferred_resolution)
                if result_rank > preferred_rank:
                    continue
                if constraints.get("required_resolution") and result_rank != preferred_rank:
                    continue
            per_ep_bytes = self._per_episode_size_bytes_for_agent(result)
            size_mb = (per_ep_bytes or 0) / (1024 * 1024) if per_ep_bytes else 0
            bitrate_kbps = self._estimated_episode_bitrate_kbps_for_agent(result)
            if max_file_size_mb and per_ep_bytes and per_ep_bytes > max_file_size_mb * 1024 * 1024:
                continue
            # Bitrate is the user's quality/size preference surface.  Treat an
            # explicit/profile max as a soft-hard ceiling with modest encoder
            # variance; never satisfy it by silently lowering resolution in the
            # query ladder.
            if max_bitrate_kbps and bitrate_kbps and bitrate_kbps > float(max_bitrate_kbps) * 1.15:
                continue
            if max_constraint_size_mb and size_mb and size_mb > float(max_constraint_size_mb):
                continue
            if min_constraint_size_mb and size_mb and size_mb < float(min_constraint_size_mb):
                continue
            if constraints.get("smaller_than_current") and current_size_mb and size_mb and size_mb >= float(current_size_mb) * 0.98:
                continue
            if target_episode_size_mb and per_ep_bytes and self._is_season_pack_result(result):
                size_mb = per_ep_bytes / (1024 * 1024)
                # Pack searches use item/local quality evidence to keep likely
                # bundles in a sane per-episode size band.  The 50% tolerance is
                # deliberately broad: it rejects obviously wrong tiny/huge packs
                # while still leaving real-world encoder variance to ranking.
                if not (target_episode_size_mb * 0.5 <= size_mb <= target_episode_size_mb * 1.5):
                    continue
            eligible.append((result, tags, per_ep_bytes or 0))

        def score(item_tuple: tuple[Any, dict[str, Any], int]) -> tuple:
            """Rank eligible TV candidates by language, quality, pack shape, and health."""
            result, tags, per_ep_bytes = item_tuple
            languages = {self._canonical_language_token(lang) for lang in (tags.get("languages") or [])}
            preferred_token = self._canonical_language_token(preferred) if preferred else ""
            title_has_preferred = bool(preferred_token and self._title_has_language_token((getattr(result, "title", "") or "").lower(), preferred_token))
            known_language_mismatch = bool(preferred_token and languages and preferred_token not in languages and not tags.get("is_multi_language") and not title_has_preferred)
            lang_score = 3 if preferred_token and (preferred_token in languages or title_has_preferred) else 2 if tags.get("is_multi_language") else -2 if known_language_mismatch else 0
            resolution = tags.get("resolution")
            res_rank = QualityAnalyzer.rank_resolution(resolution or "") if resolution else 0
            codec = str(tags.get("codec") or "").lower()
            codec_bonus = 1 if codec in {"h265", "x265", "hevc", "av1"} else 0
            size_mb = (per_ep_bytes or 0) / (1024 * 1024) if per_ep_bytes else 0
            bitrate_kbps = self._estimated_episode_bitrate_kbps_for_agent(result)
            target_score = 0.0
            bitrate_score = 0.0
            undersized_penalty = 0
            if preferred_bitrate_kbps and bitrate_kbps:
                # Prefer closeness to the learned/explicit bitrate target.  A
                # smaller replacement is a bitrate tradeoff, not a resolution
                # downgrade command.
                bitrate_score = -(abs(float(bitrate_kbps) - float(preferred_bitrate_kbps)) / max(float(preferred_bitrate_kbps), 1.0))
                if bitrate_kbps < float(preferred_bitrate_kbps) * 0.45:
                    undersized_penalty = -3
            elif target_episode_size_mb and size_mb:
                target_score = -(abs(size_mb - target_episode_size_mb) / max(target_episode_size_mb, 1))
                if size_mb < target_episode_size_mb * 0.45:
                    undersized_penalty = -2
            same_resolution_bonus = 0
            downgrade_penalty = 0
            if preferred_resolution and resolution:
                preferred_rank = QualityAnalyzer.rank_resolution(preferred_resolution)
                result_rank = QualityAnalyzer.rank_resolution(resolution)
                if result_rank == preferred_rank:
                    same_resolution_bonus = 4 if preserve_resolution else 2
                elif preserve_resolution and result_rank < preferred_rank:
                    downgrade_penalty = -5
            smaller_bonus = 0
            if size_mode == "smaller" and current_size_mb and size_mb and size_mb < float(current_size_mb):
                smaller_bonus = min(3.0, (float(current_size_mb) - size_mb) / max(float(current_size_mb), 1.0) * 3.0)
            return (
                lang_score,
                same_resolution_bonus + downgrade_penalty,
                bitrate_score,
                target_score,
                smaller_bonus,
                res_rank,
                undersized_penalty,
                codec_bonus,
                getattr(result, "seeders", None) or 0,
                getattr(result, "quality_score", 0) or 0,
                -abs(size_mb - target_episode_size_mb) if (target_episode_size_mb and size_mb) else (-size_mb if size_mb else 0),
            )

        return [result for result, _, _ in sorted(eligible, key=score, reverse=True)][:60]


    def search_candidate_quality_facts(self, result: Any, *, item: Any | None = None, unit_label: str | None = None, context: Any | None = None) -> dict[str, Any]:
        """Expose TV-owned per-unit size/bitrate facts for tools and UI.

        Torrent totals are often misleading for TV packs.  These facts describe
        the estimated useful episode payload, so the LLM can compare bitrate and
        size tradeoffs without inventing a lower-resolution query.
        """
        per_ep_bytes = self._per_episode_size_bytes_for_agent(result)
        facts: dict[str, Any] = {}
        if per_ep_bytes:
            facts["per_episode_size_bytes"] = per_ep_bytes
            facts["per_episode_size_mb"] = round(per_ep_bytes / (1024 * 1024), 1)
        bitrate = self._estimated_episode_bitrate_kbps_for_agent(result)
        if bitrate:
            facts["estimated_bitrate_kbps"] = int(bitrate)
            facts["bitrate_basis"] = "estimated_from_payload_size_and_tv_runtime"
        return facts

    def _preferred_bitrate_for_agent(self, item: Any, context: Any | None) -> float | None:
        """Return the item's saved/learned preferred bitrate target, if any."""
        quality = getattr(item, "quality", None)
        for attr in ("preferred_bitrate_kbps", "target_bitrate_kbps"):
            value = getattr(quality, attr, None) if quality is not None else None
            try:
                if value and float(value) > 0:
                    return float(value)
            except (TypeError, ValueError):
                pass
        settings = getattr(context, "settings", None) if context is not None else None
        for profile in self._download_profiles_for_agent(settings):
            for key in ("preferred_bitrate_kbps", "target_bitrate_kbps"):
                try:
                    value = profile.get(key) if isinstance(profile, dict) else None
                    if value and float(value) > 0:
                        return float(value)
                except (TypeError, ValueError):
                    pass
        return None

    def _max_bitrate_for_agent(self, item: Any, context: Any | None) -> float | None:
        quality = getattr(item, "quality", None)
        value = getattr(quality, "max_bitrate_kbps", None) if quality is not None else None
        try:
            if value and float(value) > 0:
                return float(value)
        except (TypeError, ValueError):
            pass
        settings = getattr(context, "settings", None) if context is not None else None
        for profile in self._download_profiles_for_agent(settings):
            try:
                value = profile.get("max_bitrate_kbps") if isinstance(profile, dict) else None
                if value and float(value) > 0:
                    return float(value)
            except (TypeError, ValueError):
                pass
        return None

    def _download_profiles_for_agent(self, settings: Any | None) -> list[dict[str, Any]]:
        profiles: list[dict[str, Any]] = []
        if settings is None:
            return profiles
        try:
            profile = self.category_download_profile(settings)
            if isinstance(profile, dict):
                profiles.append(profile)
        except Exception:
            pass
        category_settings = getattr(settings, "category_settings", {}) or {}
        if isinstance(category_settings, dict):
            for key in (self.category_id, "media"):
                value = category_settings.get(key) or {}
                if isinstance(value, dict) and isinstance(value.get("download_profile"), dict):
                    profiles.append(value.get("download_profile") or {})
        return profiles

    @classmethod
    def _estimated_episode_bitrate_kbps_for_agent(cls, result: Any) -> int:
        per_ep_bytes = cls._per_episode_size_bytes_for_agent(result)
        if not per_ep_bytes:
            return 0
        seconds = cls._episode_runtime_seconds_for_agent(str(getattr(result, "title", "") or ""))
        if seconds <= 0:
            return 0
        return int((per_ep_bytes * 8) / 1000 / seconds)

    @staticmethod
    def _episode_runtime_seconds_for_agent(title: str) -> int:
        # TV search results rarely carry reliable runtime metadata.  Use a
        # conservative one-hour-drama default for bitrate comparison.  The goal
        # is relative comparison between candidates for the same episode/show,
        # not exact media probing before download.
        if re.search(r"\b(?:animation|anime|cartoon|sitcom)\b", title or "", re.IGNORECASE):
            return 24 * 60
        return 55 * 60


    @staticmethod
    def _search_constraints_from_context(context: Any | None) -> dict[str, Any]:
        """Return normalized size/resolution constraints attached by the scheduler."""
        raw = getattr(context, "search_constraints", None) if context is not None else None
        return raw if isinstance(raw, dict) else {}

    @staticmethod
    def _parse_target_episode_size_mb(quality_context: str | None) -> float | None:
        if not quality_context:
            return None
        import re
        match = re.search(r"target_episode_size_mb=([0-9]+(?:\.[0-9]+)?)", quality_context)
        if not match:
            return None
        try:
            value = float(match.group(1))
            return value if value > 0 else None
        except ValueError:
            return None

    def _target_episode_size_from_context(self, item: Any, season: int | None, context: Any | None) -> float | None:
        """Read the shared search-pipeline quality reference for TV ranking."""
        try:
            pipeline = getattr(context, "pipeline", None)
            if not pipeline or not hasattr(pipeline, "quality_reference_for_item"):
                return None
            label = f"Season {int(season)}" if season is not None else None
            return self._parse_target_episode_size_mb(pipeline.quality_reference_for_item(item, label))
        except Exception:
            return None

    @staticmethod
    def _canonical_language_token(value: object) -> str:
        token = str(value or "").strip().lower()
        aliases = {
            "italian": "italian", "italiano": "italian", "ita": "italian", "it": "italian",
            "english": "english", "eng": "english", "en": "english",
            "multi": "multi", "multilanguage": "multi", "dual": "multi",
        }
        return aliases.get(token, token)

    @staticmethod
    def _title_has_language_token(title_lower: str, preferred_token: str) -> bool:
        if preferred_token == "italian":
            return bool(re.search(r"(?:^|[\s._\-\[\]()])(?:ita|italian|italiano)(?:$|[\s._\-\[\]()])", title_lower, re.I))
        if preferred_token == "english":
            return bool(re.search(r"(?:^|[\s._\-\[\]()])(?:eng|english)(?:$|[\s._\-\[\]()])", title_lower, re.I))
        return preferred_token in title_lower


    @classmethod
    def _per_episode_size_bytes_for_agent(cls, result: Any) -> int:
        title = getattr(result, "title", "") or ""
        raw_size = getattr(result, "size_bytes", None) or getattr(result, "size", None) or 0
        try:
            size = int(raw_size or 0)
        except (TypeError, ValueError):
            return 0
        if not size:
            return 0
        return int(size / max(cls._detect_episode_count_from_title_for_agent(title), 1))

    @staticmethod
    def _detect_episode_count_from_title_for_agent(title: str) -> int:
        matches = re.findall(r"S\d{1,2}E(\d{1,2})", title or "", re.IGNORECASE)
        if len(matches) > 1:
            return len(set(matches))
        range_match = re.search(r"S\d{1,2}E(\d{1,2})\s*(?:-|E)\s*E?(\d{1,2})", title or "", re.IGNORECASE)
        if range_match:
            start, end = int(range_match.group(1)), int(range_match.group(2))
            if end >= start:
                return max(1, end - start + 1)
        if re.search(r"\b(?:complete|season\s*\d+|S\d{1,2})\b", title or "", re.IGNORECASE) and not re.search(r"S\d{1,2}E\d{1,2}", title or "", re.IGNORECASE):
            return 10
        return 1


