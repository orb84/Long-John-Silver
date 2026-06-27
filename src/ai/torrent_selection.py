"""
Torrent selection service for LJS.

Selects the best torrent result using deterministic download eligibility
plus LLM-led interpretation. Torrent naming is messy: language acronyms,
editions, scene tags, release groups, and bundle/pack semantics are intentionally left
for the model to reason about after compact normalization. Deterministic code
only removes candidates that cannot or must not be queued.
"""

from __future__ import annotations

from loguru import logger

from src.llm_providers.context_limits import FALLBACK_CONTEXT_LIMIT
from typing import Optional, TYPE_CHECKING
from src.utils.json_parser import LLMResponseParser

from src.core.models import SearchResult, NormalizedTorrentCandidate, QualityProfile
from src.ai.token_budget import TokenBudgetManager
from src.utils.detailed_logger import TorrentLogger
from src.core.release_groups import ReleaseGroupTracker
from src.utils.quality import extract_quality_tags
from src.core.categories.types import ParsedMedia
from src.ai.torrent_candidate_policy import (
    REJECTED_RELEASE_TYPES,
    CandidateEligibility,
    TorrentCandidateEligibilityPolicy,
    TorrentCandidateRanking,
)
from src.ai.torrent_selection_prompt import TorrentSelectionPromptBuilder

if TYPE_CHECKING:
    from src.utils.circuit_breaker import CircuitBreaker
    from src.core.categories.registry import CategoryRegistry


MAX_LLM_CANDIDATES = 10


