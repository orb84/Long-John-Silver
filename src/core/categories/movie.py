"""
Movie category for LJS.

Implements MediaCategory for films.
Handles year-based naming, movie-specific search patterns,
and flat folder organization.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from loguru import logger

from src.core.categories.base import CategoryMedia
from src.core.categories.search_patterns import SearchPatterns
from src.core.categories.identity import clean_display_title, clean_release_title, extract_release_year, canonical_item_key, clean_path_fragment, basename_from_pathish
from src.core.categories.title_authority import CategoryTitleAuthority
from src.core.categories.types import ParsedMedia, ScannedItem, ScannedFileObservation
from src.core.categories.media_probe import probe_media_files_serial, resolution_label_from_probe_payload
from src.core.categories.video_sidecars import plan_video_sidecar_imports
from src.core.security.path_policy import SafePathResolver, SecurityPolicyError
from src.core.models import (
    CategoryActionDeclaration,
    CategoryLlmProfile,
    CategoryPromptExample,
    CategoryProperty,
    CategorySetupRequirement,
    CategoryUiSection,
    CategoryWorkflowDeclaration,
    ActionReceipt,
    ChangedEntity,
    Intent,
)

if TYPE_CHECKING:
    from src.core.models import Settings, QualityProfile
    from src.core.database import Database

_MOVIE_YEAR_RE = re.compile(r'(?P<title>.+?)(?:\s*[\[(]\s*|\s+)(?P<year>19\d{2}|20\d{2})(?:\s*[\])]|\b)')
_VIDEO_EXTENSIONS = {'.mkv', '.mp4', '.avi', '.m4v', '.mov', '.mpg', '.mpeg', '.wmv'}
_TV_EPISODE_HINT_RE = re.compile(r'\b(?:S\d{1,2}E\d{1,3}|S\d{1,2}\s*[-–]\s*\d{1,2}|\d{1,2}x\d{1,3})\b', re.IGNORECASE)
_TV_SEASON_DIR_HINT_RE = re.compile(r'^(?:season|stagione)\s*\d{1,2}$|^S\d{1,2}$', re.IGNORECASE)
_IGNORED_MOVIE_SUBDIRS = {'sample', 'samples', 'extra', 'extras', 'subs', 'subtitles', 'proof'}


@dataclass
class MovieSearchPatterns(SearchPatterns):
    """Movie-specific search patterns with year and quality."""

    def build_primary_query(self, media_name: str, language: str,
                            progress: dict | None = None) -> str:
        """Build the primary query representation.

        Keep construction deterministic and side-effect free.  Future
        extensions should add optional inputs or collaborators rather than
        hard-coding category or provider-specific behavior here.
        """
        return self._append_language(media_name, language)

    def build_alternative_queries(self, media_name: str, language: str,
                                   progress: dict | None = None) -> list[str]:
        """Build the alternative queries representation.

        Keep construction deterministic and side-effect free.  Future
        extensions should add optional inputs or collaborators rather than
        hard-coding category or provider-specific behavior here.
        """
        queries = [
            f"{media_name} 1080p",
            f"{media_name} 2160p",
            f"{media_name} BRRip",
        ]
        return [self._append_language(q, language) for q in queries]



class MovieCategory(CategoryMedia):
    """Films and movies."""

    category_id = "movie"
    display_name = "Movies"
    default_folder = "Movies"
    icon = "film"
    capabilities = ["metadata", "downloadable", "file_organization", "subtitles", "ratings", "quality_upgrades"]
    metadata_provider_names = ["tmdb"]
    supported_operations = ["search", "download", "scan", "organize", "refresh_metadata", "search_upgrade"]

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
        """Run movie-owned interactive search with title-authority validation.

        Movie searches must not expose broad keyword hits such as
        ``The Best Exotic Marigold Hotel`` for a request named ``Hotel
        Exotica``.  The category first enriches provider title authority when
        possible, then searches a bounded title/year ladder and validates every
        row against exact provider/user title phrases before the LLM can see or
        queue it.
        """
        item = await self._ensure_agent_title_authority(item, context)
        queries = self._agent_movie_search_queries(item, language=language)
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
                logger.debug(f"Movie agent query failed for {getattr(item, 'key', '')}: {query}: {exc}")
                continue
            valid = [result for result in (results or []) if self.validate_search_result_for_request(result, item, None)]
            self._log_movie_search_filter_audit(item=item, query=query, language=language, raw_results=results or [], valid_results=valid)
            self._merge_movie_results(merged, seen, valid)
            if len(merged) >= 40:
                break
        ranked = self._rank_movie_agent_results(merged, item=item, language=language)
        return ranked, "; ".join(queries[:4]) or str(getattr(item, "key", "") or "movie")

    async def _ensure_agent_title_authority(self, item: Any, context: Any | None) -> Any:
        """Attach TMDB-backed title aliases before movie search when available."""
        if item is None or CategoryTitleAuthority.authoritative_aliases_for_item(item):
            return item
        title = str(getattr(item, "key", "") or "").strip()
        if not title or context is None:
            return item
        metadata: dict[str, Any] = dict(getattr(item, "metadata", {}) or {})
        enricher = getattr(context, "metadata_enricher", None)
        if enricher and hasattr(enricher, "enrich_feature"):
            try:
                record = await enricher.enrich_feature(title)
            except Exception as exc:
                logger.debug(f"Movie title authority TMDB lookup failed for {title}: {exc}")
                record = None
            if record is not None:
                payload = record.model_dump() if hasattr(record, "model_dump") else dict(record)
                payload = {k: v for k, v in payload.items() if v not in (None, "", [], {})}
                metadata.update(payload)
                display_name = payload.get("display_name")
                if display_name and hasattr(item, "display_name"):
                    try:
                        item.display_name = str(display_name)
                    except Exception:
                        pass
                year = self._safe_year(getattr(item, "year", None))
                if year is None:
                    release_year = self._safe_year(payload.get("release_year") or payload.get("year") or self._year_from_release_date(payload.get("first_release_date")))
                    if release_year is not None and hasattr(item, "year"):
                        try:
                            item.year = release_year
                        except Exception:
                            pass
        if metadata:
            try:
                item.metadata = metadata
            except Exception:
                pass
        return item

    def _agent_movie_search_queries(self, item: Any, *, language: str | None = None) -> list[str]:
        """Return bounded movie query variants from title authority."""
        titles = CategoryTitleAuthority.query_titles_for_item(item, preferred_language=language, limit=6)
        if not titles:
            titles = [str(getattr(item, "key", "") or "").strip()]
        year = self._requested_movie_year(item)
        raw: list[str] = []
        for title in titles:
            title = str(title or "").strip()
            if not title:
                continue
            if year:
                raw.append(self._append_search_language(f"{title} {year}", language))
            raw.append(self._append_search_language(title, language))
            # English is rarely advertised in public movie torrent titles.  For
            # explicit English, the language facet is a rank/validation signal,
            # not a reason to skip bare exact-title queries.
            if language and self._append_search_language(title, language) != title:
                if year:
                    raw.append(f"{title} {year}")
                raw.append(title)
        out: list[str] = []
        seen: set[str] = set()
        for query in raw:
            normalized = re.sub(r"\s+", " ", str(query or "")).strip()
            marker = normalized.casefold()
            if normalized and marker not in seen:
                seen.add(marker)
                out.append(normalized)
        return out[:12]

    @staticmethod
    def _merge_movie_results(merged: list[Any], seen: set[str], results: list[Any] | None) -> None:
        """Append unique search rows by magnet/source/title identity."""
        for result in results or []:
            identity = str(getattr(result, "magnet", None) or f"{getattr(result, 'source', '')}|{getattr(result, 'title', '')}").casefold()
            if not identity or identity in seen:
                continue
            seen.add(identity)
            merged.append(result)

    def _rank_movie_agent_results(self, results: list[Any], *, item: Any, language: str | None = None) -> list[Any]:
        """Deterministically rank validated movie candidates before LLM review."""
        requested_year = self._requested_movie_year(item)

        def rank(result: Any) -> tuple[int, int, int, int, str]:
            title = str(getattr(result, "title", "") or "")
            lang_status = self._movie_language_status(title, language)
            lang_rank = 0 if lang_status == "preferred" else (1 if lang_status == "unknown" else 2)
            candidate_year = extract_release_year(title)
            year_rank = 0 if requested_year and candidate_year == requested_year else (1 if not requested_year or not candidate_year else 3)
            try:
                seeders = int(getattr(result, "seeders", None) if getattr(result, "seeders", None) is not None else -1)
            except (TypeError, ValueError):
                seeders = -1
            res_rank = {"2160p": 0, "4k": 0, "1080p": 1, "720p": 2, "480p": 3}.get(str(self._extract_resolution(title) or "").lower(), 4)
            return (lang_rank, year_rank, -seeders, res_rank, title.casefold())

        return sorted(results or [], key=rank)

    def _log_movie_search_filter_audit(self, *, item: Any, query: str, language: str | None, raw_results: list[Any], valid_results: list[Any]) -> None:
        """Log compact movie search filter evidence for future debugging."""
        if raw_results and not valid_results:
            sample = [str(getattr(row, "title", "") or "")[:160] for row in raw_results[:8]]
            logger.info(
                "MOVIE_SEARCH_FILTER_AUDIT item=%r query=%r language=%r raw=%d valid=0 rejected_sample=%r",
                getattr(item, "key", ""), query, language, len(raw_results), sample,
            )
    category_tool_names = [
        "movie.resolve_metadata",
        "movie.refresh_metadata",
        "movie.search_download_candidates",
        "movie.download_movie",
        "movie.search_upgrade",
        "movie.scan_library",
        "movie.delete_item",
    ]
    prompt_file = "movie.md"
    _default_naming_template = '{title} ({year})/{title} ({year}) {quality}'

    def provider_setup_requirements(self, settings: 'Settings') -> list[CategorySetupRequirement]:
        """Return shared media setup requirements inherited from CategoryMedia."""
        return super().provider_setup_requirements(settings)

    def lifecycle_policy(self) -> dict[str, Any]:
        """Declare movie-owned suggestion and lifecycle policy."""
        return {
            "policy_version": 2,
            "identity_fields": ["provider", "external_id", "title", "year", "edition"],
            "lifecycle_fields": ["library_presence", "quality", "language", "edition", "release_age", "metadata_complete"],
            "suggestion_types": ["quality_upgrade", "better_release", "metadata_repair", "related_media"],
            "invalidation_triggers": [
                "library_changed", "metadata_changed", "taste_changed",
                "download_completed", "download_failed", "user_quality_or_language_changed",
                "manual_refresh", "policy_version_changed",
            ],
            "missing_check_interval_days": 14,
            "upgrade_scan_interval_days": 30,
            "metadata_repair_interval_days": 7,
            "default_check_interval_days": 180,
            "llm_policy_description": (
                "For movies, prefer stable title/year/provider identity. Suggestions normally concern metadata repair, "
                "better releases, quality upgrades, language/subtitle preferences, and related recommendations; a finished "
                "movie does not need frequent refresh unless library quality or user preferences change."
            ),
        }

    def lifecycle_decision(self, item: Any, context: dict[str, Any]) -> dict[str, Any]:
        """Choose the next useful movie check without hardcoded scheduler branches."""
        from datetime import datetime, timedelta, timezone

        policy = context.get("policy") or self.lifecycle_policy()
        now = datetime.now(timezone.utc)
        state = getattr(item, "state", {}) or {}
        has_library = bool(state.get("library_present")) or bool(getattr(item, "resolution", None))
        metadata = getattr(item, "metadata", {}) or {}
        metadata_complete = bool(metadata.get("external_id") or metadata.get("tmdb_id") or getattr(item, "tmdb_id", None))
        if not has_library:
            days = int(policy.get("missing_check_interval_days") or 14)
            reason = "Movie is tracked but not present in the library."
        elif not metadata_complete:
            days = int(policy.get("metadata_repair_interval_days") or 7)
            reason = "Movie metadata identity is incomplete; metadata repair remains useful."
        else:
            days = int(policy.get("upgrade_scan_interval_days") or 30)
            reason = "Movie present; periodic quality/language upgrade check."
        check_at = now + timedelta(days=max(days, 1))
        return {
            "next_check_at": check_at.isoformat(),
            "valid_until": check_at.isoformat(),
            "reason": reason,
            "confidence": 0.72,
        }


    def taste_profile_schema(self) -> dict[str, Any]:
        """Return movie-specific taste metadata fields for the agent."""
        schema = super().taste_profile_schema()
        schema["movie_keys"] = [
            "genres", "cast_names", "directors", "writers", "runtime",
            "release_year", "tmdb_id", "imdb_id", "rating", "overview",
        ]
        return schema

    def taste_profile_llm_instructions(self) -> list[str]:
        """Guide the agent to record movie taste evidence correctly."""
        return [
            "For movie mentions, enrich title/year through TMDB before recording taste when possible.",
            "Record genres, cast_names, directors, writers, runtime, release_year, rating, and overview when known.",
            "Treat 'I want to watch' as curious/watchlist unless the user explicitly says they like or dislike it.",
            "Do not infer that one liked thriller means the user likes all thrillers; store the user's stated reasons as facets.",
            "Negative feedback should attach first to the item and stated reasons, not to every genre/person in the metadata.",
        ]

    def taste_dimension_weights(self) -> dict[str, float]:
        """Return movie-specific cautious metadata multipliers."""
        return {
            "genres": 0.20,
            "cast_names": 0.10,
            "directors": 0.42,
            "writers": 0.30,
            "themes": 0.48,
            "moods": 0.46,
            "tags": 0.25,
            "languages": 0.06,
        }



    def matches_external_media_type(self, source: str, media_type: str) -> bool:
        """Map Plex movie records to the Movie category inside the category boundary."""
        return source.lower() == 'plex' and media_type == 'movie'

    def library_file_records_from_scan(self, scanned: Any) -> list[dict[str, Any]]:
        """Expose movie local files with movie-owned optional year selector."""
        records: list[dict[str, Any]] = []
        for scanned_file in list(getattr(scanned, 'files', []) or []):
            size = int(getattr(scanned_file, 'size_bytes', 0) or 0)
            records.append({
                'name': getattr(scanned, 'name', ''),
                'category_id': self.category_id,
                'year': getattr(scanned, 'year', None),
                'path': getattr(scanned_file, 'file_path', ''),
                'size_mb': round(size / (1024 * 1024), 1),
                'quality': getattr(scanned_file, 'quality', ''),
            })
        return records

    def file_record_matches_selector(
        self,
        file_info: dict[str, Any],
        *,
        season: int | None = None,
        episode: int | None = None,
        year: int | None = None,
    ) -> bool:
        """Match movie cleanup records by year when the caller provided one."""
        if year is not None and file_info.get('year') not in {None, year}:
            return False
        return True

    @property
    def search(self) -> MovieSearchPatterns:
        """Search using the MovieCategory provider contract.

        Normalize inputs before calling external providers and return stable
        model objects.  Add new provider-specific behavior behind adapters,
        not in callers.
        """
        return MovieSearchPatterns()


    def llm_profile(self) -> CategoryLlmProfile:
        """Return movie-specific LLM guidance."""
        return CategoryLlmProfile(
            category_id=self.category_id,
            short_description="Standalone films identified primarily by title and release year.",
            user_facing_description=(
                "Movies are standalone video items. I can search by title and year, compare releases, "
                "download the best match, refresh metadata, and organize files into the movie library."
            ),
            router_description="Movies: standalone films usually identified by title and release year.",
            domain_vocabulary=[
                "movie", "film", "release year", "edition", "director's cut", "theatrical cut",
                "remux", "BluRay", "WEB-DL", "HDR", "subtitles",
            ],
            item_types=["movie", "edition", "release"],
            identifiers=["title", "year", "tmdb_id", "imdb_id"],
            common_user_requests=[
                "Download a movie by title.",
                "Find a better version of a movie already in the library.",
                "Refresh movie metadata.",
            ],
            ambiguity_rules=[
                "If multiple movies share the same title, ask for or infer the release year before downloading.",
                "If a title also exists as a TV show, use words like movie, film, season, or episode to disambiguate.",
            ],
            search_rules=[
                "Use movie-scoped metadata providers for exact title/year matching before torrent selection.",
                "Prefer exact title and year matches over fuzzy title-only matches.",
                "Use generic web research tools only when the user asks for quality, opinions, or recommendations.",
            ],
            download_rules=[
                "Reject CAM, TS, telesync, screener, and unrelated software/game/book releases unless the user explicitly accepts them.",
                "Prefer WEB-DL, BluRay, Remux, and releases matching the configured quality and language profile.",
            ],
            organization_rules=[
                "Organize movies under a title/year folder using the category naming template.",
            ],
            tool_usage_notes=[
                "Use movie-scoped workflows when available instead of generic TMDB calls plus manual torrent parsing.",
                "Only queue a movie automatically when title/year confidence is high and the candidate has a magnet link.",
            ],
            examples=[
                CategoryPromptExample(
                    user="Download Dune",
                    expected_intent="download",
                    expected_behavior="Clarify the year if ambiguous; otherwise resolve the movie, search exact title/year candidates, reject CAM/TS, and queue the best safe match.",
                    tool_plan=["movie.resolve_metadata", "movie.search_download_candidates", "movie.download_movie"],
                ),
                CategoryPromptExample(
                    user="Find me a better copy of Blade Runner 2049",
                    expected_intent="download",
                    expected_behavior="Inspect current library quality, search upgrade candidates, and present or queue the best higher-quality release depending on confidence.",
                    tool_plan=["movie.search_upgrade"],
                ),
            ],
        )

    def ui_sections(self) -> list[CategoryUiSection]:
        """Return UI sections for movie dashboards and item details."""
        return [
            CategoryUiSection(id="overview", title="Overview", component="metadata_summary"),
            CategoryUiSection(id="files", title="Files", component="file_list"),
            CategoryUiSection(id="downloads", title="Downloads", component="download_list"),
            CategoryUiSection(id="upgrades", title="Upgrades", component="upgrade_candidate_list"),
        ]

    def declare_actions(self) -> list[CategoryActionDeclaration]:
        """Declare movie UI/LLM actions."""
        actions = super().declare_actions()
        actions.extend([
            CategoryActionDeclaration(
                name="refresh_metadata",
                label="Refresh Metadata",
                description="Refresh this movie's metadata from the configured movie metadata provider.",
                parameters={
                    "type": "object",
                    "properties": {"title": {"type": "string"}, "year": {"type": "integer"}},
                    "required": ["title"],
                },
                requires_confirmation=False,
                risk_level="read",
                operation="refresh_metadata",
                capabilities_required=["metadata"],
                result_component="metadata_summary",
                tool_name="movie.refresh_metadata",
            ),
            CategoryActionDeclaration(
                name="search_upgrade",
                label="Search Better Version",
                description="Search for a higher-quality release of a movie already in the library.",
                parameters={
                    "type": "object",
                    "properties": {"title": {"type": "string"}, "year": {"type": "integer"}},
                    "required": ["title"],
                },
                requires_confirmation=False,
                risk_level="read",
                operation="search_upgrade",
                capabilities_required=["downloadable", "quality_upgrades"],
                result_component="upgrade_candidate_list",
                tool_name="movie.search_upgrade",
            ),
            CategoryActionDeclaration(
                name="scan_library",
                label="Scan Library",
                description="Scan the configured movie library path and reconcile discovered movie items.",
                parameters={"type": "object", "properties": {}, "required": []},
                risk_level="write",
                operation="scan_library",
                capabilities_required=["file_organization"],
                result_component="file_list",
                tool_name="movie.scan_library",
            ),
            CategoryActionDeclaration(
                name="delete_item",
                label="Delete Movie",
                description="Delete or untrack one movie item through the movie category workflow.",
                parameters={
                    "type": "object",
                    "properties": {"item_id": {"type": "string"}, "delete_files": {"type": "boolean"}},
                    "required": ["item_id"],
                },
                requires_confirmation=True,
                destructive=True,
                risk_level="destructive",
                operation="delete_item",
                capabilities_required=["file_organization"],
                confirmation_prompt="Delete this movie item? This may remove files if delete_files is true.",
                result_component="action_receipt",
                tool_name="movie.delete_item",
            ),
        ])
        return actions

    def declare_workflows(self) -> list[CategoryWorkflowDeclaration]:
        """Declare movie workflows exposed as category-scoped LLM tools."""
        return [
            CategoryWorkflowDeclaration(
                name="resolve_metadata",
                description="Resolve a movie title/year using movie-owned metadata providers.",
                parameters={
                    "type": "object",
                    "properties": {"title": {"type": "string"}, "year": {"type": "integer"}},
                    "required": ["title"],
                },
                intent=Intent.SEARCH,
                risk_level="read",
                tool_name="movie.resolve_metadata",
            ),
            CategoryWorkflowDeclaration(
                name="search_download_candidates",
                description="Search torrent candidates for a resolved movie.",
                parameters={
                    "type": "object",
                    "properties": {"title": {"type": "string"}, "year": {"type": "integer"}},
                    "required": ["title"],
                },
                intent=Intent.DOWNLOAD,
                risk_level="read",
                tool_name="movie.search_download_candidates",
            ),
            CategoryWorkflowDeclaration(
                name="scheduled_check",
                description="Run the movie category scheduled automation loop for one item.",
                parameters={"type": "object", "properties": {"item_id": {"type": "string"}}, "required": ["item_id"]},
                intent=Intent.DOWNLOAD,
                risk_level="write",
                tool_name="movie.scheduled_check",
            ),
            CategoryWorkflowDeclaration(
                name="download_movie",
                description="Queue the best confirmed movie torrent candidate.",
                parameters={
                    "type": "object",
                    "properties": {"title": {"type": "string"}, "year": {"type": "integer"}, "magnet": {"type": "string"}},
                    "required": ["title", "magnet"],
                },
                intent=Intent.DOWNLOAD,
                risk_level="write",
                requires_confirmation=False,
                tool_name="movie.download_movie",
            ),
        ]


    async def execute_workflow(self, workflow_name: str, arguments: dict[str, object], context: object) -> ActionReceipt:
        """Execute movie-owned workflows through generic collaborators."""
        title = str(arguments.get("item_id") or arguments.get("title") or arguments.get("name") or "").strip()
        year = arguments.get("year")
        if workflow_name in {"resolve_metadata", "refresh_metadata"}:
            if not title:
                return self._workflow_failed(workflow_name, "A movie title is required.")
            metadata = {"title": title, "year": year, "provider": "category", "category_id": self.category_id}
            from src.core.categories.metadata.cache_policy import get_fresh_category_metadata
            cached = await get_fresh_category_metadata(getattr(context, "db", None), self.category_id, title)
            if cached:
                metadata.update(cached)
            else:
                enricher = getattr(context, "metadata_enricher", None)
                if enricher and self.metadata_provider_enabled(getattr(context, "settings", None), "tmdb", True):
                    try:
                        enriched = await enricher.enrich_feature(title)
                        normalized = self.normalize_taste_metadata_payload(
                            self.create_item(title, year=year), enriched, "tmdb_movie",
                        )
                        if normalized:
                            metadata.update(normalized)
                    except Exception as exc:
                        logger.debug(f"Movie metadata enrichment failed for {title}: {exc}")
            metadata = await self.cache_metadata_artwork(
                self.create_item(title, year=year), metadata, context, provider="movie_metadata",
            )
            await context.db.media.upsert_category_metadata(
                self.category_id, title, metadata.get("provider", "category"), metadata,
                str(metadata.get("external_id") or metadata.get("tmdb_id") or metadata.get("id", "")),
            )
            return ActionReceipt(
                category_id=self.category_id,
                action_name=workflow_name,
                status="success",
                user_message=f"Resolved movie metadata for {title}.",
                changed_entities=[ChangedEntity(entity_type="category_item", entity_id=title, display_name=title, change="metadata_refreshed")],
                data={"metadata": metadata},
            )

        if workflow_name in {"search_download_candidates", "search_upgrade"}:
            if not title:
                return self._workflow_failed(workflow_name, "A movie title is required.")
            item = self.create_item(title, year=year, language=getattr(context.settings, "language", "English"))
            results = await context.pipeline.run_search(item, episode_label=None, mode="llm")
            return ActionReceipt(
                category_id=self.category_id,
                action_name=workflow_name,
                status="success",
                user_message=f"Found {len(results or [])} movie candidates for {title}.",
                data={"candidates": [r.model_dump() for r in (results or [])]},
            )

        if workflow_name in {"download_movie", "download_item", "scheduled_check"}:
            if not title:
                return self._workflow_failed(workflow_name, "A movie title is required.")
            magnet = str(arguments.get("magnet") or "")
            if magnet and getattr(context, "downloader", None):
                item = await context.downloader.add_magnet(
                    magnet_link=magnet,
                    item_name=title,
                    item_id=title,
                    category_id=self.category_id,
                    reason=f"Manual movie workflow {workflow_name}" if workflow_name != "scheduled_check" else "Scheduled movie workflow",
                )
                return ActionReceipt(
                    category_id=self.category_id,
                    action_name=workflow_name,
                    status="success",
                    user_message=f"Queued movie download for {title}.",
                    changed_entities=[ChangedEntity(entity_type="download", entity_id=item.id, display_name=title, change="queued")],
                    data={"download_id": item.id},
                )
            tracked = next(
                (tracked_item for tracked_item in getattr(context.settings, "tracked_items", [])
                 if getattr(tracked_item, "item_type", None) == self.category_id and tracked_item.key == title),
                None,
            )
            item_model = tracked or self.create_item(title, year=year, language=getattr(context.settings, "language", "English"))
            force_download = workflow_name != "scheduled_check"
            ok = await context.pipeline.run_discovery(item_model, force=force_download)
            return ActionReceipt(
                category_id=self.category_id,
                action_name=workflow_name,
                status="success" if ok else "partial",
                user_message=(f"Queued discovery for {title}." if ok else f"No movie candidate found for {title}."),
                data={"queued": ok, "auto_download_respected": not force_download},
            )

        if workflow_name == "delete_item":
            payload = {k: v for k, v in arguments.items() if k not in {"confirmed", "confirmation_token"}}
            affected_paths = self._candidate_delete_paths(title, context.settings) if arguments.get("delete_files") else []
            if not arguments.get("confirmed"):
                request = self._confirmation_service.create_request(
                    workflow_name,
                    payload,
                    category_id=self.category_id,
                    affected_paths=affected_paths,
                    risk_level="destructive",
                    user_message=f"Confirm deletion of {title}. Files will be permanently deleted from disk if delete_files is enabled.",
                )
                return self._confirmation_service.receipt_for_request(request)
            token = str(arguments.get("confirmation_token") or "")
            if not self._confirmation_service.verify(token, workflow_name, payload):
                return ActionReceipt(
                    category_id=self.category_id,
                    action_name=workflow_name,
                    status="needs_confirmation",
                    user_message="Deletion confirmation is missing, expired, or does not match this exact action.",
                    data={"item_id": title, "affected_paths": affected_paths},
                )
            files_deleted = False
            if arguments.get("delete_files"):
                files_deleted = self.delete(title, context.settings, year=year if isinstance(year, int) else None)
            await context.db.media.delete_category_item(self.category_id, title)
            return ActionReceipt(
                category_id=self.category_id,
                action_name=workflow_name,
                status="success",
                user_message=f"Deleted movie item {title}.",
                changed_entities=[ChangedEntity(entity_type="category_item", entity_id=title, display_name=title, change="deleted")],
                data={"files_deleted": files_deleted, "affected_paths": affected_paths},
            )

        return self._workflow_failed(workflow_name, f"Unsupported movie workflow: {workflow_name}")

    def _workflow_failed(self, workflow_name: str, message: str) -> ActionReceipt:
        """Create a failed receipt for movie workflow validation errors."""
        return ActionReceipt(
            category_id=self.category_id,
            action_name=workflow_name,
            status="failed",
            user_message=message,
            technical_message=message,
        )


    def create_item(self, key: str, **kwargs: object) -> object:
        """Create a tracked movie item without leaking model choices to API code."""
        from src.core.models import MovieItem

        return MovieItem(
            key=key,
            year=kwargs.get("year"),
            language=str(kwargs.get("language") or "English"),
            enabled=bool(kwargs.get("enabled", True)),
            check_interval_days=int(kwargs.get("check_interval_days") or 7),
            auto_download=kwargs.get("auto_download"),
        )


    def build_soulseek_search_queries(
        self,
        query_summary: str,
        item: Any,
        *,
        unit_label: str | None = None,
        language: str | None = None,
        search_scope: str | None = None,
        context: Any | None = None,
    ) -> list[str]:
        """Build movie-owned Soulseek queries.

        Movies need title/year identity, not generic Soulseek keyword spam.
        The provider layer passes raw rows back here for movie filtering.
        """
        title = str(getattr(item, "display_name", None) or getattr(item, "key", "") or query_summary or "").strip()
        metadata = getattr(item, "metadata", {}) or {}
        year = getattr(item, "year", None) or metadata.get("year") or metadata.get("release_year")
        candidates = [str(query_summary or "").strip(), title]
        if year:
            candidates.insert(0, f"{title} {year}")
        out: list[str] = []
        seen: set[str] = set()
        for value in candidates:
            cleaned = re.sub(r"\b(?:download|torrent|movie|film|please|grab|get)\b", " ", str(value), flags=re.IGNORECASE)
            cleaned = re.sub(r"[\[\]{}()]+", " ", cleaned)
            cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_.,")
            key = cleaned.casefold()
            if cleaned and key not in seen:
                seen.add(key)
                out.append(cleaned)
        return out[:4]

    def build_search_query(self, item: Any, unit_label: str | None, language: str | None) -> str:
        """Return a movie torrent query preserving title/year identity."""
        title = str(getattr(item, "key", "") or "").strip()
        year = self._requested_movie_year(item)
        base = f"{title} {year}".strip() if year else title
        if unit_label:
            base = f"{base} {unit_label}".strip()
        return self._append_search_language(base, language)

    def build_alternative_search_queries(self, item: Any, unit_label: str | None, language: str | None) -> list[str]:
        """Return movie-owned fallback queries from title authority."""
        queries = self._agent_movie_search_queries(item, language=language)
        primary = self.build_search_query(item, unit_label, language).casefold()
        return [query for query in queries if query.casefold() != primary]

    def validate_search_result_for_request(self, result: Any, item: Any, unit_label: str | None) -> bool:
        """Validate that a torrent row names the requested movie.

        This gate is intentionally stricter than token-overlap scoring.  A movie
        result must contain a known title phrase (provider title/alias first,
        user title as fallback) and, when both sides advertise a year, the year
        must not contradict the request.
        """
        title = str(getattr(result, "title", "") or "")
        if not title:
            return False
        if _TV_EPISODE_HINT_RE.search(title):
            return False
        if self._looks_like_non_movie_payload(title):
            return False
        aliases = CategoryTitleAuthority.aliases_for_item(item, preferred_language=getattr(item, "language", None), include_user_key=True)
        if not aliases:
            aliases = [str(getattr(item, "key", "") or "").strip()]
        if not CategoryTitleAuthority.matches_any_alias(title, aliases):
            return False
        requested_year = self._requested_movie_year(item)
        candidate_year = extract_release_year(title)
        if requested_year and candidate_year and int(candidate_year) != int(requested_year):
            return False
        return True

    def filter_agent_candidate_payloads_for_request(
        self,
        candidates: list[dict[str, Any]],
        *,
        season: int | None = None,
        episode: int | None = None,
        search_scope: str | None = None,
        language: str | None = None,
    ) -> list[dict[str, Any]]:
        """Final fail-safe filter before movie candidates reach the LLM/user.

        Candidate payloads carry the requested item descriptor.  If generic
        search ever lets unrelated rows through, drop them here rather than
        letting the LLM decide that keyword-overlap rows are safe to queue.
        """
        filtered: list[dict[str, Any]] = []
        for candidate in candidates or []:
            descriptor = candidate.get("unit_descriptor") if isinstance(candidate.get("unit_descriptor"), dict) else {}
            coords = descriptor.get("coordinates") if isinstance(descriptor.get("coordinates"), dict) else {}
            requested_title = str(coords.get("title") or descriptor.get("label") or "").strip()
            if not requested_title:
                filtered.append(candidate)
                continue
            title = str(candidate.get("title") or "")
            if not title or self._looks_like_non_movie_payload(title):
                continue
            if not CategoryTitleAuthority.matches_any_alias(title, [requested_title]):
                continue
            requested_year = self._safe_year(coords.get("year"))
            candidate_year = extract_release_year(title)
            if requested_year and candidate_year and int(requested_year) != int(candidate_year):
                continue
            filtered.append(candidate)
        return filtered

    def soulseek_search_limit(
        self,
        *,
        item: Any,
        unit_label: str | None = None,
        search_scope: str | None = None,
        context: Any | None = None,
    ) -> int:
        """Movie Soulseek searches need enough rows for video releases without overwhelming slskd on macOS.

        Recent macOS slskd builds can report thousands of raw files but delay or
        hide materialized response rows when LJS asks for too large a result
        pool.  Keep the raw pool category-owned and moderate; the provider
        still has count-only recovery, and movie ranking remains here.
        """
        return 100

    async def rank_soulseek_search_results(
        self,
        candidates: list[dict[str, Any]],
        *,
        item: Any,
        language: str | None = None,
        unit_label: str | None = None,
        search_scope: str | None = None,
        context: Any | None = None,
    ) -> list[dict[str, Any]]:
        """Filter/rank Soulseek rows as movie candidates.

        This intentionally rejects audiobook/ebook/music-looking Project Hail
        Mary rows even if Soulseek returned thousands of them.  A movie request
        needs video payload evidence, title/year match, language/quality facts,
        and then LLM/user choice over the survivors.
        """
        requested_title = str(getattr(item, "display_name", None) or getattr(item, "key", "") or "").strip()
        metadata = getattr(item, "metadata", {}) or {}
        requested_year = self._safe_year(getattr(item, "year", None) or metadata.get("year") or metadata.get("release_year"))
        ranked: list[tuple[float, dict[str, Any]]] = []
        for cand in candidates or []:
            extensions = self._soulseek_candidate_extensions(cand)
            if not extensions.intersection({"mkv", "mp4", "m4v", "avi", "mov", "webm"}):
                continue
            text = self._soulseek_candidate_text(cand)
            if not self._movie_title_overlap(requested_title, text):
                continue
            cand_year = extract_release_year(text)
            year_score = 0.0
            if requested_year and cand_year:
                if int(requested_year) != int(cand_year):
                    continue
                year_score = 1.0
            resolution = self._extract_resolution(text)
            language_status = self._movie_language_status(text, language)
            score = self._movie_title_score(requested_title, text) + year_score
            if resolution == "1080p":
                score += 0.35
            elif resolution in {"2160p", "4k"}:
                score += 0.25
            elif resolution == "720p":
                score += 0.1
            if language_status == "preferred":
                score += 0.3
            elif language_status == "unknown":
                score += 0.05
            payload = dict(cand)
            payload.update({
                "category_id": self.category_id,
                "category_match_status": "accepted",
                "resolution": resolution,
                "language_status": language_status,
                "category_evidence": {
                    "movie_title_match": round(self._movie_title_score(requested_title, text), 3),
                    "requested_year": requested_year,
                    "candidate_year": cand_year,
                    "accepted_video_extensions": sorted(extensions),
                },
                "category_quality_note": self.quality_reference_for_search(item, unit_label, context=context),
            })
            ranked.append((score, payload))
        ranked.sort(key=lambda row: (-row[0], self._soulseek_queue_rank(row[1]), str(row[1].get("filename") or "").casefold()))
        return [row for _, row in ranked]

    def soulseek_candidate_context(
        self,
        candidate: dict[str, Any],
        *,
        item: Any,
        language: str | None = None,
        unit_label: str | None = None,
        search_scope: str | None = None,
    ) -> dict[str, Any]:
        """Return movie-specific context attached to accepted Soulseek candidates."""
        return {
            "category_id": self.category_id,
            "category_quality_note": self.quality_reference_for_search(item, unit_label, context=None),
        }

    @staticmethod
    def _safe_year(value: Any) -> int | None:
        try:
            year = int(value)
            return year if 1900 <= year <= 2100 else None
        except (TypeError, ValueError):
            return None

    @classmethod
    def _year_from_release_date(cls, value: Any) -> int | None:
        """Extract a release year from an ISO-ish metadata date."""
        match = re.search(r"\b(19\d{2}|20\d{2})\b", str(value or ""))
        return cls._safe_year(match.group(1)) if match else None

    @classmethod
    def _requested_movie_year(cls, item: Any) -> int | None:
        """Return the requested/provider movie year from the item envelope."""
        metadata = getattr(item, "metadata", {}) or {}
        if not isinstance(metadata, dict):
            metadata = {}
        for value in (
            getattr(item, "year", None),
            metadata.get("year"),
            metadata.get("release_year"),
            cls._year_from_release_date(metadata.get("first_release_date")),
            cls._year_from_release_date(metadata.get("release_date")),
        ):
            year = cls._safe_year(value)
            if year is not None:
                return year
        return None

    @staticmethod
    def _looks_like_non_movie_payload(title: str) -> bool:
        """Reject obvious non-feature rows before LLM/user presentation."""
        text = str(title or "")
        lowered = text.lower()
        if re.search(r"\b(?:extras?|samples?|soundtrack|ost|discography|book|ebook|comic|game|software)\b", lowered):
            return True
        if re.search(r"\bS\d{1,2}E\d{1,3}\b|\b\d{1,2}x\d{1,3}\b", text, re.IGNORECASE):
            return True
        return False

    @staticmethod
    def _soulseek_candidate_extensions(candidate: dict[str, Any]) -> set[str]:
        values: list[Any] = [candidate.get("extension"), candidate.get("filename"), candidate.get("folder")]
        if isinstance(candidate.get("filenames"), list):
            values.extend(candidate.get("filenames") or [])
        out: set[str] = set()
        for value in values:
            if not value:
                continue
            text = str(value)
            suffix = Path(text).suffix.lstrip(".").lower()
            if suffix:
                out.add(suffix)
            ext = text.strip().lower().lstrip(".")
            if ext and len(ext) <= 6 and "/" not in ext and "\\" not in ext:
                out.add(ext)
        return out

    @staticmethod
    def _soulseek_candidate_text(candidate: dict[str, Any]) -> str:
        parts: list[Any] = [candidate.get("filename"), candidate.get("folder")]
        if isinstance(candidate.get("filenames"), list):
            parts.extend(candidate.get("filenames")[:12])
        return " ".join(str(part) for part in parts if part)

    @staticmethod
    def _movie_tokens(text: str) -> set[str]:
        stop = {"the", "and", "movie", "film", "1080p", "720p", "2160p", "web", "webdl", "webrip", "bluray", "brrip"}
        return {tok for tok in re.findall(r"[a-z0-9]+", str(text).lower()) if len(tok) > 2 and tok not in stop}

    @classmethod
    def _movie_title_score(cls, requested_title: str, candidate_text: str) -> float:
        req = cls._movie_tokens(requested_title)
        cand = cls._movie_tokens(candidate_text)
        if not req or not cand:
            return 0.0
        return len(req & cand) / max(1, len(req))

    @classmethod
    def _movie_title_overlap(cls, requested_title: str, candidate_text: str) -> bool:
        return cls._movie_title_score(requested_title, candidate_text) >= 0.6

    @staticmethod
    def _movie_language_status(text: str, preferred_language: str | None) -> str:
        preferred = str(preferred_language or "").lower()
        lowered = str(text or "").lower()
        if preferred and preferred in {"italian", "ita", "it"} and re.search(r"\b(ita|italian|italiano)\b", lowered):
            return "preferred"
        if preferred and preferred in {"english", "eng", "en"} and re.search(r"\b(eng|english)\b", lowered):
            return "preferred"
        if re.search(r"\b(multi|dual|ita|eng|english|italian)\b", lowered):
            return "other_or_multi"
        return "unknown"

    @staticmethod
    def _soulseek_queue_rank(candidate: dict[str, Any]) -> tuple[int, int]:
        free = candidate.get("has_free_upload_slot")
        free_rank = 0 if free is True else (1 if free is None else 2)
        try:
            queue = int(candidate.get("queue_length") if candidate.get("queue_length") is not None else 999999)
        except (TypeError, ValueError):
            queue = 999999
        return (free_rank, queue)


    def get_properties(self, settings: 'Settings') -> list[CategoryProperty]:
        """Return the requested get properties value.

        This public accessor should normalize missing or optional data at the
        boundary and avoid leaking storage/provider internals to callers.
        """
        props = [
            CategoryProperty(
                name="library_path",
                value_type="string",
                description="Absolute path to organize Movies.",
                default_value=""
            ),
            CategoryProperty(
                name="naming_template",
                value_type="string",
                description="Naming template for renaming movie files.",
                default_value=self._default_naming_template
            ),
        ]
        
        # Populate values from settings
        cat_configs = settings.category_settings.get(self.category_id, {})
        for p in props:
            p.value = cat_configs.get(p.name, p.default_value)
        return props

    def parse_name(self, name: str) -> ParsedMedia:
        """Parse a movie torrent/file name into structured info.

        Extracts year, resolution, codec, language, and release group while
        cleaning common release-folder noise such as dots, CamelCase, DLMUX,
        quality tags, and release groups.
        """
        result = ParsedMedia(original_title=name)
        cleaned = name.replace('.', ' ').replace('_', ' ').strip()

        # Try year pattern, including both "Title (2010)" and
        # "Title.2010.1080p" release names.
        m = _MOVIE_YEAR_RE.search(cleaned)
        if m:
            result.title = m.group('title').strip()
            result.year = int(m.group('year'))
        else:
            result.year = extract_release_year(name)

        # Extract shared metadata
        result.resolution = self._extract_resolution(name)
        result.codec = self._extract_codec(name)
        result.language = self._extract_language(name)

        # Clean up title for UI and provider lookup.
        if not result.title or result.title == name:
            result.title = cleaned
        result.title = clean_release_title(result.title, fallback=cleaned, media_hint="movie")
        result.title = re.sub(r'\s+', ' ', result.title).strip()

        return result


    # ── Canonical library object contract ──────────────────────────

    def library_object_spec(self) -> dict[str, Any]:
        """Declare the movie category's canonical library object shape."""
        return {
            "schema_version": 1,
            "item_identity_fields": ["category_id", "item_id", "display_name", "year", "provider_ids"],
            "unit_types": {
                "file": {
                    "description": "One local movie payload file.",
                    "required_fields": ["unit_key", "file_path"],
                    "optional_fields": [
                        "quality", "resolution", "codec", "language", "size_bytes",
                        "estimated_bitrate_kbps", "bitrate_source", "audio_languages", "audio_tracks",
                        "subtitle_languages", "subtitle_tracks", "media_probe", "video_width", "video_height", "resolution_source", "subtitle_files",
                    ],
                }
            },
            "computed_fields": [
                "file_count", "downloaded_file_count", "best_resolution", "total_size_bytes",
                "average_bitrate_kbps", "quality_gaps", "has_local_files",
            ],
            "source_of_truth_rule": (
                "Movie consumers read this canonical object and never infer movie state from raw files directly."
            ),
        }

    def library_item_from_scan(self, scanned: Any) -> dict[str, Any]:
        """Normalize a scanned movie entry into the canonical item envelope."""
        item = super().library_item_from_scan(scanned)
        item["properties"].update({
            "file_count": int(getattr(scanned, "file_count", 0) or 0),
            "year": getattr(scanned, "year", None),
        })
        item["metadata"].update({
            "resolutions": list(getattr(scanned, "resolutions", []) or []),
            "codecs": list(getattr(scanned, "codecs", []) or []),
            "detected_languages": list(getattr(scanned, "detected_languages", []) or []),
            "subtitle_languages": list(getattr(scanned, "subtitle_languages", []) or []),
            "year": getattr(scanned, "year", None),
        })
        return item

    def library_units_from_scan(self, scanned: Any) -> list[dict[str, Any]]:
        """Convert scanned movie payload files into canonical file units."""
        units: list[dict[str, Any]] = []
        for index, scanned_file in enumerate(list(getattr(scanned, "files", []) or []), start=1):
            file_path = str(getattr(scanned_file, "file_path", "") or "")
            parsed = self.parse_name(Path(file_path).name if file_path else getattr(scanned, "name", ""))
            probe = dict(getattr(scanned_file, "media_probe", {}) or {})
            audio_languages = list(getattr(scanned_file, "audio_languages", []) or probe.get("audio_languages") or [])
            subtitle_languages = list(getattr(scanned_file, "subtitle_languages", []) or probe.get("subtitle_languages") or [])
            stream_language = ", ".join(audio_languages)
            video_codecs = list(probe.get("video_codecs") or [])
            unit_key = f"file:{index:04d}"
            size_bytes = int(getattr(scanned_file, "size_bytes", 0) or 0)
            probe_resolution = self._resolution_from_probe(probe)
            bitrate = probe.get("bit_rate_kbps") or self._estimate_movie_bitrate_kbps(size_bytes)
            units.append({
                "unit_key": unit_key,
                "unit_type": "file",
                "display_name": Path(file_path).name if file_path else unit_key,
                "status": "downloaded",
                "file_path": file_path,
                "quality": getattr(scanned_file, "quality", "") or probe_resolution or parsed.resolution or "unknown",
                "resolution": probe_resolution or parsed.resolution,
                "resolution_source": "ffprobe_video_stream" if probe_resolution else ("filename" if parsed.resolution else ""),
                "video_width": probe.get("width") or probe.get("video_width"),
                "video_height": probe.get("height") or probe.get("video_height"),
                "bitrate_source": "ffprobe_format" if probe.get("bit_rate_kbps") else ("size_duration_estimate" if bitrate else ""),
                "codec": (str(video_codecs[0]) if video_codecs else None) or parsed.codec,
                "language": parsed.language or stream_language or getattr(scanned_file, "detected_language", "") or getattr(scanned, "detected_language", "") or "",
                "primary_audio_language": getattr(scanned_file, "detected_language", "") or (audio_languages[0] if audio_languages else ""),
                "audio_languages": audio_languages,
                "audio_tracks": list(getattr(scanned_file, "audio_tracks", []) or probe.get("audio_tracks") or []),
                "subtitle_languages": subtitle_languages,
                "subtitle_tracks": list(getattr(scanned_file, "subtitle_tracks", []) or probe.get("subtitle_tracks") or []),
                "media_probe": probe,
                "size_bytes": size_bytes,
                "estimated_bitrate_kbps": int(bitrate) if bitrate else None,
                "subtitle_files": self._subtitle_sidecars(file_path),
                "sort_index": index,
            })
        return units

    def library_progress_from_scan(self, scanned: Any, units: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Return generic movie download progress from canonical file units."""
        if not units:
            return None
        return {
            "unit_type": "progress",
            "display_name": "Movie library progress",
            "downloaded_file_count": len([unit for unit in units if unit.get("status") == "downloaded"]),
            "total_size_bytes": sum(int(unit.get("size_bytes") or 0) for unit in units),
        }

    def build_library_object(self, context: Any) -> dict[str, Any]:
        """Build the canonical movie object from local files and provider metadata."""
        item = context.item or {}
        file_units = [unit for unit in (context.units or []) if unit.get("unit_type") == "file"]
        downloaded = [unit for unit in file_units if unit.get("status") == "downloaded"]
        total_size = sum(int(unit.get("size_bytes") or 0) for unit in downloaded)
        resolutions = [str(unit.get("resolution") or "").lower() for unit in downloaded if unit.get("resolution")]
        best_resolution = self._best_resolution(resolutions)
        bitrates = [int(unit.get("estimated_bitrate_kbps") or 0) for unit in downloaded if unit.get("estimated_bitrate_kbps")]
        audio_languages: list[str] = []
        subtitle_languages: list[str] = []
        for unit in downloaded:
            for lang in list(unit.get("audio_languages") or []):
                if lang and lang not in audio_languages:
                    audio_languages.append(lang)
            for lang in list(unit.get("subtitle_languages") or []):
                if lang and lang not in subtitle_languages:
                    subtitle_languages.append(lang)
        return {
            "schema_version": self.library_object_spec()["schema_version"],
            "category_id": self.category_id,
            "item_id": context.item_id,
            "display_name": item.get("display_name") or context.item_id,
            "item_type": item.get("item_type") or self.category_id,
            "status": item.get("status") or "",
            "properties": item.get("properties") or {},
            "metadata": item.get("metadata") or {},
            "state": item.get("state") or {},
            "provider_metadata": [row.get("metadata") or {} for row in context.metadata_rows],
            "units": file_units,
            "files": downloaded,
            "computed": {
                "file_count": len(file_units),
                "downloaded_file_count": len(downloaded),
                "best_resolution": best_resolution,
                "total_size_bytes": total_size,
                "average_bitrate_kbps": int(sum(bitrates) / len(bitrates)) if bitrates else None,
                "audio_languages": audio_languages,
                "subtitle_languages": subtitle_languages,
                "quality_gaps": self._quality_gaps(downloaded, getattr(context.settings_item, "quality", None)),
                "has_local_files": bool(downloaded),
            },
        }

    def scan_average_bitrate_kbps(self, scanned: Any) -> int | None:
        """Estimate average bitrate for scanned movie payload files."""
        file_count = int(getattr(scanned, "file_count", 0) or 0)
        if file_count <= 0:
            return None
        avg_size = int(getattr(scanned, "total_size_bytes", 0) or 0) / file_count
        return self._estimate_movie_bitrate_kbps(avg_size)

    @staticmethod
    def _resolution_from_probe(probe: dict[str, Any]) -> str | None:
        """Return resolution from ffprobe video dimensions, never from size."""
        return resolution_label_from_probe_payload(probe)

    @staticmethod
    def _estimate_movie_bitrate_kbps(size_bytes: Any, runtime_minutes: int = 110) -> int | None:
        """Estimate bitrate from file size only when ffprobe bitrate is unavailable."""
        try:
            size = int(size_bytes or 0)
        except (TypeError, ValueError):
            return None
        if size <= 0:
            return None
        return int((size * 8) / max(runtime_minutes * 60, 1) / 1000)

    def related_sidecar_imports_for_file(
        self,
        *,
        source_path: Path,
        imported_path: Path,
        item: Any,
        settings: "Settings" | None,
        file_info: Any | None = None,
    ) -> list[dict[str, str]]:
        """Plan external subtitle sidecars that should follow an imported video.

        The download handler performs the actual safe filesystem mutation, but
        this video category owns the rule that matching subtitle sidecars are
        named from the video basename plus optional language/forced/SDH tokens.
        """
        return plan_video_sidecar_imports(
            source_path=source_path,
            imported_path=imported_path,
            allowed_extensions={".srt", ".ass", ".ssa", ".vtt", ".smi", ".idx", ".sub"},
        )

    @staticmethod
    def _subtitle_sidecars(file_path: str) -> list[str]:
        """Return nearby external subtitle files that share the media file stem.

        This includes plain ``Movie.srt`` and language/flag variants such as
        ``Movie.en.srt``, ``Movie.eng.forced.ass``, and VobSub ``idx/sub``
        pairs so scans mirror the import-time sidecar rules.
        """
        if not file_path:
            return []
        path = Path(file_path)
        plans = plan_video_sidecar_imports(source_path=path, imported_path=path)
        return [plan["source"] for plan in plans if plan.get("source")]

    @staticmethod
    def _best_resolution(resolutions: list[str]) -> str | None:
        """Return the highest known resolution from canonical file units."""
        order = {"480p": 1, "720p": 2, "1080p": 3, "2160p": 4, "4k": 4}
        known = [value for value in resolutions if value in order]
        if not known:
            return None
        return max(known, key=lambda value: order[value])

    @staticmethod
    def _quality_gaps(units: list[dict[str, Any]], quality_profile: Any | None) -> list[dict[str, Any]]:
        """Return movie files below the user's preferred resolution when known."""
        preferred = str(getattr(quality_profile, "preferred_resolution", "") or "").lower()
        order = {"480p": 1, "720p": 2, "1080p": 3, "2160p": 4, "4k": 4}
        if not preferred or preferred not in order:
            return []
        gaps: list[dict[str, Any]] = []
        for unit in units:
            resolution = str(unit.get("resolution") or "").lower()
            if resolution in order and order[resolution] < order[preferred]:
                gaps.append({
                    "unit_key": unit.get("unit_key"),
                    "current_resolution": resolution,
                    "preferred_resolution": preferred,
                })
        return gaps

    async def update(self, item: 'CategoryItem', context: 'CategoryUpdateContext') -> None:
        """Periodic background update for movies.
        
        MovieItems typically do not need periodic checks once downloaded.
        """
        pass

    async def prepare_search_item(self, item: Any, *, settings: Any, scan_result: Any | None = None) -> Any:
        """Apply movie-owned quality-size defaults before torrent search.

        The search pipeline must stay category-neutral. Movie-specific file-size
        heuristics live here because this category knows that a single payload
        file normally represents the feature and can compare against the local
        movie library.
        """
        item = await super().prepare_search_item(item, settings=settings, scan_result=scan_result)
        if not hasattr(item, "quality"):
            return item
        profile = item.quality
        if getattr(profile, "max_file_size_mb", None):
            return item
        from src.core.smart_quality import SmartQualityInferrer

        inferrer = SmartQualityInferrer()
        avg_size = await inferrer.get_average_library_item_size_mb(category_id=self.category_id, scan_result=scan_result, settings=settings, category=self)
        if avg_size > 0.0:
            smart_limit = int(avg_size * 1.3)
            logger.info(f"Applying movie category size limit: {smart_limit}MB (1.3x average {avg_size:.1f}MB)")
        else:
            pref_res = (getattr(profile, "preferred_resolution", "") or "1080p").lower()
            if pref_res in ("2160p", "4k"):
                smart_limit = 25 * 1024
            elif pref_res == "720p":
                smart_limit = 3 * 1024
            else:
                smart_limit = 7 * 1024
            logger.info(f"Movie category fallback size limit: {smart_limit}MB for preferred resolution '{pref_res}'")

        profile_copy = profile.model_copy(deep=True)
        profile_copy.max_file_size_mb = smart_limit
        item_copy = item.model_copy(deep=True)
        item_copy.quality = profile_copy
        return item_copy


    def build_torrent_selection_guidance(self) -> str:
        """Return movie torrent guidance from the category prompt-file skill."""
        skill = self.prompt_file_torrent_skill()
        return (
            "Movie torrent-selection skill is category-owned. Use the prompt-file sections below as the source of truth; "
            "do not rely on generic video or collection heuristics when they conflict with these rules.\n"
            f"{skill}"
        ).strip()

    async def scan(self, root_path: str, existing_keys: set[str] | None = None) -> list[ScannedItem]:
        """Scan a movie directory. Movies are folders or flat files."""
        items: list[ScannedItem] = []
        root = Path(root_path)
        try:
            summaries = await asyncio.to_thread(self._collect_movie_entries, root)
            for summary in summaries:
                await self._enrich_scanned_file_stream_metadata(summary)
        except OSError as e:
            logger.error(f"[MovieCategory] Failed to access root path '{root_path}': {e}")
            return items

        existing_set = set(existing_keys or set())
        for summary in summaries:
            if summary["file_count"] <= 0:
                continue
            try:
                movie_name = summary["movie_name"]
                detected_languages = list(summary.get("detected_languages") or [])
                detected_language = ", ".join(detected_languages) if detected_languages else await self.detect_language(summary["detected_language_name"], None)
                items.append(ScannedItem(
                    name=movie_name,
                    category_id=self.category_id,
                    resolutions=sorted(summary["resolutions"]),
                    codecs=sorted(summary["codecs"]),
                    file_count=summary["file_count"],
                    total_size_bytes=summary["total_size"],
                    detailed_episodes=summary["detailed"],
                    detected_language=detected_language,
                    detected_languages=detected_languages,
                    subtitle_languages=list(summary.get("subtitle_languages") or []),
                    year=summary["movie_year"],
                ))
            except OSError as e:
                logger.warning(f"[MovieCategory] Failed to finalize scan for '{summary.get('movie_name', 'unknown')}': {e}")

        return items

    def _collect_movie_entries(self, root: Path) -> list[dict]:
        """Collect movie file facts with blocking filesystem calls off-loop.

        The movie root can contain normal one-folder-per-movie layouts, flat
        movie files, and collection containers such as ``Sonic Trilogia``.  The
        scanner must not persist the collection folder itself as an empty movie;
        it should surface the actual child films.  It also skips TV-shaped
        folders accidentally placed under the movie root instead of cataloguing
        them as movies.
        """
        if not root.is_dir():
            return []
        summaries: list[dict] = []
        for path in sorted(root.iterdir()):
            try:
                if path.name.startswith("."):
                    continue
                summaries.extend(self._movie_summaries_for_path(path))
            except OSError as e:
                logger.warning(f"[MovieCategory] Skipping unreadable item '{path}': {e}")
                continue
        return summaries

    def _movie_summaries_for_path(self, path: Path) -> list[dict]:
        """Return one or more movie summaries for a top-level root entry."""
        if path.is_file():
            if path.suffix.lower() not in _VIDEO_EXTENSIONS:
                return []
            return [self._build_movie_summary(path.stem, [path], detected_language_name=path.name)]

        if not path.is_dir():
            return []

        video_files = self._video_files_in_movie_dir(path)
        if self._looks_like_tv_directory(path, video_files):
            logger.info(f"[MovieCategory] Skipping TV-shaped folder in movie root: {path.name}")
            return []

        if self._looks_like_collection_directory(path):
            return self._collect_movie_collection(path)

        if not video_files:
            return []
        return [self._build_movie_summary(path.name, video_files, detected_language_name=path.name)]

    def _collect_movie_collection(self, collection_dir: Path) -> list[dict]:
        """Flatten collection containers into their actual child movies."""
        summaries: list[dict] = []
        try:
            children = sorted(child for child in collection_dir.iterdir() if not child.name.startswith("."))
        except OSError as e:
            logger.warning(f"[MovieCategory] Could not inspect collection '{collection_dir}': {e}")
            return summaries

        for child in children:
            try:
                if child.is_file() and child.suffix.lower() in _VIDEO_EXTENSIONS:
                    summaries.append(self._build_movie_summary(child.stem, [child], detected_language_name=child.name))
                elif child.is_dir() and child.name.lower() not in _IGNORED_MOVIE_SUBDIRS:
                    files = self._video_files_in_movie_dir(child)
                    if files and not self._looks_like_tv_directory(child, files):
                        summaries.append(self._build_movie_summary(child.name, files, detected_language_name=child.name))
            except OSError as e:
                logger.warning(f"[MovieCategory] Skipping unreadable collection child '{child}': {e}")
        if summaries:
            logger.info(f"[MovieCategory] Flattened collection folder '{collection_dir.name}' into {len(summaries)} movie item(s).")
        return summaries

    @staticmethod
    def _video_files_in_movie_dir(path: Path) -> list[Path]:
        """Return likely movie payload files under a movie directory."""
        files: list[Path] = []
        try:
            for child in path.iterdir():
                if child.name.startswith("."):
                    continue
                if child.is_file() and child.suffix.lower() in _VIDEO_EXTENSIONS:
                    files.append(child)
                elif child.is_dir() and child.name.lower() not in _IGNORED_MOVIE_SUBDIRS:
                    try:
                        for nested in child.iterdir():
                            if nested.is_file() and nested.suffix.lower() in _VIDEO_EXTENSIONS:
                                files.append(nested)
                    except OSError:
                        continue
        except OSError:
            raise
        return sorted(files)

    @staticmethod
    def _looks_like_tv_directory(path: Path, video_files: list[Path]) -> bool:
        """Detect TV payloads accidentally placed under the movie root."""
        try:
            has_season_dir = any(child.is_dir() and _TV_SEASON_DIR_HINT_RE.search(child.name) for child in path.iterdir())
        except OSError:
            has_season_dir = False
        episode_hits = sum(1 for file_path in video_files if _TV_EPISODE_HINT_RE.search(file_path.name))
        if has_season_dir and episode_hits:
            return True
        if episode_hits >= 2:
            return True
        if _TV_EPISODE_HINT_RE.search(path.name) and episode_hits:
            return True
        return False

    def _looks_like_collection_directory(self, path: Path) -> bool:
        """Return True when a folder is a container for multiple movies."""
        try:
            children = [child for child in path.iterdir() if not child.name.startswith(".")]
        except OSError:
            return False
        child_dirs = [child for child in children if child.is_dir() and child.name.lower() not in _IGNORED_MOVIE_SUBDIRS]
        video_files = [child for child in children if child.is_file() and child.suffix.lower() in _VIDEO_EXTENSIONS]
        child_identities: set[tuple[str, int | None]] = set()
        for child in child_dirs[:12]:
            files = self._video_files_in_movie_dir(child)
            if not files or self._looks_like_tv_directory(child, files):
                continue
            parsed = self.parse_name(child.name)
            child_identities.add((parsed.title.lower(), parsed.year))
        for file_path in video_files[:12]:
            parsed = self.parse_name(file_path.stem)
            child_identities.add((parsed.title.lower(), parsed.year))
        years = {year for _title, year in child_identities if year}
        return len(child_identities) >= 2 and len(years) >= 2

    def _build_movie_summary(self, raw_name: str, movie_files: list[Path], *, detected_language_name: str) -> dict:
        """Build one movie scan summary from one logical movie payload."""
        parsed_item = self.parse_name(raw_name)
        movie_name = parsed_item.title
        movie_year = parsed_item.year
        resolutions: set[str] = set()
        codecs: set[str] = set()
        detailed: list[ScannedFileObservation] = []
        file_count = 0
        total_size = 0
        first_file = None

        for f in movie_files:
            try:
                if not f.is_file() or f.suffix.lower() not in _VIDEO_EXTENSIONS:
                    continue
                sz = f.stat().st_size
                file_count += 1
                total_size += sz
                if first_file is None:
                    first_file = f

                quality = self._extract_quality(f.name)
                detailed.append(ScannedFileObservation(
                    season=0, episode=0, file_path=str(f),
                    quality=quality, size_bytes=sz,
                ))

                lower = f.name.lower()
                for res in ("2160p", "1080p", "720p", "480p", "4k"):
                    if res in lower:
                        resolutions.add(res)
                for codec in ("x264", "h264", "x265", "h265", "hevc", "xvid", "av1"):
                    if codec in lower:
                        codecs.add(codec)
            except OSError as e:
                logger.warning(f"[MovieCategory] Skipping unreadable movie file or attributes: {e}")
                continue

        return {
            "movie_name": movie_name,
            "movie_year": movie_year,
            "detected_language_name": detected_language_name,
            "resolutions": resolutions,
            "codecs": codecs,
            "detailed": detailed,
            "file_count": file_count,
            "total_size": total_size,
            "first_file": first_file,
            "detected_languages": [],
            "subtitle_languages": [],
        }

    async def _enrich_scanned_file_stream_metadata(self, summary: dict[str, Any]) -> None:
        """Attach serialized media-probe stream facts to scanned movie files.

        Probing is sequential and cached through the shared media probe service
        so a library scan does not launch concurrent ffprobe reads.
        """
        observations = [obs for obs in list(summary.get("detailed") or []) if getattr(obs, "file_path", "")]
        if not observations:
            return
        probe_by_path = await probe_media_files_serial(Path(obs.file_path) for obs in observations)
        detected_languages: list[str] = []
        subtitle_languages: list[str] = []
        for obs in observations:
            probe = probe_by_path.get(str(Path(obs.file_path).resolve(strict=False)))
            if probe is None:
                continue
            payload = probe.to_dict()
            obs.media_probe = payload
            obs.audio_languages = list(payload.get("audio_languages") or [])
            obs.audio_tracks = list(payload.get("audio_tracks") or [])
            obs.subtitle_languages = list(payload.get("subtitle_languages") or [])
            obs.subtitle_tracks = list(payload.get("subtitle_tracks") or [])
            obs.detected_language = str(payload.get("primary_audio_language") or "")
            for lang in obs.audio_languages:
                if lang and lang not in detected_languages:
                    detected_languages.append(lang)
            for lang in obs.subtitle_languages:
                if lang and lang not in subtitle_languages:
                    subtitle_languages.append(lang)
            for codec in list(payload.get("video_codecs") or []):
                if codec:
                    summary["codecs"].add(str(codec).lower())
            probe_resolution = resolution_label_from_probe_payload(payload)
            if probe_resolution:
                summary["resolutions"].add(probe_resolution)
        summary["detected_languages"] = detected_languages
        summary["subtitle_languages"] = subtitle_languages

    @staticmethod
    def _extract_quality(filename: str) -> str:
        """Extract a compact quality string from a filename."""
        parts: list[str] = []
        lower = filename.lower()
        for res in ["2160p", "1080p", "720p", "480p"]:
            if res in lower:
                parts.append(res)
                break
        for codec in ["h265", "x265", "hevc", "h264", "x264", "av1"]:
            if codec in lower:
                parts.append(codec)
                break
        return "/".join(parts) if parts else "unknown"


    def unit_descriptor_from_search_result(self, result: Any, item: Any, unit_label: str | None) -> dict[str, Any]:
        """Return the movie item descriptor for a candidate result.

        Movies are item-scoped, but the descriptor carries title/year hints so
        the generic bundle handler can let this category select the right file
        from a collection torrent after metadata arrives.
        """
        label = getattr(item, "display_name", None) or getattr(item, "key", "") or "movie"
        parsed = self.parse_name(str(getattr(result, 'title', '') or label))
        year = getattr(item, 'year', None) or parsed.year
        title_key = canonical_item_key(str(label or parsed.title or 'movie'))
        stable_key = f"movie:{title_key}:{year or ''}"
        return {
            "granularity": "item",
            "label": label,
            "stable_key": stable_key,
            "sort_key": [0],
            "coordinates": {"title": label, "title_key": title_key, "year": year},
        }

    def torrent_bundle_candidate_context(self, result: Any, item: Any | None = None, unit_label: str | None = None) -> dict[str, Any] | None:
        """Describe movie collection torrents only from payload/file evidence.

        A movie collection is a structural fact: the torrent/file-list contains
        multiple primary video payloads with distinct parsed movie identities.
        Do not infer this from marketing words in the torrent title.
        """
        files = self._candidate_file_entries(result)
        if not self._file_entries_indicate_collection(files):
            return None
        return {
            'is_bundle': True,
            'bundle_type': 'movie_collection',
            'scope': 'item_collection',
            'unit_count': self._distinct_movie_identity_count_from_entries(files),
            'can_select_files_after_metadata': True,
            'selection_note': 'Torrent file-list shows multiple distinct primary movie payloads; preserve collection structure and import each movie file by its own source filename.',
        }


    @staticmethod
    def _candidate_file_entries(result: Any) -> list[Any]:
        """Return cached torrent file entries when a search provider exposes them."""
        if result is None:
            return []
        for attr in ("files", "file_list"):
            value = getattr(result, attr, None)
            if isinstance(value, list):
                return value
        if isinstance(result, dict):
            for key in ("files", "file_list"):
                value = result.get(key)
                if isinstance(value, list):
                    return value
        return []

    def _file_entries_indicate_collection(self, files: list[Any]) -> bool:
        """Return True when file-list evidence shows multiple movie identities."""
        return self._distinct_movie_identity_count_from_entries(files) >= 2

    def _distinct_movie_identity_count_from_entries(self, files: list[Any]) -> int:
        identities: set[tuple[str, int | None]] = set()
        for entry in files or []:
            raw_path = self._file_entry_path(entry)
            if not raw_path:
                continue
            path = Path(raw_path.replace("\\", "/"))
            if path.suffix.lower() not in _VIDEO_EXTENSIONS:
                continue
            lower_parts = {part.lower() for part in path.parts}
            if "sample" in lower_parts or lower_parts.intersection(_IGNORED_MOVIE_SUBDIRS):
                continue
            parsed = self.parse_name(path.stem)
            key = canonical_item_key(parsed.title or path.stem)
            if key:
                identities.add((key, parsed.year))
        return len(identities)

    @staticmethod
    def _file_entry_path(entry: Any) -> str:
        if isinstance(entry, dict):
            return str(entry.get("path") or entry.get("file_path") or entry.get("name") or "")
        for attr in ("path", "file_path", "name"):
            value = getattr(entry, attr, None)
            if value:
                return str(value)
        return str(entry or "")

    def _download_files_indicate_collection(self, item: Any) -> bool:
        files = list(getattr(item, "files", []) or [])
        entries = [{"file_path": getattr(file_info, "file_path", "")} for file_info in files]
        return self._file_entries_indicate_collection(entries)

    def unit_descriptor_from_file(self, file_path: str, parsed: Any | None = None, item_descriptor: dict[str, Any] | None = None) -> dict[str, Any]:
        """Describe a file inside a movie torrent by parsed title/year."""
        parsed = parsed or self.parse_name(Path(str(file_path or '')).stem)
        title = getattr(parsed, 'title', '') or Path(str(file_path or '')).stem
        year = getattr(parsed, 'year', None)
        title_key = canonical_item_key(title)
        return {
            'granularity': 'file',
            'label': f"{title} ({year})" if year else title,
            'stable_key': f"movie-file:{title_key}:{year or ''}",
            'sort_key': [title_key, year or 0],
            'coordinates': {'title': title, 'title_key': title_key, 'year': year},
        }

    def torrent_file_matches_target(
        self,
        *,
        file_path: str,
        parsed: Any | None,
        file_descriptor: dict[str, Any],
        target_descriptors: list[dict[str, Any]],
    ) -> bool:
        """Select movie payload files matching the requested title/year."""
        path = Path(str(file_path or ''))
        lower_parts = {part.lower() for part in path.parts}
        if 'sample' in lower_parts or lower_parts.intersection(_IGNORED_MOVIE_SUBDIRS):
            return False
        if path.suffix.lower() not in _VIDEO_EXTENSIONS:
            return False
        coords = file_descriptor.get('coordinates') if isinstance(file_descriptor.get('coordinates'), dict) else {}
        file_key = str(coords.get('title_key') or '')
        file_year = coords.get('year')
        for target in target_descriptors or []:
            target_coords = target.get('coordinates') if isinstance(target.get('coordinates'), dict) else {}
            target_key = str(target_coords.get('title_key') or canonical_item_key(str(target_coords.get('title') or target.get('label') or '')))
            target_year = target_coords.get('year')
            if not target_key or not file_key:
                continue
            same_title = file_key == target_key or target_key in file_key or file_key in target_key
            same_year = not target_year or not file_year or str(target_year) == str(file_year)
            if same_title and same_year:
                return True
        return False

    def torrent_file_priority(
        self,
        *,
        file_path: str,
        parsed: Any | None,
        file_descriptor: dict[str, Any],
        selected: bool,
    ) -> int:
        """Prioritize selected movie payloads and ignore samples/extras."""
        if not selected:
            return 0
        suffix = Path(str(file_path or '')).suffix.lower()
        if suffix in _VIDEO_EXTENSIONS:
            return 4
        if suffix in {'.srt', '.ass', '.ssa', '.sub', '.idx'}:
            return 2
        return 0

    def download_target_for_item(
        self,
        source: Path,
        item: Any,
        settings: "Settings",
        *,
        source_name: str | None = None,
        file_info: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        """Return the target path for a completed movie-category download.

        Movie naming is deliberately implemented in the movie category rather
        than the download handler. The handler only supplies persisted planning
        metadata and the source file.
        """
        data = dict(metadata or {})
        if self._is_movie_collection_download(item, source_name=source_name, source=source):
            return self._collection_target_path(
                source=source,
                item=item,
                settings=settings,
                source_name=source_name or source.name,
                file_info=file_info,
            )
        return self.compute_target_path(
            source_name=source_name or source.name,
            item_name=clean_display_title(data.get("title") or getattr(item, "item_name", "") or source.stem, fallback="Unknown"),
            year=data.get("year") or getattr(item, "year", None),
            settings=settings,
            library_root=self.get_root_path(settings),
        )

    def ready_import_file_allowed(
        self,
        source_path: Path,
        item: Any | None = None,
        file_info: Any | None = None,
        settings: Any | None = None,
    ) -> bool:
        """Import only actual movie payload files as primary library media."""
        return Path(str(source_path or "")).suffix.lower() in _VIDEO_EXTENSIONS

    def _is_movie_collection_download(self, item: Any, *, source_name: str | None = None, source: Path | None = None) -> bool:
        """Return whether a movie download should preserve collection structure.

        This is based on category-owned payload structure, not title keywords:
        multiple primary video files with distinct parsed movie identities mean
        this download is a collection container.
        """
        context = getattr(item, "import_context", None)
        candidate = getattr(context, "candidate_snapshot", {}) if context is not None else {}
        bundle = candidate.get("bundle_context") if isinstance(candidate, dict) else None
        if isinstance(bundle, dict) and str(bundle.get("bundle_type") or "").strip() == "movie_collection":
            return True
        return self._download_files_indicate_collection(item)

    def _collection_folder_title(self, item: Any, source_name: str | None = None) -> str:
        """Return a stable folder title for a downloaded movie collection."""
        context = getattr(item, "import_context", None)
        candidate = getattr(context, "candidate_snapshot", {}) if context is not None else {}
        for value in (
            candidate.get("title") if isinstance(candidate, dict) else "",
            getattr(context, "release_title", "") if context is not None else "",
            getattr(item, "torrent_title", ""),
            source_name or "",
            getattr(item, "item_name", ""),
        ):
            text = clean_release_title(str(value or "")).strip()
            if text:
                return text
        return clean_display_title(getattr(item, "item_name", "") or source_name or "Movie Collection", fallback="Movie Collection")

    def _collection_target_path(
        self,
        *,
        source: Path,
        item: Any,
        settings: "Settings",
        source_name: str,
        file_info: Any | None = None,
    ) -> Path:
        """Preserve collection folder plus each source filename for movie bundles."""
        root = Path(self.get_root_path(settings))
        collection_folder = clean_path_fragment(self._collection_folder_title(item, source_name), fallback="Movie Collection")
        raw_relative = str(getattr(file_info, "file_path", "") or source_name or source.name).replace("\\", "/")
        parts = [part for part in raw_relative.split("/") if part not in {"", ".", ".."}]
        if not parts:
            parts = [source.name]
        if len(parts) > 1:
            first = clean_path_fragment(parts[0], fallback="")
            if first and first.casefold() == collection_folder.casefold():
                parts = parts[1:]
        safe_parts = [clean_path_fragment(part, fallback="file") for part in parts]
        if not safe_parts:
            safe_parts = [basename_from_pathish(source.name, fallback="movie.mkv")]
        return root.joinpath(collection_folder, *safe_parts)

    def compute_target_path(self, source_name: str, item_name: str,
                            season: int = 0, episode: int = 0, **kwargs: Any) -> Path:
        """Compute the library path for a completed movie file.

        DownloadCompletionHandler needs a non-mutating path planner so it can
        hardlink/copy completed torrents into the movie library while the
        original torrent payload remains available for post-download seeding.
        This mirrors ``organize()`` but deliberately performs no file operation.
        """
        settings = kwargs.get("settings")
        library_root = Path(kwargs.get("library_root") or self.get_root_path(settings))
        parsed = self.parse_name(source_name or item_name)
        safe_source = basename_from_pathish(source_name, fallback="movie.mkv")
        title = (item_name or parsed.title or Path(safe_source).stem or "Unknown").strip()
        # Prefer explicit metadata year, then parsed file/torrent year.
        year = kwargs.get("year") or parsed.year
        folder_name = clean_path_fragment(f"{title} ({year})" if year else title, fallback="Unknown")
        suffix = Path(safe_source).suffix or ".mkv"
        return library_root / folder_name / f"{folder_name}{suffix}"


    def organize(self, source: Path, settings: "Settings", metadata: dict) -> str | None:
        """Organize a movie file into the library."""
        movie_name = metadata.get("movie_name") or metadata.get("item_name") or metadata.get("title") or "Unknown"
        year = metadata.get("year")

        root = Path(self.get_root_path(settings))
        folder_name = f"{movie_name} ({year})" if year else movie_name
        target_dir = root / folder_name

        filename = f"{folder_name}{source.suffix}"
        target = target_dir / filename
        try:
            resolver = SafePathResolver.for_category(self, settings)
            resolver.safe_mkdir(target_dir, purpose="movie.organize.mkdir")
            safe_target = resolver.ensure_destination(target, purpose="movie.organize.target")
            resolver.safe_rename(source, safe_target, purpose="movie.organize.rename")
            return str(safe_target)
        except SecurityPolicyError as exc:
            logger.error(f"Movie organize blocked unsafe path: {exc}")
            return None

    def delete(self, name: str, settings: "Settings", season: int | None = None,
               episode: int | None = None, year: int | None = None) -> bool:
        """Delete a movie from the library."""
        root = Path(self.get_root_path(settings))
        if not root.exists():
            return False
        resolver = SafePathResolver.for_category(self, settings)
        for d in root.iterdir():
            if d.is_dir() and name.lower() in d.name.lower():
                try:
                    resolver.safe_rmtree(d, purpose="movie.delete", move_to_trash=False)
                    return True
                except SecurityPolicyError as exc:
                    logger.warning(f"Movie delete blocked unsafe path: {exc}")
                    return False
        return False




    def summarize_item_for_llm(self, item: Any) -> dict[str, Any]:
        """Return movie-owned tracked-item context for prompts."""
        summary = super().summarize_item_for_llm(item)
        summary.update({
            "year": getattr(item, "year", None),
            "resolution": getattr(item, "resolution", None),
            "codec": getattr(item, "codec", None),
            "tmdb_id": getattr(item, "tmdb_id", None),
            "overview": (getattr(item, "overview", "") or "")[:300],
            "genres": getattr(item, "genres", []) or [],
            "instruction": "For movie tools, pass the exact movie key/title and year separately when available.",
        })
        return summary

    async def build_item_detail_payload(
        self,
        item_id: str,
        item: Any,
        settings: "Settings",
        db: Any | None = None,
        artwork_manager: Any | None = None,
    ) -> dict[str, Any]:
        """Build a movie detail payload using movie-owned metadata semantics."""
        payload = await super().build_item_detail_payload(
            item_id=item_id, item=item, settings=settings, db=db, artwork_manager=artwork_manager,
        )
        metadata = payload.get("metadata") or {}
        if metadata:
            payload.setdefault("year", metadata.get("year") or metadata.get("release_year"))
            payload.setdefault("runtime", metadata.get("runtime"))
            payload.setdefault("status", metadata.get("status"))
        return payload

    async def enrich_taste_metadata(self, item: Any, context: Any) -> dict[str, Any] | None:
        """Return movie-owned metadata for taste profiling.

        The generic taste profiler deliberately does not know how movies are
        enriched. This category owns the decision to use TMDB feature metadata
        and returns a normalized envelope for aggregation.
        """
        enricher = getattr(context, "metadata_enricher", None)
        if not enricher or not self.metadata_provider_enabled(getattr(context, "settings", None), "tmdb", True):
            return None
        record = await enricher.enrich_feature(item.key)
        metadata = self.normalize_taste_metadata_payload(item, record, "tmdb_movie")
        if metadata:
            metadata = await self.cache_metadata_artwork(item, metadata, context, provider="tmdb_movie")
        return metadata

    async def enquire(self, name: str, settings: "Settings", db: "Database") -> dict[str, Any]:
        """Enquire about a movie (local database tracked state, and TMDB reality cached in DB)."""
        logger.info(f"[MovieCategory] Enquiring about Movie '{name}'")
        
        # 1. Local tracking settings
        tracked_item = None
        configured_language = "English"
        enabled = False
        for item in settings.tracked_items:
            if item.key.lower() == name.lower():
                tracked_item = item
                configured_language = getattr(item, "language", "English")
                enabled = item.enabled
                break
                
        # 2. Check if movie is already present in downloaded library files/folders
        downloaded = False
        if db and db.downloads:
            try:
                dl_items = await db.downloads.get_recent_downloads(limit=100)
                for item in dl_items:
                    if item.item_name.lower() == name.lower() and item.status == "complete":
                        downloaded = True
                        break
            except Exception as e:
                logger.error(f"[MovieCategory] Failed to get recent downloads: {e}")

        # 3. Retrieve or Refresh TMDB Movie Metadata using caching!
        from datetime import datetime, timezone
        from src.core.categories.metadata.enricher import TMDBMetadataEnricher
        
        cached_meta = None
        if db and db.media:
            try:
                from src.core.models import CategoryMediaMetadata
                rows = await db.media.get_category_metadata(self.category_id, name, provider="tmdb_movie")
                if rows:
                    cached_meta = CategoryMediaMetadata(**rows[0]["metadata"])
            except Exception as e:
                logger.error(f"[MovieCategory] Failed to load cached movie metadata: {e}")
                
        now = datetime.now(timezone.utc)
        should_refresh = True
        
        if cached_meta and cached_meta.enriched_at:
            try:
                enriched_time = datetime.fromisoformat(cached_meta.enriched_at)
                # Cache for 24 hours, but bypass if TMDB ID or poster is missing/empty
                has_tmdb = getattr(cached_meta, "tmdb_id", None) is not None
                has_poster = bool(getattr(cached_meta, "poster_path", ""))
                if (now - enriched_time).total_seconds() < 86400 and has_tmdb and has_poster:
                    should_refresh = False
            except Exception:
                pass
                
        if should_refresh:
            logger.info(f"[MovieCategory] Cache stale/missing. Querying TMDB for '{name}'...")
            from src.integrations.tmdb import TMDBClient
            api_key = settings.category_service_value(self.category_id, "tmdb", "api_key")
            if api_key and self.metadata_provider_enabled(settings, "tmdb", True):
                try:
                    client = TMDBClient(api_key)
                    enricher = TMDBMetadataEnricher(tmdb_client=client)
                    refreshed_meta = await enricher.enrich_feature(name)
                    if refreshed_meta and refreshed_meta.tmdb_id:
                        cached_meta = refreshed_meta
                        if db and db.media:
                            await db.media.upsert_category_metadata(
                                self.category_id,
                                refreshed_meta.display_name or name,
                                "tmdb_movie",
                                refreshed_meta.model_dump(),
                                str(refreshed_meta.tmdb_id or ""),
                            )
                    await client.close()
                except Exception as e:
                    logger.error(f"[MovieCategory] Failed to refresh TMDB movie metadata: {e}")
                    
        # 4. Formulate response
        response = {
            "category_id": self.category_id,
            "title": name,
            "tracked": tracked_item is not None,
            "enabled": enabled,
            "configured_language": configured_language,
            "downloaded": downloaded,
        }
        
        if cached_meta:
            response.update({
                "tmdb_id": cached_meta.tmdb_id,
                "overview": cached_meta.overview,
                "genres": cached_meta.genres,
                "release_date": cached_meta.first_release_date,
                "runtime_minutes": cached_meta.runtime_minutes,
                "rating": cached_meta.rating,
            })
        else:
            response["note"] = "TMDB reality details could not be loaded; displaying local library state only."
            
        return response
