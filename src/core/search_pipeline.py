"""
Search pipeline for LJS.

UNIFIED search pipeline used by both the automated scheduler and the
LLM agent. Single entry point (run_search) with three modes:
  - "fast": regex-only validation, no LLM cost
  - "auto": regex first, LLM fallback, auto-download on match
  - "llm": regex pre-filter + LLM selection, returns candidates for agent

Categories define query patterns, validation rules, and LLM prompt structure.
"""

from typing import TYPE_CHECKING, Optional
from types import SimpleNamespace
from loguru import logger
from src.core.models import CategoryItem, DownloadPriority, SearchResult, QualityProfile

if TYPE_CHECKING:
    from src.search.aggregator import SearchAggregator
    from src.core.downloader import DownloadManager
    from src.core.database import Database
    from src.core.librarian import Librarian
    from src.core.categories.registry import CategoryRegistry
    from src.core.config import SettingsManager


def _build_query(item: CategoryItem, episode_label: str | None,
                 language: str, category) -> str:
    """Build a search query by delegating to the owning category.

    ``episode_label`` is a historical parameter name.  The search pipeline now
    treats it as an opaque category unit label; only the category may interpret
    whether it means an episode, version, volume, edition, or something else.
    """
    if category and hasattr(category, 'build_search_query'):
        query = category.build_search_query(item, episode_label, language)
        if query:
            return query
    return _inline_query(item.key, episode_label, language)


def _inline_query(name: str, episode_label: str | None, language: str) -> str:
    """Fallback inline query builder when category patterns are unavailable."""
    query = f'{name} {episode_label}' if episode_label else name
    if language and language.lower() != 'english':
        query += f' {language}'
    return query.strip()