class TorrentSelectionService:
    """Selects the best torrent result with LLM-led torrent interpretation.

    Deterministic code performs only hard download eligibility checks and
    first-pass ordering. The LLM remains responsible for semantic judgments
    such as odd language tags, title variants, edition names, bundles, packs, acronyms,
    and scene naming conventions.
    """

    def __init__(
        self,
        llm_client: Optional[object] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
        token_budget: Optional[TokenBudgetManager] = None,
        release_group_tracker: Optional[ReleaseGroupTracker] = None,
        category_registry: Optional["CategoryRegistry"] = None,
        torrent_logger: Optional[TorrentLogger] = None,
    ):
        """Initialize the torrent selection service.

        Args:
            llm_client: TaskLLMClient for LLM-based selection.
            circuit_breaker: Optional circuit breaker for LLM calls.
            token_budget: Token budget manager for prompt size control.
            release_group_tracker: Optional tracker for release group reputation.
            category_registry: Optional registry for category-specific parsing.
        """
        self._llm_client = llm_client
        self._breaker = circuit_breaker
        self._token_budget = token_budget or TokenBudgetManager()
        self._release_group_tracker = release_group_tracker
        self._category_registry = category_registry or self._default_category_registry()
        self._torrent_logger = torrent_logger
        self._eligibility = TorrentCandidateEligibilityPolicy()

    def deterministic_pre_filter(
        self,
        results: list[SearchResult],
        require_magnet: bool = True,
        preferred_language: str | None = None,
        *,
        language_relevant: bool = True,
    ) -> list[SearchResult]:
        """Apply only hard download eligibility before LLM selection.

        Language tags, edition words, release groups, and ambiguous acronyms are
        not reliable enough for hard deterministic rejection.  They influence
        ordering and prompt context, but the LLM receives the candidate and can
        reason about it.
        """
        candidates: list[SearchResult] = []
        preferred = (preferred_language or "").strip().lower()
        for result in results:
            verdict = self._eligibility.evaluate(result, require_magnet=require_magnet)
            if not verdict.eligible:
                logger.debug(
                    f"Pre-filter rejected non-queueable torrent: {result.title[:80]} "
                    f"(reason: {verdict.reason})"
                )
                continue
            candidates.append(result)

        candidates.sort(
            key=lambda result: TorrentCandidateRanking.pre_score(result, preferred, language_relevant=language_relevant),
            reverse=True,
        )
        return candidates[:MAX_LLM_CANDIDATES]

    def normalize_candidates(
        self,
        results: list[SearchResult],
        require_magnet: bool = True,
        preferred_language: str | None = None,
        category_id: str = '',
        unit_label: str | None = None,
    ) -> list[NormalizedTorrentCandidate]:
        """Convert raw SearchResult objects into compact normalized candidates.

        Each NormalizedTorrentCandidate includes parsed media metadata
        and an llm_summary field suitable for smaller LLM prompts.

        Args:
            results: List of SearchResult objects to normalize.
            require_magnet: Whether to skip results without magnet links.
            preferred_language: Optional language preference for filtering.
            category_id: Media category for parsing ('tv', 'movie', etc.).

        Returns:
            List of NormalizedTorrentCandidate objects, sorted by quality + seeders.
        """
        category = self._category_registry.get(category_id) if category_id else None
        language_relevant = True
        if category and hasattr(category, "language_is_search_relevant"):
            try:
                language_relevant = bool(category.language_is_search_relevant())
            except Exception:
                language_relevant = True
        candidates = self.deterministic_pre_filter(
            results, require_magnet=require_magnet,
            preferred_language=preferred_language,
            language_relevant=language_relevant,
        )
        normalized = []

        for r in candidates:
            parsed = self._parse_media_name(r.title, category_id)
            tags = extract_quality_tags(r.title)

            red_flag_reasons = []
            for f in tags.get("red_flags", []):
                if isinstance(f, dict):
                    red_flag_reasons.append(f.get("reason", ""))

            bundle_context = {}
            if category and hasattr(category, "torrent_bundle_candidate_context"):
                try:
                    bundle_context = category.torrent_bundle_candidate_context(r, unit_label=unit_label) or {}
                except Exception as exc:
                    logger.debug(f"Category bundle annotation failed for {r.title}: {exc}")
                    bundle_context = {}
            is_bundle = bool(bundle_context.get("is_bundle"))
            estimated_unit_size_mb = None
            if is_bundle and r.size_bytes and category and hasattr(category, "estimate_bundle_unit_size_mb"):
                try:
                    estimated_unit_size_mb = float(category.estimate_bundle_unit_size_mb(
                        total_size_bytes=r.size_bytes,
                        title=r.title,
                        bundle_context=bundle_context,
                        target_descriptor=None,
                    ))
                except Exception as exc:
                    logger.debug(f"Category bundle unit-size estimate failed for {r.title}: {exc}")

            n = NormalizedTorrentCandidate(
                title=r.title,
                source=r.source,
                magnet=r.magnet,
                magnet_available=r.magnet is not None,
                detail_url=r.url,
                size=r.size,
                size_bytes=r.size_bytes,
                seeders=r.seeders,
                parsed_title=parsed.title if parsed else None,
                media_type=tags.get("media_type"),
                season=parsed.season if parsed else None,
                episode=parsed.episode if parsed else None,
                is_bundle=is_bundle,
                bundle_type=str(bundle_context.get("bundle_type") or "") or None,
                bundle_scope=str(bundle_context.get("scope") or "") or None,
                bundle_context=bundle_context,
                estimated_unit_size_mb=estimated_unit_size_mb,
                resolution=tags.get("resolution"),
                codec=tags.get("codec"),
                release_type=tags.get("release_type"),
                release_group=tags.get("release_group"),
                language=", ".join(tags.get("languages", [])) or (
                    "MULTI" if tags.get("is_multi_language") else None
                ),
                red_flags=red_flag_reasons,
                quality_score=r.quality_score,
                extraction_confidence=1.0 if r.magnet else 0.5,
            )
            n.llm_summary = self._build_llm_summary(n)
            normalized.append(n)

        return normalized

    @staticmethod
    def _default_category_registry() -> "CategoryRegistry":
        """Create the built-in category registry for standalone use."""
        from src.core.categories.registry import CategoryRegistry
        return CategoryRegistry.with_defaults()

    def _parse_media_name(self, name: str, category_id: str = '') -> ParsedMedia:
        """Parse a media name using category-specific or fallback parser.

        Args:
            name: The torrent/file name to parse.
            category_id: The media category ID ('tv', 'movie', etc.).
                If empty, tries both TV and movie parsers.

        Returns:
            ParsedMedia with extracted metadata.
        """
        return self._category_registry.parse(name, category_id)

    @staticmethod
    def _build_llm_summary(candidate: NormalizedTorrentCandidate) -> str:
        """Build a compact one-line summary for LLM consumption.

        The summary is category-neutral: it includes only normalized fields the
        owning category/parser exposed. Category-specific meaning still comes
        from the category prompt-file skill.
        """
        parts = []
        if candidate.resolution and candidate.codec:
            parts.append(f"{candidate.resolution} {candidate.codec}")
        elif candidate.resolution:
            parts.append(candidate.resolution)

        if candidate.release_type:
            parts.append(candidate.release_type)

        if candidate.season is not None and candidate.episode is not None:
            parts.append(f"S{candidate.season:02d}E{candidate.episode:02d}")
        if candidate.is_bundle:
            btype = candidate.bundle_type or "bundle"
            scope = f"/{candidate.bundle_scope}" if candidate.bundle_scope else ""
            unit_size = f", est {candidate.estimated_unit_size_mb:.0f}MB per useful unit" if candidate.estimated_unit_size_mb else ""
            parts.append(f"bundle:{btype}{scope}{unit_size}")

        # Language: include detected language for the LLM to reason about
        if candidate.language:
            parts.append(f"lang:{candidate.language}")

        if candidate.seeders is not None:
            parts.append(f"{candidate.seeders} seeders")
        parts.append(candidate.size)
        parts.append(f"magnet {'yes' if candidate.magnet_available else 'no'}")
        parts.append(f"source {candidate.source}")

        if candidate.red_flags:
            parts.append(f"[flags: {', '.join(candidate.red_flags)}]")

        # Include raw title so the LLM can detect content type, file
        # extensions (.rar, .exe, .pdf), and ambiguous naming.
        return f"\"{candidate.title}\" — " + ", ".join(parts)

    def build_quality_reference(
        self, candidates: list[SearchResult], context_limit: int = FALLBACK_CONTEXT_LIMIT,
        preferred_resolution: Optional[str] = None,
        category_id: str | None = None,
    ) -> str:
        """Return quality reference guidance based on category-owned semantics."""
        category = self._category_registry.get(str(category_id or "")) if category_id else None
        if category and not category.uses_global_quality_profile():
            return (
                "Use the owning category's torrent-selection guidance as the quality authority. "
                "Do not import video release tiers, resolution rankings, codec preferences, or spoken-language defaults unless this category guidance or the user explicitly asks for them. "
                "Prefer candidates that satisfy the requested identity, format/edition, safe payload files, plausible size, and healthy seeders."
            )

        from src.utils.torrent_knowledge import get_quality_guide
        full_guide = get_quality_guide()
        if context_limit >= 16384:
            return full_guide

        res_str = "Prefer higher resolution (1080p > 720p) and better codecs (HEVC/h265 > h264)."
        if preferred_resolution:
            res_str = (
                f"Prefer resolution matching '{preferred_resolution}' (e.g. {preferred_resolution} > lower resolutions). "
                f"DO NOT select resolutions higher than '{preferred_resolution}' (e.g., do NOT select 4k or 2160p when preferred is {preferred_resolution}) — "
                f"these are unacceptable due to size and bitrate constraints."
            )

        return (
            "Quality tiers (best to worst): REMUX > WEB-DL > Blu-ray > HDTV > WEBRip > DVDRip.\n"
            "Always reject: CAM, TS, HDCAM — theater recordings, unwatchable quality.\n"
            f"{res_str}\n"
            "Bundles/collections/packs can be acceptable when the category can identify the requested payload; evaluate useful per-unit/file size, not just total torrent size.\n"
        )


    @staticmethod
    def _language_is_eligible(candidate: NormalizedTorrentCandidate, preferred_language: str | None) -> bool:
        """Compatibility shim for older tests; language remains LLM-interpreted."""
        if not preferred_language or not candidate.language:
            return True
        preferred = preferred_language.lower()
        langs = [part.strip().lower() for part in candidate.language.split(",")]
        return "multi" in candidate.language.lower() or preferred in langs

    @staticmethod
    def _resolution_is_eligible(candidate: NormalizedTorrentCandidate, preferred_resolution: str | None) -> bool:
        """Compatibility shim for older tests; prompt still carries resolution guidance."""
        if not preferred_resolution or not candidate.resolution:
            return True
        from src.utils.quality import QualityAnalyzer
        return QualityAnalyzer.rank_resolution(candidate.resolution) <= QualityAnalyzer.rank_resolution(preferred_resolution)


    async def select_best(self, item_name: str, episodes: str, results: list[SearchResult], preferred_language: str, **kwargs: object) -> Optional[dict]:
        """Backward-compatible wrapper around select_best_for_category used by older tests/callers."""
        return await self.select_best_for_category(
            category_id=kwargs.get("category_id") or kwargs.get("media_category") or "",
            item_id=item_name,
            item_display_name=item_name,
            unit_key=episodes,
            unit_request={"label": episodes},
            results=results,
            preferred_language=preferred_language,
            quality_context=kwargs.get("quality_context", ""),
            user_id=kwargs.get("user_id"),
            require_magnet=kwargs.get("require_magnet", True),
            quality_profile=kwargs.get("quality_profile"),
        )


    async def select_best_for_category(
        self,
        category_id: str,
        item_id: str,
        item_display_name: str,
        unit_key: str | None,
        unit_request: dict[str, object],
        results: list[SearchResult],
        preferred_language: str,
        quality_context: str = "",
        user_id: str | None = None,
        require_magnet: bool = True,
        quality_profile: Optional[QualityProfile] = None,
    ) -> Optional[dict]:
        """Select the best torrent candidate using LLM judgment.

        Deterministic filters enforce only hard queueability constraints first.
        The LLM performs the semantic torrent interpretation: language tags,
        packs, edition names, release groups, and title ambiguity.

        Args:
            item_display_name: The show name being searched.
            unit_key: Episode specification (e.g., 'S02E01').
            results: List of SearchResult objects to choose from.
            preferred_language: The user's preferred language.
            quality_context: Additional quality context string.
            media_category: The media category (tv, movie, etc.) for content
                type awareness.
            category_id: Media category for name parsing ('tv', 'movie', etc.).
            user_id: Optional user ID for behavioral tracking.
            require_magnet: When True, skip results without magnet links.
                Set False for fallback searches where the LLM should
                evaluate all candidates (e.g. alternative query results
                from indexers that return HTTP links instead of magnets).

        Returns:
            The selected result dict, or None if no match.
        """
        if not results:
            return None

        # Use category_id for parsing, fall back to media_category
        parse_category = category_id

        normalized = self.normalize_candidates(
            results, require_magnet=require_magnet,
            preferred_language=preferred_language,
            category_id=parse_category,
            unit_label=unit_key,
        )

        if not normalized:
            logger.info(f"No candidates after normalization for {item_display_name} {unit_key or ''}")
            return None

        preferred_resolution = quality_profile.preferred_resolution if quality_profile else None
        max_mb = quality_profile.max_file_size_mb if quality_profile else None
        hard_eligible: list[NormalizedTorrentCandidate] = []
        for candidate in normalized:
            verdict = self._eligibility.evaluate_normalized(candidate, require_magnet=require_magnet, quality_profile=quality_profile)
            if verdict.eligible:
                hard_eligible.append(candidate)
            else:
                logger.info(
                    f"Selection pre-filter rejected non-queueable candidate '{candidate.title}' "
                    f"(reason: {verdict.reason})"
                )
        cat = self._category_registry.get(parse_category)
        category_filter = getattr(cat, "filter_torrent_candidates_for_unit", None) if cat else None
        if callable(category_filter):
            try:
                hard_eligible = list(category_filter(
                    hard_eligible,
                    item_id=item_id,
                    item_display_name=item_display_name,
                    unit_key=unit_key,
                    unit_request=unit_request or {},
                    preferred_language=preferred_language,
                ))
            except Exception as exc:
                logger.warning(
                    f"Category torrent candidate unit filter failed for {parse_category}/{item_display_name} {unit_key or ''}: {exc}"
                )

        category_language_relevant = True
        category_uses_global_quality = True
        if cat and hasattr(cat, "language_is_search_relevant"):
            try:
                category_language_relevant = bool(cat.language_is_search_relevant())
            except Exception:
                category_language_relevant = True
        if cat and hasattr(cat, "uses_global_quality_profile"):
            try:
                category_uses_global_quality = bool(cat.uses_global_quality_profile())
            except Exception:
                category_uses_global_quality = True
        target_episode_size_mb = TorrentCandidateRanking.target_episode_size_from_context(quality_context) if category_uses_global_quality else None
        normalized = sorted(
            hard_eligible,
            key=lambda n: TorrentCandidateRanking.selection_score(
                n,
                preferred_language,
                preferred_resolution,
                target_episode_size_mb,
                language_relevant=category_language_relevant,
                use_global_quality_profile=category_uses_global_quality,
            ),
            reverse=True,
        )

        if not normalized:
            logger.info(f"No queueable candidates after hard/category filtering for {item_display_name} {unit_key or ''}")
            return None

        if self._release_group_tracker:
            for n in normalized[:5]:
                boost = await self._release_group_tracker.get_reputation_boost(n.title)
                if abs(boost) > 0.01:
                    label = "trusted" if boost > 0 else "low-reputation"
                    quality_context += f"\n{n.title}: release group {label} (score: {boost:+.2f})"

        context_limit = FALLBACK_CONTEXT_LIMIT
        quality_ref = self.build_quality_reference(
            [SearchResult(title=n.title, source=n.source,
                          magnet=n.magnet, size=n.size,
                          seeders=n.seeders, url=n.detail_url,
                          quality_score=n.quality_score)
             for n in normalized],
            context_limit,
            preferred_resolution=preferred_resolution,
            category_id=parse_category,
        )

        # Get category-specific selection guidance
        selection_guidance = ""
        if cat:
            selection_guidance = cat.build_torrent_selection_guidance()

        prompt = TorrentSelectionPromptBuilder.build(
            item_display_name=item_display_name,
            unit_key=unit_key or "",
            preferred_language=preferred_language,
            media_category=category_id,
            quality_context=quality_context,
            quality_ref=quality_ref,
            candidates=normalized,
            preferred_resolution=preferred_resolution,
            max_file_size_mb=max_mb,
            selection_guidance=selection_guidance,
        )

        if not self._llm_client:
            logger.warning("No LLM client available for torrent selection; refusing deterministic semantic choice")
            return None

        try:
            response = await self._breaker.call(
                self._llm_client.completion,
                task="torrent_ranker",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=50,
            )
            content = LLMResponseParser.safe_extract_content(response)
            
            index = -1
            try:
                parsed = LLMResponseParser.extract_json_resilient(content)
                index = parsed.get("index", -1)
            except Exception as err:
                logger.warning(f"Torrent selection LLM returned invalid JSON, attempting regex fallback: {err}")
                # Fallback: search for "index": X or index: X in the raw text
                import re
                match = re.search(r'(?i)"index"\s*:\s*(\d+)', content)
                if not match:
                    match = re.search(r'(?i)index\s*[:\-\s]\s*(\d+)', content)
                
                if match:
                    index = int(match.group(1))
                    logger.info(f"Regex fallback successfully extracted index: {index}")
                else:
                    logger.warning(f"Could not extract index via regex fallback from raw LLM output: {content!r}")

            if 0 <= index < len(normalized):
                selected = normalized[index]
                verdict = self._eligibility.evaluate_normalized(selected, require_magnet=require_magnet)
                if not verdict.eligible:
                    logger.warning(
                        f"Torrent selection LLM chose a non-queueable candidate at index {index}: "
                        f"{selected.title} (reason: {verdict.reason})"
                    )
                    return None
                logger.info(f"Successfully selected torrent at index {index}: {selected.title}")
                if self._torrent_logger:
                    try:
                        await self._torrent_logger.log_candidates(
                            item_name=item_display_name,
                            episode=unit_key or "",
                            candidates=normalized,
                            preferred_lang=preferred_language,
                            selected_index=index,
                            selected_title=selected.title,
                        )
                    except Exception as le:
                        logger.warning(f"Failed to log torrent selection: {le}")
                return selected.model_dump()
            
            logger.warning(
                f"Torrent selection LLM failed to return a valid index (got: {index}). "
                "Failing closed instead of silently selecting a possibly wrong candidate."
            )
            return None
        except Exception as e:
            logger.error(f"Torrent selection failed: {e}")
            return None

    _build_selection_prompt = staticmethod(TorrentSelectionPromptBuilder.build)
