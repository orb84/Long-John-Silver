"""
TV Show category for LJS.

Implements MediaCategory for episodic television content.
Handles season/episode naming, SxxExx search patterns,
TVMaze integration, and TV-specific file organization.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from datetime import datetime, timezone
from loguru import logger

from src.core.categories.base import CategoryMedia
from src.core.categories.tv_agent import TvAgentSearchMixin
from src.core.categories.tv_context import TvContextMixin
from src.core.categories.tv_metadata_info import TvMetadataInfoMixin
from src.core.categories.tv_workflows import TvWorkflowMixin
from src.core.categories.season_folders import SeasonFolderLayout
from src.core.categories.search_patterns import SearchPatterns
from src.core.categories.types import ParsedMedia, ScannedItem, ScannedFileObservation
from src.core.categories.media_probe import probe_media_files_serial, resolution_label_from_probe_payload
from src.core.categories.video_sidecars import plan_video_sidecar_imports
from src.core.security.path_policy import SafePathResolver, SecurityPolicyError
from src.core.categories.identity import clean_display_title, canonical_item_key, clean_release_title, extract_release_year, basename_from_pathish
from src.core.categories.tv_bundle import TVBundleKnowledge
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

from src.core.categories.tv_patterns import _ANIME_PATTERNS, _EPISODE_FILE, _RELEASE_GROUP_RE, _SEASON_DIR, _TV_PATTERNS


@dataclass
class TvSearchPatterns(SearchPatterns):
    """TV-specific search query patterns with SxxExx format."""

    def build_primary_query(self, media_name: str, language: str,
                            progress: dict | None = None) -> str:
        """Build the primary query representation.

        Keep construction deterministic and side-effect free.  Future
        extensions should add optional inputs or collaborators rather than
        hard-coding category or provider-specific behavior here.
        """
        query = media_name
        if progress:
            season = progress.get("last_season")
            episode = progress.get("last_episode")
            if season is not None and episode is not None:
                next_ep = int(episode) + 1
                query += f" S{int(season):02d}E{next_ep:02d}"
        return self._append_language(query, language)

    def build_alternative_queries(self, media_name: str, language: str,
                                   progress: dict | None = None) -> list[str]:
        """Build the alternative queries representation.

        Keep construction deterministic and side-effect free.  Future
        extensions should add optional inputs or collaborators rather than
        hard-coding category or provider-specific behavior here.
        """
        if not progress:
            return []
        season = progress.get("last_season")
        episode = progress.get("last_episode")
        if season is None or episode is None:
            return []
        next_ep = int(episode) + 1
        s, e = int(season), next_ep
        queries = [
            f"{media_name}.S{s:02d}E{e:02d}",
            f"{media_name} {s}x{e:02d}",
            f"{media_name} S{s:02d}E{e:02d} 1080p",
        ]
        return [self._append_language(q, language) for q in queries]

    def build_pack_query(self, media_name: str, language: str,
                          season: int | None = None) -> str | None:
        """Build the pack query representation.

        Keep construction deterministic and side-effect free.  Future
        extensions should add optional inputs or collaborators rather than
        hard-coding category or provider-specific behavior here.
        """
        if season is not None:
            return self._append_language(
                f"{media_name} S{season:02d} Complete", language,
            )
        return None


class TvShowCategory(TvMetadataInfoMixin, TvContextMixin, TvAgentSearchMixin, TvWorkflowMixin, CategoryMedia):
    """Television shows with seasons and episodes."""

    category_id = "tv"
    display_name = "TV Shows"
    default_folder = "TV Shows"
    icon = "tv"
    capabilities = ["metadata", "episodic", "downloadable", "scheduled_updates", "file_organization", "subtitles"]
    metadata_provider_names = ["tmdb", "tvmaze"]
    supported_operations = [
        "search", "download", "scan", "organize", "refresh_metadata",
        "find_missing_episodes", "download_next_missing_episode", "search_season_pack",
    ]
    category_tool_names = [
        "tv.resolve_show",
        "tv.refresh_metadata",
        "tv.find_missing_episodes",
        "tv.download_next_missing_episode",
        "tv.download_specific_episode",
        "tv.download_season_pack",
        "tv.scan_library",
        "tv.delete_item",
    ]
    prompt_file = "tv.md"
    is_episodic = True
    _default_naming_template = '{title} ({year})/Season {season}/{title} - S{season:02d}E{episode:02d}'



    def build_search_query(self, item: Any, unit_label: str | None, language: str | None) -> str:
        """Return the first TV torrent query for a specific unit.

        For TV episodes, language is mostly a ranking/confirmation facet, not a
        safe hard search term.  The provider query starts broad enough to find
        the exact SxxEyy release; language-specific variants live in the TV
        alternative ladder and final queueing still enforces the preferred
        media-language policy.
        """
        title = str(getattr(item, "key", "") or "").strip()
        label = str(unit_label or "").strip()
        return f"{title} {label}".strip() if label else title

    def build_alternative_search_queries(self, item: Any, unit_label: str | None, language: str | None) -> list[str]:
        """Return TV-owned fallback queries for exact episode searches.

        This deliberately tries bare exact forms before language-tagged forms:
        indexers often list TV releases as WEB/ATVP/GRACE/Kitsune/EZTV without
        an ``ITA`` token even when the episode is real.  Preferred language is
        still passed to ranking and final confirmation checks.
        """
        title = str(getattr(item, "key", "") or "").strip()
        label = str(unit_label or "").strip()
        if not title or not label:
            return []
        season, episode = self._unit_coordinates(label)
        if not season or not episode:
            return []
        dotted_title = re.sub(r"\s+", ".", title)
        base_queries = [
            f"{title} S{season:02d}E{episode:02d}",
            f"{dotted_title}.S{season:02d}E{episode:02d}",
            f"{title} {season}x{episode:02d}",
            f"{title} S{season:02d}E{episode:02d} 1080p",
            f"{title} S{season:02d}E{episode:02d} HEVC",
            f"{title} S{season:02d}E{episode:02d} x265",
            # Exact-episode releases are not always published separately. Keep
            # season-pack schemas in the episode ladder so a single requested
            # episode can be file-selected from inside a torrent bundle.
            f"{title} S{season:02d}",
            f"{dotted_title}.S{season:02d}",
            f"{title} Season {season}",
            f"{title} S{season:02d} Complete",
            f"{title} Season {season} Complete",
            f"{title} S{season:02d} Pack",
        ]
        language_queries: list[str] = []
        preferred = str(language or "").strip()
        if preferred:
            for query in [base_queries[0], base_queries[3]]:
                tagged = self._append_search_language(query, preferred)
                if tagged != query:
                    language_queries.append(tagged)
            language_queries.append(f"{base_queries[0]} MULTI")
        seen: set[str] = set()
        out: list[str] = []
        for query in [*base_queries, *language_queries]:
            normalized = query.casefold().strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                out.append(query.strip())
        return out

    def validate_search_result_for_request(self, result: Any, item: Any, unit_label: str | None) -> bool:
        """Validate that a TV candidate can satisfy the requested unit.

        Exact SxxEyy files are preferred, but a specific episode may only be
        available inside a season/series pack.  The TV category therefore treats
        a pack as structurally valid when its own pack parser says the requested
        episode could be contained in it.  Selection/downloading then relies on
        LLM ranking plus selective torrent-file priorities rather than pretending
        a title regex can fully evaluate the release.
        """
        requested_season, requested_episode = self._unit_coordinates(str(unit_label or ""))
        if not requested_season or not requested_episode:
            return super().validate_search_result_for_request(result, item, unit_label)
        title = str(getattr(result, "title", "") or "")
        if not self._title_matches_requested_series(title, str(getattr(item, "key", "") or "")):
            return False
        candidate_season, candidate_episode = self._unit_coordinates(title)
        if candidate_season == requested_season and candidate_episode == requested_episode:
            return True
        return self._bundle_contains_episode(title, requested_season, requested_episode)

    def prefer_llm_search_selection(self, *, item: Any, unit_label: str | None, mode: str, candidates: list[Any]) -> bool:
        """TV wants the LLM to choose among plausible episode releases.

        Deterministic TV checks only establish structural plausibility: exact
        episode, candidate season pack, obvious blacklist rejection, and so on.
        The final choice should consider title nuance, seeders, language
        evidence, source health, exact-vs-pack tradeoffs, and whether a selective
        file download is needed.
        """
        return bool(unit_label and mode in {"auto", "llm"} and len(candidates or []) > 1)

    def download_coordinates_from_search_result(self, result: Any, item: Any, unit_label: str | None) -> dict[str, Any]:
        """Return category-owned season/episode coordinates for a selected TV result."""
        descriptor = self.unit_descriptor_from_search_result(result, item, unit_label)
        coordinates = descriptor.get("coordinates") if isinstance(descriptor.get("coordinates"), dict) else {}
        return dict(coordinates or {})

    def unit_descriptor_from_agent_args(self, *, season: int | None = None, episode: int | None = None, **_: Any) -> dict[str, Any]:
        """Return a TV episode/season descriptor from assistant arguments."""
        season_i = self._safe_positive_int(season)
        episode_i = self._safe_positive_int(episode)
        if season_i and episode_i:
            label = f"S{season_i:02d}E{episode_i:02d}"
            return {
                "granularity": "episode",
                "label": label,
                "stable_key": label,
                "sort_key": [season_i, episode_i],
                "coordinates": {"season": season_i, "episode": episode_i},
            }
        if season_i:
            label = f"Season {season_i}"
            return {
                "granularity": "season",
                "label": label,
                "stable_key": f"S{season_i:02d}",
                "sort_key": [season_i, 0],
                "coordinates": {"season": season_i},
            }
        return {"granularity": "item", "label": "", "coordinates": {}}

    def unit_descriptor_from_search_result(self, result: Any, item: Any, unit_label: str | None) -> dict[str, Any]:
        """Return the requested TV unit descriptor carried into download/import state."""
        season, episode = self._unit_coordinates(str(unit_label or ""))
        if not season or not episode:
            parsed = self.parse_name(str(getattr(result, "title", "") or ""))
            season = self._safe_positive_int(parsed.season) or season
            episode = self._safe_positive_int(parsed.episode) or episode
        return self.unit_descriptor_from_agent_args(season=season, episode=episode)

    def torrent_bundle_candidate_context(self, result: Any, item: Any | None = None, unit_label: str | None = None) -> dict[str, Any] | None:
        """Annotate TV candidates that are season/series bundles."""
        title = str(getattr(result, "title", "") or "")
        pack = TVBundleKnowledge.detect_season_pack(title)
        if not pack:
            return None
        requested_season, requested_episode = self._unit_coordinates(str(unit_label or ""))
        context = dict(pack)
        context["category"] = self.category_id
        context["is_bundle"] = True
        context["bundle_type"] = str(pack.get("pack_type") or "tv_pack")
        context["bundle_kind"] = "tv_season_pack"
        if requested_season:
            context["requested_season"] = requested_season
        if requested_episode:
            context["requested_episode"] = requested_episode
            context["contains_requested_unit"] = self._bundle_contains_episode(title, requested_season, requested_episode)
            context["selective_download_required"] = True
        count = self._bundle_episode_count_hint(pack, title)
        if count:
            context["unit_count"] = count
        return {key: value for key, value in context.items() if value not in (None, "", [], {})}

    def estimate_bundle_unit_size_mb(
        self,
        *,
        total_size_bytes: int,
        title: str,
        bundle_context: dict[str, Any] | None = None,
        target_descriptor: dict[str, Any] | None = None,
    ) -> float:
        """Estimate useful per-episode size for TV pack candidates."""
        context = bundle_context or {}
        count = self._safe_positive_int(context.get("unit_count"))
        if count:
            return (total_size_bytes / (1024 * 1024)) / count
        return TVBundleKnowledge.estimate_per_episode_size_mb(total_size_bytes, title)

    def unit_descriptor_from_file(self, file_path: str, parsed: Any | None = None, item_descriptor: dict[str, Any] | None = None) -> dict[str, Any]:
        """Describe one torrent payload file as a TV episode when possible."""
        parsed = parsed or self.parse_name(Path(file_path).stem)
        season = self._safe_positive_int(getattr(parsed, "season", None))
        episode = self._safe_positive_int(getattr(parsed, "episode", None))
        if season and episode:
            return self.unit_descriptor_from_agent_args(season=season, episode=episode)
        # Torrent file names inside a Season folder often omit Sxx and only keep
        # Eyy.  Use the category-owned season folder parser as a local hint.
        folder_season = self._season_from_path(file_path)
        episode_only = self._episode_from_path(file_path)
        if folder_season and episode_only:
            return self.unit_descriptor_from_agent_args(season=folder_season, episode=episode_only)
        return super().unit_descriptor_from_file(file_path, parsed, item_descriptor)

    def torrent_file_matches_target(
        self,
        *,
        file_path: str,
        parsed: Any | None,
        file_descriptor: dict[str, Any],
        target_descriptors: list[dict[str, Any]],
    ) -> bool:
        """Return whether a torrent payload file matches the requested TV episode."""
        coordinates = file_descriptor.get("coordinates") if isinstance(file_descriptor.get("coordinates"), dict) else {}
        file_season = self._safe_positive_int(coordinates.get("season"))
        file_episode = self._safe_positive_int(coordinates.get("episode"))
        if not file_season or not file_episode:
            return False
        suffix = Path(file_path).suffix.lower()
        if suffix and suffix not in {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".wmv", ".srt", ".ass", ".ssa", ".sub", ".vtt"}:
            return False
        lower_path = str(file_path or "").lower()
        if any(token in lower_path for token in ("sample", "trailer", "extras", "behind.the.scenes", "behind the scenes")):
            return False
        for target in target_descriptors or []:
            target_coords = target.get("coordinates") if isinstance(target.get("coordinates"), dict) else {}
            if self._safe_positive_int(target_coords.get("season")) == file_season and self._safe_positive_int(target_coords.get("episode")) == file_episode:
                return True
        return False

    def torrent_file_priority(self, *, file_path: str, parsed: Any | None, file_descriptor: dict[str, Any], selected: bool) -> int:
        """Return libtorrent priority for selected TV episode/subtitle files."""
        if not selected:
            return 0
        suffix = Path(file_path).suffix.lower()
        if suffix in {".srt", ".ass", ".ssa", ".sub", ".vtt"}:
            return 2
        return 5

    @staticmethod
    def _bundle_contains_episode(title: str, season: int, episode: int) -> bool:
        pack = TVBundleKnowledge.detect_season_pack(title)
        if not pack or not season or not episode:
            return False
        pack_type = str(pack.get("pack_type") or "")
        if pack_type == "series_complete":
            return True
        start = TvShowCategory._safe_positive_int(pack.get("season_start")) or TvShowCategory._safe_positive_int(pack.get("season"))
        end = TvShowCategory._safe_positive_int(pack.get("season_end")) or start
        if not start or not end or not (start <= int(season) <= end):
            return False
        if str(pack.get("scope")) == "episode_range":
            ep_start = TvShowCategory._safe_positive_int(pack.get("start"))
            ep_end = TvShowCategory._safe_positive_int(pack.get("end"))
            return bool(ep_start and ep_end and ep_start <= int(episode) <= ep_end)
        return True

    @staticmethod
    def _bundle_episode_count_hint(pack: dict[str, Any], title: str) -> int | None:
        if not pack:
            return None
        if pack.get("scope") == "episode_range":
            start = TvShowCategory._safe_positive_int(pack.get("start"))
            end = TvShowCategory._safe_positive_int(pack.get("end"))
            if start and end and end >= start:
                return end - start + 1
        if pack.get("pack_type") == "multi_season":
            start = TvShowCategory._safe_positive_int(pack.get("season_start"))
            end = TvShowCategory._safe_positive_int(pack.get("season_end"))
            if start and end and end >= start:
                return TVBundleKnowledge.approximate_episode_count(title, end - start + 1)
        if pack.get("pack_type") == "series_complete":
            return 60
        return TVBundleKnowledge.approximate_episode_count(title, 1)

    @staticmethod
    def _season_from_path(file_path: str) -> int | None:
        for part in Path(file_path).parts[:-1]:
            match = _SEASON_DIR.search(part)
            if match:
                return TvShowCategory._safe_positive_int(match.group(1))
        return None

    @staticmethod
    def _episode_from_path(file_path: str) -> int | None:
        name = Path(file_path).stem
        match = _EPISODE_FILE.search(name)
        if not match:
            return None
        groups = match.groups()
        value = groups[1] or groups[3] or groups[4]
        return TvShowCategory._safe_positive_int(value)

    def provider_setup_requirements(self, settings: 'Settings') -> list[CategorySetupRequirement]:
        """Return shared media setup requirements plus TV-specific TVMaze."""
        requirements = list(super().provider_setup_requirements(settings))
        tvmaze = CategorySetupRequirement(
            id="tvmaze_provider",
            label="TVMaze episode schedule provider",
            description="Keyless episode schedule lookup used for aired/future episode checks.",
            required=False,
            configured=self.category_service_enabled(settings, "tvmaze", True),
            setting_key="category_config.tv.services.tvmaze.enabled",
            severity="recommended",
            why_it_matters="TVMaze helps avoid searching for unaired future episodes.",
        )
        return [requirements[0], tvmaze, *requirements[1:]] if requirements else [tvmaze]

    def lifecycle_policy(self) -> dict[str, Any]:
        """Declare TV-owned lifecycle, suggestion, and taste invalidation rules."""
        return {
            "policy_version": 2,
            "identity_fields": ["provider", "external_id", "title", "first_air_date"],
            "lifecycle_fields": [
                "airing_status", "next_air_date", "season_status",
                "missing_episodes", "language", "quality", "release_cadence", "specials",
            ],
            "suggestion_types": [
                "missing_episode", "download_next", "download_all_missing",
                "download_remaining_next", "quality_upgrade", "related_media", "metadata_repair",
            ],
            "invalidation_triggers": [
                "library_changed", "metadata_changed", "taste_changed",
                "download_completed", "download_failed", "new_episode_detected",
                "user_quality_or_language_changed", "manual_refresh", "policy_version_changed",
            ],
            "active_check_interval_days": 7,
            "inactive_check_interval_days": 90,
            "ended_check_interval_days": 180,
            "upgrade_scan_interval_days": 30,
            "default_check_interval_days": 30,
            "llm_policy_description": (
                "For TV, suggestions depend on airing status, next known episode date, missing aired episodes, "
                "season completeness, language, release quality, and upgrade opportunities. Stable suggestions should "
                "remain valid until the next known air date or a library/provider/preference change invalidates them."
            ),
        }

    def lifecycle_decision(self, item: Any, context: dict[str, Any]) -> dict[str, Any]:
        """Choose the next useful TV check without forcing provider refreshes."""
        from datetime import datetime, timedelta, timezone

        policy = context.get("policy") or self.lifecycle_policy()
        state = getattr(item, "state", {}) or {}
        lifecycle = str(state.get("lifecycle") or getattr(item, "_lifecycle", "") or "unknown").lower()
        next_air_date = state.get("next_air_date")

        def parse_dt(value: object):
            """Parse provider date/time values into timezone-aware datetimes when possible."""
            if not value:
                return None
            try:
                text = str(value).replace("Z", "+00:00")
                parsed = datetime.fromisoformat(text)
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except Exception:
                return None

        now = datetime.now(timezone.utc)
        next_air = parse_dt(next_air_date)
        if next_air and next_air > now:
            # Recheck shortly after the known episode date so missing-episode suggestions
            # do not churn for months while still waking up when new content can exist.
            check_at = next_air + timedelta(hours=6)
            return {
                "next_check_at": check_at.isoformat(),
                "valid_until": check_at.isoformat(),
                "reason": f"Next known TV episode airs around {next_air.date().isoformat()}.",
                "confidence": 0.85,
            }

        if lifecycle in {"ended", "cancelled", "finished"}:
            days = int(policy.get("ended_check_interval_days") or 180)
            reason = "Ended TV show; rare metadata/upgrade refresh only."
        elif lifecycle in {"between_seasons", "hiatus", "paused"}:
            days = int(policy.get("inactive_check_interval_days") or 90)
            reason = "TV show is between seasons or on hiatus."
        elif lifecycle in {"active_airing", "running", "returning"}:
            days = int(policy.get("active_check_interval_days") or 7)
            reason = "Actively airing TV show; regular missing-episode check."
        else:
            days = int(policy.get("default_check_interval_days") or 30)
            reason = "Unknown TV lifecycle; moderate check cadence."
        check_at = now + timedelta(days=max(days, 1))
        return {
            "next_check_at": check_at.isoformat(),
            "valid_until": check_at.isoformat(),
            "reason": reason,
            "confidence": 0.75,
        }


    async def next_scheduled_unit(self, item: Any, context: dict[str, Any]) -> dict[str, Any] | None:
        """Return the next relevant TV episode schedule update.

        This keeps TVMaze and episode semantics inside the TV category.  The
        scheduler only persists the returned state updates; it does not know how
        a TV schedule is resolved.
        """
        tvmaze = context.get("tvmaze")
        if not tvmaze:
            return None
        try:
            show_id = getattr(item, "tvmaze_id", None)
            if not show_id:
                results = await tvmaze.search(getattr(item, "key", ""))
                if not results:
                    return None
                show_id = results[0].get("id")
            if not show_id:
                return None
            schedule = await tvmaze.get_episode_list(show_id)
            if not schedule:
                return None
            now = datetime.now(timezone.utc)
            for raw in schedule:
                air_date_str = raw.get("airstamp") or raw.get("airdate")
                if not air_date_str:
                    continue
                try:
                    air_date = datetime.fromisoformat(str(air_date_str).replace("Z", "+00:00"))
                    if air_date.tzinfo is None:
                        air_date = air_date.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                if (now - air_date).total_seconds() > 172800:
                    continue
                return {
                    "state_updates": {
                        "next_air_date": air_date_str,
                        "next_scheduled_at": air_date_str,
                        "next_unit_title": raw.get("name", ""),
                        "next_unit": {
                            "season": raw.get("season"),
                            "episode": raw.get("number"),
                            "title": raw.get("name", ""),
                            "scheduled_at": air_date_str,
                        },
                    }
                }
        except Exception as exc:
            logger.warning(f"TV schedule lookup failed for {getattr(item, 'key', '?')}: {exc}")
        return None


    def get_properties(self, settings: 'Settings') -> list[CategoryProperty]:
        """Return the requested get properties value.

        This public accessor should normalize missing or optional data at the
        boundary and avoid leaking storage/provider internals to callers.
        """
        props = [
            CategoryProperty(
                name="library_path",
                value_type="string",
                description="Absolute path to organize TV shows.",
                default_value=""
            ),
            CategoryProperty(
                name="naming_template",
                value_type="string",
                description="Naming template for renaming media files.",
                default_value=self._default_naming_template
            ),
            CategoryProperty(
                name="active_update_interval_days",
                value_type="int",
                description="Check interval in days for TV shows currently actively airing.",
                default_value=7
            ),
            CategoryProperty(
                name="inactive_update_interval_days",
                value_type="int",
                description="Check interval in days for TV shows in hiatus or between seasons.",
                default_value=90
            ),
            CategoryProperty(
                name="ended_update_interval_days",
                value_type="int",
                description="Check interval in days for ended TV shows.",
                default_value=180
            ),
            CategoryProperty(
                name="upgrade_scan_interval_days",
                value_type="int",
                description="Check interval in days for scanning downloaded episodes for quality upgrades.",
                default_value=30
            ),
        ]
        
        # Populate values from settings when available. Path previews and tests
        # may call category formatting without a full Settings object.
        cat_configs = settings.category_settings.get(self.category_id, {}) if settings is not None else {}
        for p in props:
            p.value = cat_configs.get(p.name, p.default_value)
        return props

    def parse_name(self, name: str) -> ParsedMedia:
        """Parse a TV show torrent/file name into structured info.

        Handles SxxExx, 1x01, Season X Episode Y, season packs,
        and anime [SubGroup] patterns. Extracts resolution, codec,
        language, and release group as well.
        """
        result = ParsedMedia(original_title=name)
        cleaned = name.replace('.', ' ').replace('_', ' ').strip()

        # Try anime patterns first (most specific)
        for pattern in _ANIME_PATTERNS:
            m = pattern.search(cleaned)
            if m:
                result.title = m.group('title').strip()
                result.episode = int(m.group('episode'))
                result.release_group = m.group('release_group')
                result.is_anime = True
                break

        # Try TV patterns
        if not result.is_anime:
            for pattern in _TV_PATTERNS:
                m = pattern.search(cleaned)
                if m:
                    result.title = m.group('title').strip().rstrip('-.').strip()
                    result.season = int(m.group('season'))
                    if 'episode' in pattern.groupindex:
                        ep = m.group('episode')
                        result.episode = int(ep) if ep else None
                    break

        # Extract shared metadata
        result.resolution = self._extract_resolution(name)
        result.codec = self._extract_codec(name)
        result.language = self._extract_language(name)

        # Extract release group if not already found
        if not result.release_group:
            rg = _RELEASE_GROUP_RE.search(cleaned)
            if rg:
                result.release_group = rg.group('release_group')

        # Clean up title before metadata/search use. Torrent folders often include
        # release payload after the show name (S01-06, DLMUX, x264, groups).
        if not result.title or result.title == name:
            result.title = cleaned
        result.title = clean_release_title(result.title, fallback=cleaned, media_hint="tv")
        result.title = re.sub(r'\s+', ' ', result.title).strip()
        if result.year is None:
            result.year = extract_release_year(name)

        return result


    # ── Canonical library object contract ──────────────────────────

    def library_object_spec(self) -> dict[str, Any]:
        """Declare the TV category's canonical nested library object.

        The library core must not know about seasons or episodes.  It stores raw
        envelopes and calls these category hooks; TV alone defines that its local
        units are episodes grouped by season and that missing/quality state is
        computed from those units plus provider metadata.
        """
        return {
            "schema_version": 1,
            "item_identity_fields": ["category_id", "item_id", "display_name", "provider_ids"],
            "unit_types": {
                "file": {
                    "description": "One local episode payload file. Multiple files may point at the same logical episode.",
                    "required_fields": ["unit_key", "logical_key", "season", "episode", "file_path"],
                    "optional_fields": [
                        "title", "quality", "resolution", "codec", "language", "size_bytes",
                        "estimated_bitrate_kbps", "bitrate_source", "audio_languages", "audio_tracks",
                        "subtitle_languages", "subtitle_tracks", "media_probe", "video_width", "video_height", "resolution_source",
                        "subtitle_files", "downloaded_at",
                    ],
                },
                "progress": {
                    "description": "Derived latest local episode marker for quick legacy/status surfaces.",
                    "required_fields": ["last_season", "last_episode"],
                },
            },
            "computed_fields": [
                "season_count", "episode_count", "downloaded_episode_count", "provider_aired_episode_count",
                "missing_episodes", "quality_gaps", "language_gaps", "total_size_bytes", "has_local_files",
            ],
            "source_of_truth_rule": (
                "TV suggestions, UI, and agent status must read this canonical object; "
                "they must not reconstruct episode state from aliases or progress rows."
            ),
        }

    def library_item_from_scan(self, scanned: Any) -> dict[str, Any]:
        """Normalize a scanned TV show folder into the canonical item envelope."""
        item = super().library_item_from_scan(scanned)
        item["properties"].update({
            "season_count": int(getattr(scanned, "seasons", 0) or 0),
            "episode_count": sum(len(values or []) for values in (getattr(scanned, "episodes", {}) or {}).values()),
        })
        item["metadata"].update({
            "episodes": getattr(scanned, "episodes", {}) or {},
            "resolutions": list(getattr(scanned, "resolutions", []) or []),
            "codecs": list(getattr(scanned, "codecs", []) or []),
            "detected_languages": list(getattr(scanned, "detected_languages", []) or []),
            "subtitle_languages": list(getattr(scanned, "subtitle_languages", []) or []),
        })
        return item

    def library_units_from_scan(self, scanned: Any) -> list[dict[str, Any]]:
        """Convert scanned episode payload files into canonical TV file units.

        A logical episode may have more than one local file: alternate releases,
        redownloaded quality upgrades, split payloads, or subtitle variants.  The
        unit key is therefore a stable file identity, while ``logical_key`` holds
        the episode coordinate used by the TV canonical object and suggestions.
        Never key storage rows only by ``SxxExx`` or later files will overwrite
        earlier local evidence.
        """
        units: list[dict[str, Any]] = []
        for index, scanned_file in enumerate(list(getattr(scanned, "files", []) or []), start=1):
            season = self._safe_positive_int(getattr(scanned_file, "season", None))
            episode = self._safe_positive_int(getattr(scanned_file, "episode", None))
            if season is None or episode is None:
                continue
            file_path = str(getattr(scanned_file, "file_path", "") or "")
            parsed = self.parse_name(Path(file_path).name if file_path else "")
            probe = dict(getattr(scanned_file, "media_probe", {}) or {})
            audio_languages = list(getattr(scanned_file, "audio_languages", []) or probe.get("audio_languages") or [])
            subtitle_languages = list(getattr(scanned_file, "subtitle_languages", []) or probe.get("subtitle_languages") or [])
            stream_language = ", ".join(audio_languages)
            probe_resolution = self._resolution_from_probe(probe)
            bitrate = probe.get("bit_rate_kbps") or self._estimate_episode_bitrate_kbps(getattr(scanned_file, "size_bytes", 0))
            video_codecs = list(probe.get("video_codecs") or [])
            logical_key = f"S{season:02d}E{episode:02d}"
            identity_seed = f"{season}:{episode}:{file_path}:{getattr(scanned_file, 'size_bytes', 0) or 0}"
            file_identity = hashlib.sha1(identity_seed.encode("utf-8", errors="ignore")).hexdigest()[:16]
            unit_key = f"file:{file_identity}"
            units.append({
                "unit_key": unit_key,
                "logical_key": logical_key,
                "unit_type": "file",
                "role": "episode_payload",
                "display_name": Path(file_path).name if file_path else f"{getattr(scanned, 'name', '')} {logical_key}".strip(),
                "status": "downloaded",
                "season": season,
                "episode": episode,
                "title": f"{getattr(scanned, 'name', '')} {logical_key}".strip(),
                "quality": getattr(scanned_file, "quality", "") or probe_resolution or parsed.resolution or "unknown",
                "resolution": probe_resolution or parsed.resolution or self._resolution_from_quality(getattr(scanned_file, "quality", "")),
                "resolution_source": "ffprobe_video_stream" if probe_resolution else ("filename" if parsed.resolution or self._resolution_from_quality(getattr(scanned_file, "quality", "")) else ""),
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
                "file_path": file_path,
                "size_bytes": int(getattr(scanned_file, "size_bytes", 0) or 0),
                "estimated_bitrate_kbps": int(bitrate) if bitrate else None,
                "subtitle_files": self._subtitle_sidecars(file_path),
                "sort_index": season * 100000 + episode * 100 + index,
            })
        return units

    def library_progress_from_scan(self, scanned: Any, units: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Return the latest local episode marker from canonical episode units."""
        coords = sorted({
            (int(unit.get("season") or 0), int(unit.get("episode") or 0))
            for unit in units
            if unit.get("role") == "episode_payload" and int(unit.get("season") or 0) > 0 and int(unit.get("episode") or 0) > 0
        })
        if not coords:
            return None
        last_season = max(season for season, _ in coords)
        last_episode = max(episode for season, episode in coords if season == last_season)
        return {
            "unit_type": "progress",
            "display_name": "TV library progress",
            "last_season": last_season,
            "last_episode": last_episode,
            "downloaded_episode_count": len(coords),
        }

    def build_library_object(self, context: Any) -> dict[str, Any]:
        """Build the canonical TV show object from local files and provider metadata.

        The repository stores file units because local reality is file-shaped.
        The TV category then groups those files into logical episode objects.
        Consumers that need episode status should read ``seasons``/``computed``;
        consumers that need physical payload details should read ``units``.
        """
        item = context.item or {}
        file_units = [
            unit for unit in (context.units or [])
            if unit.get("role") == "episode_payload" or unit.get("unit_type") == "file"
        ]
        downloaded_files = [unit for unit in file_units if unit.get("status") == "downloaded"]
        episode_map: dict[tuple[int, int], dict[str, Any]] = {}
        for unit in downloaded_files:
            season = int(unit.get("season") or 0)
            episode = int(unit.get("episode") or 0)
            if season <= 0 or episode <= 0:
                continue
            logical_key = unit.get("logical_key") or f"S{season:02d}E{episode:02d}"
            entry = episode_map.setdefault((season, episode), {
                "season": season,
                "episode": episode,
                "episode_key": logical_key,
                "title": unit.get("title") or logical_key,
                "status": "downloaded",
                "files": [],
                "file_count": 0,
                "total_size_bytes": 0,
                "subtitle_files": [],
            })
            entry["files"].append(unit)
            entry["file_count"] += 1
            entry["total_size_bytes"] += int(unit.get("size_bytes") or 0)
            entry["subtitle_files"] = sorted(set(entry.get("subtitle_files") or []) | set(unit.get("subtitle_files") or []))

        for entry in episode_map.values():
            files = sorted(entry["files"], key=lambda row: (row.get("sort_index") or 0, row.get("unit_key") or ""))
            entry["files"] = files
            entry["best_resolution"] = self._best_resolution([str(row.get("resolution") or "").lower() for row in files])
            bitrates = [int(row.get("estimated_bitrate_kbps") or 0) for row in files if row.get("estimated_bitrate_kbps")]
            entry["average_bitrate_kbps"] = int(sum(bitrates) / len(bitrates)) if bitrates else None
            audio_languages: list[str] = []
            subtitle_languages: list[str] = []
            audio_tracks: list[dict[str, Any]] = []
            subtitle_tracks: list[dict[str, Any]] = []
            for row in files:
                for lang in list(row.get("audio_languages") or []):
                    if lang and lang not in audio_languages:
                        audio_languages.append(lang)
                for lang in list(row.get("subtitle_languages") or []):
                    if lang and lang not in subtitle_languages:
                        subtitle_languages.append(lang)
                audio_tracks.extend(list(row.get("audio_tracks") or []))
                subtitle_tracks.extend(list(row.get("subtitle_tracks") or []))
            # Preserve common unit-shaped fields so older detail tables still
            # render something useful while they migrate to nested file arrays.
            best_file = files[-1] if files else {}
            entry.setdefault("display_name", entry.get("episode_key"))
            entry["quality"] = best_file.get("quality") or entry.get("best_resolution") or ""
            entry["audio_languages"] = audio_languages
            entry["primary_audio_language"] = audio_languages[0] if audio_languages else best_file.get("primary_audio_language", "")
            entry["language"] = ", ".join(audio_languages) if audio_languages else best_file.get("language") or ""
            entry["audio_tracks"] = audio_tracks
            entry["subtitle_languages"] = subtitle_languages
            entry["subtitle_tracks"] = subtitle_tracks
            entry["downloaded_at"] = best_file.get("downloaded_at") or ""

        seasons: dict[str, dict[str, Any]] = {}
        for (season, episode), entry in sorted(episode_map.items()):
            season_key = str(season)
            seasons.setdefault(season_key, {"season_number": season, "season": season, "episodes": []})["episodes"].append(entry)
        for season_payload in seasons.values():
            season_payload["episodes"].sort(key=lambda row: int(row.get("episode") or 0))
            season_payload["episode_count"] = len(season_payload["episodes"])
            season_payload["file_count"] = sum(int(row.get("file_count") or 0) for row in season_payload["episodes"])

        provider_episodes = self._provider_episode_rows(context.metadata_rows)
        local_keys = set(episode_map.keys())
        missing = [row for row in provider_episodes if (row["season"], row["episode"]) not in local_keys]
        quality_gaps = self._quality_gaps(list(episode_map.values()), getattr(context.settings_item, "quality", None))
        total_size = sum(int(unit.get("size_bytes") or 0) for unit in downloaded_files)
        local_audio_languages: list[str] = []
        local_subtitle_languages: list[str] = []
        for unit in downloaded_files:
            for lang in list(unit.get("audio_languages") or []):
                if lang and lang not in local_audio_languages:
                    local_audio_languages.append(lang)
            for lang in list(unit.get("subtitle_languages") or []):
                if lang and lang not in local_subtitle_languages:
                    local_subtitle_languages.append(lang)
        local_episode_keys = [f"S{s:02d}E{e:02d}" for s, e in sorted(local_keys)]
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
            "units": downloaded_files,
            "seasons": [seasons[key] for key in sorted(seasons, key=lambda value: int(value))],
            "computed": {
                "season_count": len(seasons),
                "episode_count": len(episode_map),
                "downloaded_episode_count": len(episode_map),
                "downloaded_file_count": len(downloaded_files),
                "local_episode_keys": local_episode_keys,
                "provider_aired_episode_count": len(provider_episodes),
                "missing_episodes": missing,
                "missing_episode_count": len(missing),
                "quality_gaps": quality_gaps,
                "language_gaps": [],
                "audio_languages": local_audio_languages,
                "subtitle_languages": local_subtitle_languages,
                "total_size_bytes": total_size,
                "has_local_files": bool(downloaded_files),
            },
        }

    def create_suggestion_workflow(self, context: Any) -> Any | None:
        """Create the TV-owned suggestion workflow for the generic compiler."""
        from src.core.categories.workflows.tv_suggestions import TvSuggestionWorkflow

        return TvSuggestionWorkflow(
            db=getattr(context, "db", None),
            tmdb_client=getattr(context, "tmdb_client", None),
            tvmaze_client=getattr(context, "tvmaze_client", None),
            settings_manager=getattr(context, "settings_manager", None),
            library_object_builder=getattr(context, "library_object_builder", None),
        )

    def scan_average_bitrate_kbps(self, scanned: Any) -> int | None:
        """Estimate average bitrate for scanned episode payload files."""
        file_count = int(getattr(scanned, "file_count", 0) or 0)
        if file_count <= 0:
            return None
        avg_size = int(getattr(scanned, "total_size_bytes", 0) or 0) / file_count
        return self._estimate_episode_bitrate_kbps(avg_size)

    def rss_unit_label_from_parsed(self, parsed: Any) -> str | None:
        """Return the TV-owned unit label for an RSS release title."""
        season = self._safe_positive_int(getattr(parsed, "season", None))
        episode = self._safe_positive_int(getattr(parsed, "episode", None))
        if season is not None and episode is not None:
            return f"S{season:02d}E{episode:02d}"
        if season is not None:
            return f"Season {season}"
        return None

    @staticmethod
    def _safe_positive_int(value: Any) -> int | None:
        """Return ``value`` as a positive int, otherwise ``None``."""
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    @staticmethod
    def _resolution_from_quality(quality: str | None) -> str | None:
        """Extract a resolution token from a compact quality string."""
        if not quality:
            return None
        for token in ("2160p", "1080p", "720p", "480p", "4k"):
            if token in str(quality).lower():
                return token
        return None

    @staticmethod
    def _resolution_from_probe(probe: dict[str, Any]) -> str | None:
        """Return resolution from ffprobe video dimensions, never from size."""
        return resolution_label_from_probe_payload(probe)

    @staticmethod
    def _estimate_episode_bitrate_kbps(size_bytes: Any, runtime_minutes: int = 55) -> int | None:
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
    def _provider_episode_rows(metadata_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Extract aired provider episodes from cached category metadata."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        episodes: list[dict[str, Any]] = []
        for row in metadata_rows or []:
            metadata = row.get("metadata") or {}
            for raw in metadata.get("episodes") or metadata.get("aired_episodes") or []:
                try:
                    season = int(raw.get("season") or raw.get("season_number") or 0)
                    episode = int(raw.get("number") or raw.get("episode") or raw.get("episode_number") or 0)
                except (TypeError, ValueError):
                    continue
                if season <= 0 or episode <= 0:
                    continue
                airdate = str(raw.get("airdate") or raw.get("air_date") or "")
                if airdate and airdate > today:
                    continue
                episodes.append({
                    "season": season,
                    "episode": episode,
                    "title": raw.get("name") or raw.get("title") or f"Episode {episode}",
                    "airdate": airdate,
                })
        episodes.sort(key=lambda row: (row["season"], row["episode"]))
        return episodes


    @staticmethod
    def _best_resolution(resolutions: list[str]) -> str | None:
        """Return the highest known resolution from a set of TV payload files."""
        order = {"480p": 1, "720p": 2, "1080p": 3, "2160p": 4, "4k": 4}
        known = [value for value in resolutions if value in order]
        if not known:
            return None
        return max(known, key=lambda value: order[value])

    @staticmethod
    def _quality_gaps(units: list[dict[str, Any]], quality_profile: Any | None) -> list[dict[str, Any]]:
        """Return local logical episodes/files below the preferred resolution."""
        preferred = str(getattr(quality_profile, "preferred_resolution", "") or "").lower()
        order = {"480p": 1, "720p": 2, "1080p": 3, "2160p": 4, "4k": 4}
        if not preferred or preferred not in order:
            return []
        gaps: list[dict[str, Any]] = []
        for unit in units:
            resolution = str(unit.get("best_resolution") or unit.get("resolution") or "").lower()
            if resolution in order and order[resolution] < order[preferred]:
                gaps.append({
                    "unit_key": unit.get("unit_key"),
                    "logical_key": unit.get("episode_key") or unit.get("logical_key"),
                    "current_resolution": resolution,
                    "preferred_resolution": preferred,
                    "file_count": unit.get("file_count"),
                })
        return gaps

    async def update(self, item: 'CategoryItem', context: 'CategoryUpdateContext') -> None:
        """Periodic background update for TV shows.
        
        Handles:
        1. Checking for new episodes based on the lifecycle timer.
        2. Scanning for quality upgrades for downloaded episodes (batched by season).
        """
        from src.core.models import TvShowItem, UpgradeRecord
        from src.utils.quality import QualityAnalyzer
        from datetime import datetime, timezone
        
        if not isinstance(item, TvShowItem):
            return

        now = datetime.now(timezone.utc)
        
        # Determine cooldown based on lifecycle:
        lifecycle = getattr(item, "_lifecycle", "active_airing")
        active_days = self.get_property_value("active_update_interval_days", context.settings)
        inactive_days = self.get_property_value("inactive_update_interval_days", context.settings)
        ended_days = self.get_property_value("ended_update_interval_days", context.settings)
        upgrade_days = self.get_property_value("upgrade_scan_interval_days", context.settings)
        
        if lifecycle == "active_airing":
            cooldown = active_days * 86400
        elif lifecycle in ("between_seasons", "hiatus"):
            cooldown = inactive_days * 86400
        elif lifecycle == "ended":
            cooldown = ended_days * 86400
        else:
            cooldown = getattr(item, "check_interval_days", active_days) * 86400

        # 1. New Episode Tracking
        last_check = datetime.fromisoformat(item.last_checked_at) if item.last_checked_at else None
        if not last_check or (now - last_check).total_seconds() >= cooldown:
            logger.debug(f"[TvShowCategory] Checking for new episodes: {item.key}")
            item.last_checked_at = now.isoformat()
            
            progress = await context.db.media.get_item_progress(self.category_id, item.key)
            if progress:
                s = progress.get("last_season")
                e = progress.get("last_episode")
                if s is not None and e is not None:
                    label = f"S{int(s):02d}E{int(e) + 1:02d}"
                    await context.pipeline.run_discovery(item, episode_label=label)
            else:
                await context.pipeline.run_discovery(item)
                
        # 2. Quality Upgrade Scanning (cooldown based on upgrade_scan_interval_days)
        last_upgrade = datetime.fromisoformat(item.last_upgrade_scan_at) if item.last_upgrade_scan_at else None
        if not last_upgrade or (now - last_upgrade).total_seconds() >= upgrade_days * 86400:
            logger.debug(f"[TvShowCategory] Scanning for quality upgrades: {item.key}")
            item.last_upgrade_scan_at = now.isoformat()
            
            episodes = [
                row for row in await context.db.media.list_category_units(self.category_id, item.key, status="downloaded")
                if int(row.get("season") or 0) > 0 and int(row.get("episode") or 0) > 0
            ]
            if episodes and item.quality and item.quality.preferred_resolution:
                by_season = {}
                for ep in episodes:
                    by_season.setdefault(int(ep.get("season") or 0), []).append(ep)
                    
                target_res = item.quality.preferred_resolution
                target_rank = QualityAnalyzer.rank_resolution(target_res)
                
                for season, eps in by_season.items():
                    eps_to_upgrade = []
                    for ep in eps:
                        quality = str(ep.get("quality") or "")
                        curr_res = quality.split("/")[0] if "/" in quality else quality
                        if QualityAnalyzer.rank_resolution(curr_res) < target_rank:
                            eps_to_upgrade.append(ep)
                            
                    if not eps_to_upgrade:
                        continue
                        
                    query = f"{item.key} Season {season} {target_res}"
                    if item.language and item.language.lower() != "english":
                        query += f" {item.language}"
                        
                    results = await context.aggregator.search(query, category=self.category_id, preferred_language=item.language)
                    
                    for ep in eps_to_upgrade:
                        best = None
                        best_score = 0
                        for r in results:
                            parsed = self.parse_name(r.title)
                            # Match season pack (episode is None) or exact episode
                            if parsed.season == int(ep.get("season") or 0) and (parsed.episode is None or parsed.episode == int(ep.get("episode") or 0)):
                                if r.quality_score is not None and r.quality_score > best_score:
                                    best = r
                                    best_score = r.quality_score
                                    
                        if best and best_score > 0.8:
                            ep_quality = str(ep.get("quality") or "")
                            current_res = ep_quality.split("/")[0] if "/" in ep_quality else ep_quality
                            record = UpgradeRecord(
                                item_name=item.key,
                                current_resolution=current_res,
                                current_codecs=[],
                                best_upgrade_resolution=target_res,
                                best_upgrade_codecs=[],
                                best_upgrade_title=best.title or '',
                                best_upgrade_magnet=best.magnet or '',
                                quality_improvement=str(best.quality_score),
                                status="pending"
                            )
                            await context.db.downloads.upsert_upgrade_candidate(record)

    def build_prompt_guidance(self, for_intent: str, settings: object | None = None) -> str:
        """Return compact TV profile guidance for the active intent."""
        return self.llm_profile_for_settings(settings).format_for_prompt(for_intent)

    def _preferred_show_dir(self, root: Path, title: str) -> Path:
        """Return an existing show folder matching ``title`` when possible.

        Users may already have folders such as ``ForAllMankind`` while metadata
        and tracked items use ``For All Mankind``.  TV owns that fuzzy folder
        selection so generic download/import code does not learn TV library
        semantics.
        """
        safe_title = clean_display_title(str(title or "Untitled"), fallback="Untitled")
        canonical = canonical_item_key(safe_title)
        compact = canonical.replace(" ", "")
        try:
            children = list(root.iterdir()) if root.exists() and root.is_dir() else []
        except OSError:
            children = []
        for child in sorted(children, key=lambda p: (len(p.name), p.name.lower())):
            if not child.is_dir() or child.name.startswith("."):
                continue
            child_key = canonical_item_key(child.name)
            if child_key == canonical or child_key.replace(" ", "") == compact:
                return child
        return root / safe_title

    def sharing_save_path_for_item(self, item: Any, settings: "Settings", staging_root: Path) -> tuple[Path, bool]:
        """Return TV seed-in-place folder using the category unit descriptor.

        The download manager no longer creates ``Season NN`` paths itself. TV
        reads its descriptor/compatibility coordinates and applies the same
        existing-season-folder preservation used by normal organization.
        """
        try:
            root = Path(self.get_root_path(settings)).resolve()
        except Exception:
            return staging_root.resolve(), False
        context = getattr(item, 'import_context', None)
        title = getattr(context, 'planning_title', None) or getattr(item, 'item_name', '') or getattr(item, 'torrent_title', '') or getattr(item, 'id', '')
        save_path = self._preferred_show_dir(root, str(title or 'Untitled'))
        descriptor = getattr(context, 'unit_descriptor', {}) if context else {}
        coordinates = descriptor.get('coordinates') if isinstance(descriptor, dict) and isinstance(descriptor.get('coordinates'), dict) else {}
        season = self._safe_positive_int(coordinates.get('season')) or self._safe_positive_int(getattr(context, 'season', None) if context else None) or self._safe_positive_int(getattr(item, 'season', None))
        if season is not None:
            save_path = SeasonFolderLayout.preferred_season_dir(save_path, season, proposed_dir=save_path / f"Season {season:02d}")
        return save_path.resolve(), True

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
        """Return the safe ready-time import path for a completed TV download.

        Ready-time download exposure is not the same operation as library
        consolidation/renaming.  While the torrent is still seeding, LJS creates
        a hardlink/copy in the user's library and must preserve the original
        payload filename so later seeding cleanup and duplicate detection remain
        stable.  Therefore TV uses its category-owned conservative import
        layout directly: ``TV root / show folder / season folder / source file``.

        The configurable naming template remains valid for explicit
        consolidation/preview operations, but it is intentionally not allowed to
        produce ready-time paths such as ``Media/Season 5/Title.mkv``.
        """
        data = dict(metadata or {})
        title = clean_display_title(data.get("title") or getattr(item, "item_name", "") or source.stem, fallback="Unknown")
        file_descriptor = getattr(file_info, "unit_descriptor", {}) if file_info is not None else {}
        item_context = getattr(item, "import_context", None)
        item_descriptor = getattr(item_context, "unit_descriptor", {}) if item_context is not None else {}
        coordinates = {}
        for descriptor in (file_descriptor, item_descriptor):
            if isinstance(descriptor, dict) and isinstance(descriptor.get("coordinates"), dict):
                coordinates.update({k: v for k, v in descriptor["coordinates"].items() if v is not None})
        season = self._safe_positive_int(coordinates.get("season")) or self._safe_positive_int(getattr(file_info, "season", None)) or self._safe_positive_int(getattr(item, "season", None)) or 1
        episode = self._safe_positive_int(coordinates.get("episode")) or self._safe_positive_int(getattr(file_info, "episode", None)) or self._safe_positive_int(getattr(item, "episode", None)) or 1
        payload_name = basename_from_pathish(source_name or source.name, fallback=source.name or "file")
        return self.fallback_library_path(
            source,
            title,
            settings,
            season=season,
            episode=episode,
            source_name=payload_name,
            year=data.get("year") or getattr(item, "year", None),
            episode_title=data.get("unit_title") or getattr(file_info, "episode_title", "") or "",
        )

    def fallback_library_path(
        self,
        source: Path,
        item_name: str,
        settings: "Settings",
        *,
        season: int | None = None,
        episode: int | None = None,
        source_name: str | None = None,
        year: int | None = None,
        episode_title: str | None = None,
    ) -> Path:
        """Return a conservative TV fallback path for completed downloads.

        This is used when a user naming template or stale import row produces
        an unsafe target.  It keeps the source filename, includes the show
        folder, and preserves the existing season-folder convention.
        """
        root = Path(self.get_root_path(settings))
        show_dir = self._preferred_show_dir(root, item_name or source.stem)
        resolved_season = self._safe_positive_int(season) or 1
        season_dir = SeasonFolderLayout.preferred_season_dir(
            show_dir, resolved_season, proposed_dir=show_dir / f"Season {resolved_season:02d}"
        )
        return season_dir / (source_name or source.name)

    def compute_target_path(self, source_name: str, item_name: str,
                            season: int, episode: int, **kwargs: Any) -> Path:
        """Compute a TV target path while preserving existing season folders.

        A user library may already use ``Season 5`` while the active template
        formats ``Season 05``.  The base path planner still applies the naming
        template, then this TV-specific override rewrites only the season folder
        segment when a matching folder for the same show/season already exists.
        """
        # Category-owned aliases keep existing user naming templates working
        # without teaching the generic path planner what a TV series is.
        kwargs.setdefault("series_title", item_name)
        kwargs.setdefault("show_title", item_name)
        kwargs.setdefault("unit_title", kwargs.get("episode_title") or "")
        target = super().compute_target_path(
            source_name=source_name,
            item_name=item_name,
            season=season,
            episode=episode,
            **kwargs,
        )
        return SeasonFolderLayout.prefer_existing_parent(target, season=season)


    async def scan(self, root_path: str, existing_keys: set[str] | None = None) -> list[ScannedItem]:
        """Scan a TV root, extracting season/episode structure recursively."""
        items: list[ScannedItem] = []
        root = Path(root_path)
        try:
            show_dirs = await asyncio.to_thread(self._list_show_dirs, root)
        except OSError as e:
            logger.error(f"[TvShowCategory] Failed to access root path '{root_path}': {e}")
            return items

        for show_dir in show_dirs:
            item = await self._scan_show_dir(root, show_dir, existing_keys=existing_keys)
            if item is not None:
                items.append(item)

        return items

    async def scan_item(
        self,
        root_path: str,
        *,
        item_id: str,
        existing_keys: set[str] | None = None,
        changed_path: str | None = None,
    ) -> list[ScannedItem]:
        """Scan a single TV show folder after a known local mutation.

        The scheduler uses this after download import or item-level refreshes so
        one changed episode does not force every TV show and movie to be crawled.
        TV owns the folder resolution because only the TV category understands
        show-folder aliases such as ``ForAllMankind`` versus ``For All Mankind``.
        """
        root = Path(root_path)
        candidates: list[Path] = []
        if changed_path:
            try:
                changed = Path(changed_path).expanduser().resolve(strict=False)
                rel = changed.relative_to(root.expanduser().resolve(strict=False))
                if rel.parts:
                    candidates.append(root / rel.parts[0])
            except Exception:
                pass
        if item_id:
            candidates.append(self._preferred_show_dir(root, item_id))

        seen: set[str] = set()
        wanted = canonical_item_key(clean_category_item_name(item_id, self.category_id))
        for show_dir in candidates:
            key = str(show_dir.expanduser().resolve(strict=False))
            if key in seen:
                continue
            seen.add(key)
            if not show_dir.exists() or not show_dir.is_dir():
                continue
            item = await self._scan_show_dir(root, show_dir, existing_keys=existing_keys)
            if item is None:
                continue
            scanned_key = canonical_item_key(clean_category_item_name(item.name, self.category_id))
            if not wanted or scanned_key == wanted:
                return [item]

        return []

    async def _scan_show_dir(
        self,
        root: Path,
        show_dir: Path,
        *,
        existing_keys: set[str] | None = None,
    ) -> ScannedItem | None:
        """Scan and normalize one show folder with blocking filesystem work off-loop."""
        try:
            show_dir = await asyncio.to_thread(self._repair_artifact_show_folder, root, show_dir)
            await asyncio.to_thread(SeasonFolderLayout.repair_duplicate_season_folders, show_dir)
            summary = await asyncio.to_thread(self._collect_show_files, show_dir)
            await self._enrich_scanned_file_stream_metadata(summary)
        except OSError as e:
            logger.warning(f"[TvShowCategory] Skipping unreadable show dir '{show_dir}': {e}")
            return None

        if summary["file_count"] <= 0:
            return None

        try:
            display_name = self._canonical_scanned_show_name(show_dir.name, existing_keys)
            detected_languages = list(summary.get("detected_languages") or [])
            detected_language = ", ".join(detected_languages) if detected_languages else await self.detect_language(show_dir.name, None)
            return ScannedItem(
                name=display_name,
                category_id=self.category_id,
                resolutions=sorted(summary["resolutions"]),
                codecs=sorted(summary["codecs"]),
                episodes=summary["episodes"],
                detailed_episodes=summary["detailed"],
                seasons=len(summary["episodes"]),
                file_count=summary["file_count"],
                total_size_bytes=summary["total_size"],
                detected_language=detected_language,
                detected_languages=detected_languages,
                subtitle_languages=list(summary.get("subtitle_languages") or []),
                year=extract_release_year(show_dir.name),
            )
        except OSError as e:
            logger.warning(f"[TvShowCategory] Failed to finalize scan for '{show_dir.name}': {e}")
            return None

    @staticmethod
    def _list_show_dirs(root: Path) -> list[Path]:
        """List top-level show directories in a worker thread."""
        if not root.is_dir():
            return []
        return sorted(
            path for path in root.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        )

    def _collect_show_files(self, show_dir: Path) -> dict:
        """Collect episode file facts with blocking filesystem calls off-loop."""
        resolutions: set[str] = set()
        codecs: set[str] = set()
        episodes: dict[int, list[int]] = {}
        detailed: list[ScannedFileObservation] = []
        file_count = 0
        total_size = 0
        first_file: Path | None = None
        skipped_without_episode = 0
        skipped_without_episode_examples: list[str] = []

        def get_all_files(path: Path) -> list[Path]:
            """Return all regular files below a candidate season/show folder."""
            results: list[Path] = []
            try:
                for child in path.iterdir():
                    if child.name.startswith("."):
                        continue
                    if child.is_file():
                        if child.suffix.lower() not in (".mkv", ".mp4", ".avi", ".m4v", ".mov", ".mpg", ".mpeg", ".wmv"):
                            continue
                        results.append(child)
                    elif child.is_dir():
                        results.extend(get_all_files(child))
            except OSError as e:
                logger.warning(f"[TvShowCategory] In show '{show_dir.name}', skipping unreadable path '{path}': {e}")
            return results

        for f in get_all_files(show_dir):
            try:
                sz = f.stat().st_size
                parsed = self.parse_name(f.name)
                season_num = parsed.season
                ep_num = parsed.episode

                if season_num is None or ep_num is None:
                    inferred_season, inferred_episode = self._infer_episode_coordinates_from_path(f, show_dir)
                    season_num = season_num if season_num is not None else inferred_season
                    ep_num = ep_num if ep_num is not None else inferred_episode

                if season_num is None:
                    season_num = 1
                if ep_num is None:
                    skipped_without_episode += 1
                    if len(skipped_without_episode_examples) < 3:
                        skipped_without_episode_examples.append(str(f))
                    continue

                file_count += 1
                total_size += sz
                if first_file is None:
                    first_file = f

                episodes.setdefault(season_num, []).append(ep_num)
                quality = self._extract_quality(f.name)
                detailed.append(ScannedFileObservation(
                    season=season_num,
                    episode=ep_num,
                    file_path=str(f),
                    quality=quality,
                    size_bytes=sz,
                ))

                lower = f.name.lower()
                for res in ("2160p", "1080p", "720p", "480p", "4k"):
                    if res in lower:
                        resolutions.add(res)
                for codec in ("x264", "h264", "x265", "h265", "hevc", "xvid", "av1"):
                    if codec in lower:
                        codecs.add(codec)
            except OSError as e:
                logger.warning(f"[TvShowCategory] Skipping unreadable episode file or attributes: {e}")
                continue

        if skipped_without_episode:
            logger.debug(
                "[TvShowCategory] Skipped videos without episode coordinates while scanning show: "
                f"show={show_dir.name!r} count={skipped_without_episode} "
                f"examples={skipped_without_episode_examples}"
            )

        for season, values in list(episodes.items()):
            episodes[season] = sorted(set(values))

        return {
            "resolutions": resolutions,
            "codecs": codecs,
            "episodes": episodes,
            "detailed": detailed,
            "file_count": file_count,
            "total_size": total_size,
            "first_file": first_file,
            "detected_languages": [],
            "subtitle_languages": [],
        }

    async def _enrich_scanned_file_stream_metadata(self, summary: dict[str, Any]) -> None:
        """Attach serialized media-probe stream facts to scanned file observations.

        This deliberately probes files sequentially through the shared media probe
        service.  It restores real audio/subtitle language detection without
        launching a burst of concurrent disk reads during fresh library scans.
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


    def _infer_episode_coordinates_from_path(self, file_path: Path, show_dir: Path) -> tuple[int | None, int | None]:
        """Infer TV season/episode coordinates from folder layout and filename.

        Fresh library scans often see already-organized shows where the folder
        carries the season (``Season 1``, ``S01``, ``Stagione 1``) and files are
        named only ``01.mkv`` or ``Episode 01.mkv``.  The canonical unit builder
        needs those files or the UI/suggestions think the show is empty.  This
        remains TV-owned parsing; generic scan persistence still receives only
        category-provided unit envelopes.
        """
        season_num: int | None = None
        episode_num: int | None = None

        # First try broad TV filename tokens, including the historical E01-only
        # branch that was compiled but no longer used by the Round 77/78 path.
        cleaned_name = file_path.stem.replace('.', ' ').replace('_', ' ')
        match = _EPISODE_FILE.search(cleaned_name)
        if match:
            if match.group(1) and match.group(2):
                season_num = self._safe_positive_int(match.group(1))
                episode_num = self._safe_positive_int(match.group(2))
            elif match.group(3) and match.group(4):
                season_num = self._safe_positive_int(match.group(3))
                episode_num = self._safe_positive_int(match.group(4))
            elif match.group(5):
                episode_num = self._safe_positive_int(match.group(5))

        # Older DVD-era libraries commonly use tokens such as ``s1.08`` or
        # ``s1 08`` rather than S01E08 / 1x08.  Round 80 recovered minimal
        # season-folder layouts, but this legacy inline form still appeared in
        # the user's logs as a skipped real episode.  Keep the check narrow so
        # quality tags like ``S1 720p`` do not become episode 720.
        if season_num is None or episode_num is None:
            sm = re.search(r"\b[Ss]\s*0*(\d{1,3})[\.\s_-]+0*(\d{1,3})(?!\d|p\b)", cleaned_name)
            if sm:
                parsed_season = self._safe_positive_int(sm.group(1))
                parsed_episode = self._safe_positive_int(sm.group(2))
                if parsed_episode is not None and parsed_episode <= 200:
                    season_num = season_num if season_num is not None else parsed_season
                    episode_num = episode_num if episode_num is not None else parsed_episode

        # Walk up to the show directory and let folder names provide the season.
        curr = file_path.parent
        while curr != show_dir and curr != show_dir.parent:
            sm = _SEASON_DIR.search(curr.name)
            if sm and season_num is None:
                season_num = self._safe_positive_int(sm.group(1))
            curr = curr.parent

        # If the season folder is known, accept common minimal episode filenames:
        # ``01``, ``01 - title``, ``Episode 01``, ``Ep. 01``. Avoid quality/year
        # tokens by requiring the number to appear at the start or after an
        # explicit episode word.
        if episode_num is None and season_num is not None:
            minimal_patterns = (
                r'^(?:episode|episodio|ep|e)\s*[\._ -]*0*(\d{1,3})(?:\b|$)',
                r'^0*(\d{1,3})(?:\b|[\._ -])',
            )
            for pattern in minimal_patterns:
                mm = re.search(pattern, cleaned_name, re.IGNORECASE)
                if mm:
                    candidate = self._safe_positive_int(mm.group(1))
                    # Episode numbers above 200 are almost certainly years or
                    # release artefacts, not local TV episode coordinates.
                    if candidate is not None and candidate <= 200:
                        episode_num = candidate
                        break

        return season_num, episode_num



    @staticmethod
    def _repair_artifact_show_folder(root: Path, show_dir: Path) -> Path:
        """Repair folders created from missing-template artefacts, e.g. ``(None)``.

        The operation is intentionally conservative and stays inside the TV root.
        If the clean target exists, children are moved only when they do not
        already exist at the destination; otherwise the old folder remains for
        manual inspection.
        """
        cleaned = clean_display_title(show_dir.name)
        if not cleaned or cleaned == show_dir.name:
            return show_dir
        target = root / cleaned
        try:
            if not target.exists():
                show_dir.rename(target)
                logger.info(f"Repaired TV library folder name: {show_dir.name} -> {target.name}")
                return target
            if not target.is_dir():
                return show_dir
            moved_any = False
            for child in list(show_dir.iterdir()):
                destination = target / child.name
                if destination.exists():
                    continue
                child.rename(destination)
                moved_any = True
            try:
                show_dir.rmdir()
            except OSError:
                pass
            if moved_any:
                logger.info(f"Merged TV library artifact folder {show_dir.name} into {target.name}")
            return target
        except Exception as exc:
            logger.warning(f"Could not repair TV library folder {show_dir}: {exc}")
            return show_dir

    @staticmethod
    def _canonical_scanned_show_name(raw_name: str, existing_keys: set[str] | None) -> str:
        """Return the clean display name for a scanned TV folder.

        Folder names copied from torrent releases can be dirty, e.g.
        ``Silicon.Valley.S01-06.ITA.DLMUX.x264-mkeagle3``. Keep an
        already-tracked canonical key when one matches loosely, otherwise strip
        season/range/quality/language/group markers before the item is saved or
        sent to metadata providers.
        """
        cleaned = clean_release_title(raw_name, fallback=clean_display_title(raw_name), media_hint="tv")
        if existing_keys:
            clean_key = canonical_item_key(cleaned)
            raw_key = canonical_item_key(raw_name)
            for key in existing_keys:
                existing_key = canonical_item_key(key)
                if existing_key in {clean_key, raw_key}:
                    return str(key)
        return cleaned


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

    def infer_quality(self, item: ScannedItem, profile: "QualityProfile") -> "QualityProfile":
        """Execute the public TvShowCategory.infer_quality behavior.

        This method is a supported extension point for callers outside the
        class.  Keep its input/output contract stable and move specialized
        logic into collaborators or protected helpers as the feature grows.
        """
        from src.core.models import QualityProfile, ScannedLibraryItem, ScannedMediaFile
        from src.core.smart_quality import SmartQualityInferrer

        inferrer = SmartQualityInferrer()
        scanned_item = ScannedLibraryItem(
            name=item.name,
            category_id=item.category_id,
            files=[
                ScannedMediaFile(
                    season=episode.season,
                    episode=episode.episode,
                    file_path=episode.file_path,
                    quality=episode.quality,
                    size_bytes=episode.size_bytes,
                )
                for episode in item.detailed_episodes
            ],
            file_count=item.file_count,
            total_size_bytes=item.total_size_bytes,
            avg_file_size_mb=round(item.total_size_bytes / item.file_count / 1024 / 1024, 1)
            if item.file_count else 0,
            codecs=item.codecs,
            resolutions=item.resolutions,
        )
        return inferrer.infer_for_item(scanned_item)

    def delete(self, name: str, settings: "Settings", season: int | None = None,
               episode: int | None = None, year: int | None = None) -> bool:
        """Delete a specific episode from the TV library."""
        if season is None or episode is None:
            return False
        root = Path(self.get_root_path(settings))
        if not root.exists():
            return False
        show_dir = root / name
        season_dir = SeasonFolderLayout.preferred_season_dir(show_dir, int(season), proposed_dir=show_dir / f"Season {int(season):02d}")
        if not season_dir.exists():
            return False
        pattern = f"S{season:02d}E{episode:02d}"
        resolver = SafePathResolver.for_category(self, settings)
        deleted = False
        for f in season_dir.iterdir():
            if pattern in f.name:
                try:
                    resolver.safe_unlink(f, purpose="tv.delete_unit", move_to_trash=True)
                    deleted = True
                except SecurityPolicyError as exc:
                    logger.warning(f"TV delete blocked unsafe path: {exc}")
        return deleted

    def format_progress(self, progress: dict | None) -> str:
        """Format data for the progress surface.

        Return presentation-ready text or values without mutating domain
        objects.  Keep formatting stable because chat, UI, and tests may rely
        on the resulting shape.
        """
        if not progress:
            return "—"
        s = progress.get("last_season")
        e = progress.get("last_episode")
        if s is not None and e is not None:
            return f"S{int(s):02d}E{int(e):02d}"
        return "—"