class SearchPipeline:
    """Unified search pipeline for both automated and LLM-triggered flows."""

    def __init__(
        self,
        aggregator: "SearchAggregator",
        downloader: "DownloadManager",
        db: "Database",
        librarian: "Librarian",
        category_registry: "CategoryRegistry",
        torrent_selection: object | None = None,
        settings_manager: "SettingsManager | None" = None,
    ) -> None:
        self._aggregator = aggregator
        self._downloader = downloader
        self._db = db
        self._librarian = librarian
        self._categories = category_registry
        self._torrent_selection = torrent_selection
        self._settings_manager = settings_manager
        self._scheduler = None

    def set_scheduler(self, scheduler: object) -> None:
        """Inject the scheduler coordinator."""
        self._scheduler = scheduler

    def _category_search_context(self) -> SimpleNamespace:
        """Return generic collaborators categories may use for search decisions."""
        return SimpleNamespace(
            db=self._db,
            pipeline=self,
            scheduler=self._scheduler,
            settings=self._settings_manager.settings if self._settings_manager else None,
            settings_manager=self._settings_manager,
            category_registry=self._categories,
        )

    async def run_search(
        self, item: CategoryItem, episode_label: str | None = None,
        mode: str = 'auto', language: str | None = None,
    ) -> SearchResult | list[SearchResult] | None:
        """Single entry point for all torrent searches.

        Args:
            item: The tracked item to search for.
            episode_label: Historical name for an opaque category unit label.
            mode: 'fast' (regex only), 'auto' (regex + LLM fallback + download),
                  'llm' (regex pre-filter + LLM rank, return candidates).
            language: Override language. Defaults to item.language.

        Returns:
            mode='fast'/'auto': SearchResult | None
            mode='llm': list[SearchResult] | None
        """
        settings = self._settings_manager.settings
        category_id = item.item_type
        category = self._categories.get(category_id)
        category_context = self._category_search_context()
        category_profile = category.category_download_profile(settings) if category and hasattr(category, "category_download_profile") else {}
        target_lang = self._normalize_category_search_language(
            category,
            language or getattr(item, 'language', '') or category_profile.get('language') or settings.language,
            explicit=language is not None,
        )

        if category and hasattr(category, "prepare_search_item"):
            # Search preparation can be category-specific (for example size
            # limits derived from local library statistics).  The pipeline only
            # offers context; it must not branch on concrete category IDs.
            item = await category.prepare_search_item(
                item,
                settings=settings,
                scan_result=self._scheduler.get_last_scan_result() if getattr(self, '_scheduler', None) else None,
            )
            target_lang = self._normalize_category_search_language(
                category,
                language or getattr(item, 'language', '') or category_profile.get('language') or settings.language,
                explicit=language is not None,
            )

        logger.info(f'Search: {item.key} {episode_label or ""} ({mode} mode, lang={target_lang or "none"})')

        # Build query from category patterns
        query = _build_query(item, episode_label, target_lang, category)

        results = await self._aggregator.search(
            query, category=category_id, quality_profile=self._search_quality_profile(category, item),
            preferred_language=target_lang,
        )

        primary_timed_out = False
        if not results:
            primary_timed_out = self._last_provider_search_timed_out()
            if primary_timed_out:
                logger.warning(
                    f'Search: provider timed out for {query}; skipping query-ladder fan-out for this attempt so one slow Jackett/indexer backend does not freeze the UI for minutes.'
                )
            else:
                logger.info(f'Search: no primary results for {query}; trying category alternatives when available')
        else:
            # Step 1: category-owned pre-filter. The pipeline does not parse the
            # opaque unit label; it asks the category whether the candidate matches.
            validated = self._validate_results_for_request(results, category, item, episode_label)

            # Step 2: Route to LLM or return based on mode
            if mode == 'fast' and validated:
                return validated[0]

            if mode == 'llm' and validated:
                # Return validated candidates for LLM agent to review. If LLM
                # selection service is available, use it to rank.
                if self._torrent_selection:
                    ranked = await self._safe_llm_rank(validated, item, episode_label or '', target_lang)
                    return ranked or validated
                return validated

            # mode == 'auto': candidates are structurally relevant, but the
            # owning category may still prefer LLM evaluation for nuanced
            # release choice (for example exact TV episodes vs season packs,
            # language uncertainty, source health, and size tradeoffs).
            if mode == 'auto' and validated:
                if self._should_llm_rank_validated(category, item, episode_label, mode, validated):
                    ranked = await self._safe_llm_rank(validated, item, episode_label or '', target_lang)
                    if ranked:
                        return ranked[0]
                return validated[0]

            # LLM fallback when regex found nothing.  This remains inside the
            # non-empty branch: if the provider returned zero rows, the useful
            # next step is the category's query ladder, not asking the LLM to
            # rank an empty set.
            if self._torrent_selection and results and mode == 'auto':
                logger.info(f'Search: regex found no match for {query}, trying LLM selection')
                ranked = await self._safe_llm_rank(results, item, episode_label or '', target_lang)
                if ranked:
                    return ranked[0]

        # Try alternative queries after either an empty primary query or a
        # non-empty primary result set with no category-valid candidate.  This
        # is important for media categories where language is a preference and
        # ranking facet: a strict first query such as "Show S05E10 ITA" may
        # return zero even though the exact episode exists under bare/MULTI
        # titles that should be presented or require confirmation.
        if category and episode_label and not primary_timed_out:
            alt_queries = category.build_alternative_search_queries(item, episode_label, target_lang)
            seen_queries = {query.strip().casefold()}
            alt_validated: list[SearchResult] = []
            for alt_query in alt_queries:
                alt_query = str(alt_query or "").strip()
                if not alt_query or alt_query.casefold() in seen_queries:
                    continue
                seen_queries.add(alt_query.casefold())
                logger.info(f'Search: trying alternative query: {alt_query}')
                alt_results = await self._aggregator.search(
                    alt_query, category=category_id,
                    quality_profile=self._search_quality_profile(category, item),
                    preferred_language=target_lang,
                )
                if not alt_results:
                    continue

                validated = self._validate_results_for_request(alt_results, category, item, episode_label)
                if not validated and self._torrent_selection and mode == 'auto':
                    ranked = await self._safe_llm_rank(alt_results, item, episode_label or '', target_lang)
                    if ranked:
                        logger.info(f'Search: alternative query selected LLM match from {alt_query}')
                        return ranked[0]
                    continue

                if not validated:
                    continue

                if mode == 'llm':
                    alt_validated.extend(validated)
                    continue

                if self._should_llm_rank_validated(category, item, episode_label, mode, validated):
                    ranked = await self._safe_llm_rank(validated, item, episode_label or '', target_lang)
                    if ranked:
                        logger.info(f'Search: alternative query selected LLM-ranked match from {alt_query}: {ranked[0].title}')
                        return ranked[0]
                logger.info(f'Search: alternative query found match: {validated[0].title}')
                return validated[0]

            if mode == 'llm' and alt_validated:
                if self._torrent_selection:
                    ranked = await self._safe_llm_rank(alt_validated, item, episode_label or '', target_lang)
                    return ranked or alt_validated
                return alt_validated

        if primary_timed_out:
            logger.warning(f'Search: no suitable results found for {query} because the primary provider timed out before returning candidates.')
        else:
            logger.info(f'Search: no suitable results found for {query}')
        return None

    @staticmethod
    def _normalize_category_search_language(category: object | None, language: str | None, *, explicit: bool = False) -> str | None:
        """Return the category-approved search language, if any."""
        value = str(language or "").strip()
        if "," in value:
            value = next((part.strip() for part in value.split(",") if part.strip()), "")
        if not value:
            return None
        normalizer = getattr(category, "normalize_search_language", None)
        if callable(normalizer):
            try:
                return normalizer(value, explicit=explicit)
            except TypeError:
                return normalizer(value)
        return value

    @staticmethod
    def _search_quality_profile(category: object | None, item: CategoryItem) -> QualityProfile | None:
        """Return the category-owned quality profile for provider ranking."""
        uses_global = getattr(category, "uses_global_quality_profile", None)
        if callable(uses_global):
            try:
                if not uses_global():
                    return QualityProfile(preferred_resolution="", preferred_codecs=[])
            except Exception:
                pass
        return getattr(item, 'quality', None)

    def _build_alternative_queries(self, item, episode_label, language, category):
        """Compatibility wrapper for category-owned alternative queries."""
        if category and hasattr(category, 'build_alternative_search_queries'):
            return category.build_alternative_search_queries(item, episode_label, language)
        return []

    def _should_llm_rank_validated(
        self,
        category: object | None,
        item: CategoryItem,
        episode_label: str | None,
        mode: str,
        validated: list[SearchResult],
    ) -> bool:
        """Ask the category whether structurally valid candidates still need LLM choice.

        The deterministic layer should only prove that candidates are plausible
        for the requested category unit. It should not pretend to know all
        release-group, bundle, language, and per-file tradeoffs. Categories can
        opt in to LLM selection while keeping generic search category-agnostic.
        """
        if not self._torrent_selection or len(validated) <= 1:
            return False
        decider = getattr(category, "prefer_llm_search_selection", None)
        if not callable(decider):
            return False
        try:
            return bool(decider(item=item, unit_label=episode_label, mode=mode, candidates=validated))
        except TypeError:
            try:
                return bool(decider(item, episode_label, mode))
            except Exception:
                return False
        except Exception as exc:
            logger.debug(f"Category LLM search-selection preference failed for {getattr(item, 'key', '?')}: {exc}")
            return False

    def _last_provider_search_timed_out(self) -> bool:
        """Return whether the most recent aggregate provider search timed out."""
        checker = getattr(self._aggregator, "last_search_timed_out", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                return False
        diagnostics = getattr(self._aggregator, "provider_diagnostics", {})
        if callable(diagnostics):
            try:
                diagnostics = diagnostics()
            except Exception:
                diagnostics = {}
        return any("timeout" in str(getattr(diag, "error", "") or "").casefold() for diag in (diagnostics or {}).values())


    @staticmethod
    def _validate_results_for_request(results: list[SearchResult], category: object | None, item: CategoryItem, episode_label: str | None) -> list[SearchResult]:
        """Apply structural and category-owned request validation to provider results."""
        validated: list[SearchResult] = []
        for result in results:
            if not result.magnet or result.quality_score < 0.5:
                continue
            if category and not category.validate_search_result_for_request(result, item, episode_label):
                continue
            validated.append(result)
        return validated

    def _effective_quality_profile(self, item: CategoryItem) -> QualityProfile | None:
        """Return the item's quality profile with a safe global resolution floor.

        Round 5/6 accidentally made this helper call itself, which caused
        ``maximum recursion depth exceeded`` whenever the LLM torrent ranker was
        invoked.  Keep this method deliberately simple and side-effect-free:
        start from the item profile, fall back to the global default profile,
        then copy before applying the global preferred-resolution floor.
        """
        settings = self._settings_manager.settings if self._settings_manager else None
        category = self._categories.get(getattr(item, 'item_type', '')) if self._categories else None
        uses_global = getattr(category, "uses_global_quality_profile", None)
        if callable(uses_global):
            try:
                if not uses_global():
                    return None
            except Exception:
                pass
        profile = getattr(item, 'quality', None) or (getattr(settings, 'default_quality', None) if settings else None)
        if not profile:
            return None

        global_profile = getattr(settings, 'default_quality', None) if settings else None
        global_resolution = getattr(global_profile, 'preferred_resolution', None)
        current_resolution = getattr(profile, 'preferred_resolution', None)
        if not global_resolution:
            return profile

        try:
            from src.utils.quality import QualityAnalyzer
            if (not current_resolution) or QualityAnalyzer.rank_resolution(global_resolution) > QualityAnalyzer.rank_resolution(current_resolution):
                if hasattr(profile, 'model_copy'):
                    profile = profile.model_copy(deep=True)
                else:
                    from copy import deepcopy
                    profile = deepcopy(profile)
                profile.preferred_resolution = global_resolution
        except Exception as exc:
            logger.debug(f"Quality profile resolution-floor merge failed for {item.key}: {exc}")
        return profile


    async def _safe_llm_rank(
        self, candidates: list[SearchResult], item: CategoryItem,
        episode_label: str, language: str,
    ) -> list[SearchResult] | None:
        """LLM-rank candidates without letting ranker failures kill search.

        Search already found provider results at this point.  If the optional
        LLM ranking layer fails, the caller should still receive deterministic
        candidates instead of an empty assistant reply or a failed plan.
        """
        try:
            return await self._llm_rank(candidates, item, episode_label, language)
        except RecursionError as exc:
            logger.error(f"Torrent LLM ranker recursed for {item.key} {episode_label}: {exc}; using unranked candidates")
            return None
        except Exception as exc:
            logger.warning(f"Torrent LLM ranker failed for {item.key} {episode_label}: {exc}; using unranked candidates")
            return None


    def quality_reference_for_item(self, item: CategoryItem, episode_label: str | None = None) -> str:
        """Return category-owned library quality context for rankers/LLMs.

        This method remains as the public pipeline accessor used by older
        ranking helpers, but it now delegates to the category.  Do not add
        domain-specific size/bitrate heuristics here.
        """
        category = self._categories.get(getattr(item, 'item_type', '')) if self._categories else None
        if category and hasattr(category, 'quality_reference_for_search'):
            return category.quality_reference_for_search(item, episode_label, self._category_search_context())
        return ""

    async def _llm_rank(
        self, candidates: list[SearchResult], item: CategoryItem,
        episode_label: str, language: str,
    ) -> list[SearchResult] | None:
        """Use TorrentSelectionService to LLM-rank candidates."""
        if not self._torrent_selection or not candidates:
            return None

        profile = self._effective_quality_profile(item)
        quality_context = self.quality_reference_for_item(item, episode_label)

        result = await self._torrent_selection.select_best_for_category(
            category_id=item.item_type,
            item_id=item.key,
            item_display_name=item.key,
            unit_key=episode_label,
            unit_request={"label": episode_label} if episode_label else {},
            results=candidates,
            preferred_language=language,
            quality_context=quality_context,
            require_magnet=True,
            quality_profile=profile,
        )
        if result:
            result_title = result.get('title', '')
            for c in candidates:
                if c.title == result_title:
                    return [c] + [x for x in candidates if x.title != result_title]
        return None


    async def run_discovery(self, item: CategoryItem, episode_label: str | None = None,
                            force: bool = False, language: str | None = None) -> bool:
        """Auto-download wrapper. Finds best match and downloads it.

        Used by the scheduler and batch download endpoints.
        """
        settings = self._settings_manager.settings
        item_auto = getattr(item, 'auto_download', None)
        can_download = item_auto if item_auto is not None else settings.auto_download

        if not can_download and not force:
            logger.trace(f'Discovery: skipping {item.key} (auto_download disabled)')
            return False

        category_id = item.item_type
        category = self._categories.get(category_id)

        if category and await category.discovery_already_satisfied(item, episode_label, self._category_search_context()):
            logger.debug(f'Discovery: skipping {item.key} {episode_label or ""} (already satisfied by category library state)')
            return False
        if not category and getattr(item, "discovered", False) and not episode_label:
            logger.debug(f'Discovery: skipping already-discovered item {item.key}')
            return False

        # Use provided language, then item language, then settings default
        lang = language or getattr(item, 'language', None) or settings.language
        best = await self.run_search(item, episode_label, mode='auto', language=lang)

        if not best:
            logger.info(f'Discovery: no suitable results for {item.key} {episode_label or ""}')
            return False

        if category and hasattr(category, "candidate_requires_user_language_confirmation"):
            try:
                if category.candidate_requires_user_language_confirmation(best, item, episode_label, lang):
                    logger.info(
                        f'Discovery: refusing to queue {best.title} for {item.key} {episode_label or ""}; '
                        f'candidate does not match preferred language {lang!r} and needs user approval.'
                    )
                    return False
            except Exception as exc:
                logger.debug(f"Discovery language-confirmation check failed for {item.key}: {exc}")

        logger.info(f'Discovery: triggering download for {best.title}')

        descriptor = category.unit_descriptor_from_search_result(best, item, episode_label) if category and hasattr(category, "unit_descriptor_from_search_result") else {}
        bundle_context = category.torrent_bundle_candidate_context(best, item=item, unit_label=episode_label) if category and hasattr(category, "torrent_bundle_candidate_context") else None
        coordinates = descriptor.get("coordinates") if isinstance(descriptor.get("coordinates"), dict) else {}
        if not coordinates and category:
            coordinates = category.download_coordinates_from_search_result(best, item, episode_label)
        season = coordinates.get("season")
        episode = coordinates.get("episode")

        query = _build_query(item, episode_label, getattr(item, 'language', ''), category)

        reason = f"user approved discovery for {query}" if force else f"Auto-discovery for {query}"
        priority = DownloadPriority.HIGH if force else DownloadPriority.NORMAL
        await self._downloader.add_magnet(
            magnet_link=best.magnet,
            item_name=item.key,
            torrent_title=best.title,
            item_id=item.key,
            priority=priority,
            reason=reason,
            season=season,
            episode=episode,
            language=getattr(item, 'language', ''),
            category_id=category_id,
            import_context={
                "category_id": category_id,
                "item_id": getattr(item, "key", ""),
                "display_title": getattr(item, "display_name", None) or getattr(item, "key", ""),
                "canonical_title": getattr(item, "key", ""),
                "season": season,
                "episode": episode,
                "unit_descriptor": descriptor,
                "release_title": best.title,
                "candidate_snapshot": {
                    "title": best.title,
                    "source": best.source,
                    "size_bytes": best.size_bytes,
                    "bundle_context": bundle_context or {},
                },
            },
            selective_descriptors=[descriptor] if bundle_context and descriptor else None,
        )
        return True
