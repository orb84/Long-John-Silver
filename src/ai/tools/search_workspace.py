"""Search result workspace policies for agent-facing media torrent searches.

This module owns the candidate workspace projection used by
``search_media_torrents``.  It intentionally contains no category-specific
parsing; category semantics arrive through descriptors, category hooks, and
category-provided candidate annotations.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from src.core.categories.language import LanguageTokenPolicy
from src.core.categories.search_scope import SearchScopePolicy


class SearchWorkspaceNumbers:
    """Small numeric coercion helpers used by workspace policies."""

    @staticmethod
    def safe_int(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def safe_float(value: Any) -> float:
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0


class SearchWorkspaceFormatter:
    """Format cached search candidates into compact LLM-facing rows."""

    @staticmethod
    def format_size(size_bytes: int | None) -> str | None:
        if not size_bytes:
            return None
        units = [(1024 ** 3, "GB"), (1024 ** 2, "MB"), (1024, "KB")]
        for factor, suffix in units:
            if size_bytes >= factor:
                return f"{size_bytes / factor:.2f} {suffix}"
        return f"{size_bytes} B"

    @classmethod
    def compact_soulseek_candidates(
        cls,
        candidates: list[dict[str, Any]],
        *,
        result_set_id: str = "",
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        compact: list[dict[str, Any]] = []
        for raw in (candidates or [])[:limit]:
            if not isinstance(raw, dict):
                continue
            filenames = [str(v) for v in (raw.get("filenames") or []) if str(v).strip()]
            audio = [str(v) for v in (raw.get("audio_filenames") or []) if str(v).strip()]
            support = [str(v) for v in (raw.get("supporting_filenames") or []) if str(v).strip()]
            row = {
                "index": raw.get("index"),
                "candidate_id": raw.get("candidate_id"),
                "result_set_id": result_set_id,
                "source": "slskd",
                "candidate_type": raw.get("candidate_type") or ("folder" if filenames else "file"),
                "username": raw.get("username"),
                "folder": raw.get("folder"),
                "filename": raw.get("filename"),
                "file_count": raw.get("file_count") or (len(filenames) if filenames else None),
                "audio_file_count": raw.get("audio_file_count") or (len(audio) if audio else None),
                "supporting_file_count": raw.get("supporting_file_count") or (len(support) if support else None),
                "size_bytes": raw.get("size_bytes"),
                "size": cls.format_size(raw.get("size_bytes") if isinstance(raw.get("size_bytes"), int) else None),
                "bitrate": raw.get("bitrate"),
                "extension": raw.get("extension"),
                "has_free_upload_slot": raw.get("has_free_upload_slot"),
                "queue_length": raw.get("queue_length"),
                "folder_relevance": raw.get("folder_relevance"),
                "folder_query_match_score": raw.get("folder_query_match_score"),
                "sample_filenames": (audio or filenames or ([raw.get("filename")] if raw.get("filename") else []))[:6],
                "enqueue_hint": {
                    "tool": "enqueue_soulseek_download",
                    "candidate_id": raw.get("candidate_id"),
                    "result_set_id": result_set_id,
                },
            }
            compact.append({k: v for k, v in row.items() if v not in (None, "", [], {})})
        return compact

    @staticmethod
    def best_soulseek_candidate_id(candidates: list[dict[str, Any]]) -> str:
        if not candidates:
            return ""
        folders = [c for c in candidates if isinstance(c, dict) and c.get("candidate_type") == "folder"]
        strong = [c for c in folders if str(c.get("folder_relevance") or "").lower() in {"strong", "partial"}]
        chosen = (strong or folders or candidates)[0]
        return str(chosen.get("candidate_id") or "")

    @classmethod
    def candidate_picker_rows(cls, candidates: list[dict[str, Any]], limit: int = 60) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for c in candidates[: max(0, int(limit))]:
            candidate_id = c.get("candidate_id")
            row = {
                "id": candidate_id,
                "candidate_id": candidate_id,
                "index": c.get("index"),
                "title": c.get("title"),
                "size": c.get("size"),
                "size_bytes": c.get("size_bytes"),
                "seeders": c.get("seeders"),
            }
            optional_keys = (
                "languages",
                "resolution",
                "per_episode_size",
                "estimated_bitrate_kbps",
                "bitrate_basis",
                "expected_episode_count",
                "requested_bundle_coverage",
                "requested_season_coverage",
                "coverage_note",
                "source",
            )
            for key in optional_keys:
                if c.get(key):
                    row[key] = c.get(key)
            if c.get("llm_recommended"):
                row["llm_recommended"] = True
            if c.get("selection_warnings"):
                row["selection_warnings"] = c.get("selection_warnings")[:3]
            if c.get("selection_blockers"):
                row["selection_blockers"] = c.get("selection_blockers")[:3]
            if c.get("auto_queue_allowed") is False:
                row["auto_queue_allowed"] = False
                row["blocked_reason"] = c.get("auto_queue_blocked_reason")
            if c.get("is_bundle"):
                row.update({
                    "is_bundle": True,
                    "bundle_scope": c.get("bundle_scope"),
                    "pack_type": c.get("pack_type"),
                    "bundle_unit_count": c.get("bundle_unit_count"),
                })
            descriptor = c.get("unit_descriptor") or {}
            if descriptor:
                row["unit"] = descriptor.get("label") or descriptor.get("stable_key")
            rows.append({k: v for k, v in row.items() if v not in (None, "", [], {})})
        return rows


class SearchArgumentConstraints:
    """Extract structured quality/size constraints from tool arguments."""

    @staticmethod
    def from_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
        constraints: dict[str, Any] = {}
        for key in ("target_size_gb", "max_size_gb", "min_size_gb", "current_size_gb"):
            value = arguments.get(key)
            if value in (None, ""):
                continue
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                continue
            if parsed > 0:
                constraints[key] = parsed
        for key in ("target_bitrate_kbps", "preferred_bitrate_kbps", "max_bitrate_kbps", "current_bitrate_kbps"):
            value = arguments.get(key)
            if value in (None, ""):
                continue
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                continue
            if parsed > 0:
                constraints[key] = parsed
        for key in ("preferred_resolution", "required_resolution"):
            value = str(arguments.get(key) or "").strip()
            if value:
                constraints[key] = value
        for key in ("smaller_than_current", "preserve_resolution"):
            if arguments.get(key) is not None:
                constraints[key] = bool(arguments.get(key))
        if constraints.get("smaller_than_current") and not constraints.get("size_mode"):
            constraints["size_mode"] = "smaller"
        if constraints and "preserve_resolution" not in constraints:
            constraints["preserve_resolution"] = True
        return constraints


class CandidateBundlePolicy:
    """Interpret category-published bundle descriptors without category parsing."""

    _BUNDLE_GRANULARITIES = {"bundle", "season", "album", "volume", "collection", "series"}

    @staticmethod
    def requested_bundle_coverage(candidate: dict[str, Any]) -> str:
        return str(candidate.get("requested_bundle_coverage") or candidate.get("requested_season_coverage") or "")

    @classmethod
    def covers_full_requested_bundle(cls, candidate: dict[str, Any]) -> bool:
        coverage = cls.requested_bundle_coverage(candidate)
        return not coverage or coverage in {"full_requested_bundle", "full_requested_season"}

    @classmethod
    def is_bundle(cls, candidate: dict[str, Any]) -> bool:
        granularity = str((candidate.get("unit_descriptor") or {}).get("granularity") or "").lower()
        return bool(
            candidate.get("is_bundle")
            or candidate.get("bundle_scope")
            or candidate.get("pack_type")
            or granularity in cls._BUNDLE_GRANULARITIES
        )

    @staticmethod
    def logical_unit_key(candidate: dict[str, Any]) -> str:
        descriptor = candidate.get("unit_descriptor") if isinstance(candidate.get("unit_descriptor"), dict) else {}
        for key in ("stable_key", "sort_key", "label"):
            value = descriptor.get(key)
            if isinstance(value, (list, tuple)):
                value = ":".join(str(v) for v in value if str(v).strip())
            stable = str(value or "").strip()
            if stable:
                return stable
        return ""

    @classmethod
    def quality_scope_key(cls, candidate: dict[str, Any]) -> tuple[str, str]:
        if cls.is_bundle(candidate):
            return ("bundle", cls.logical_unit_key(candidate))
        return ("unit", cls.logical_unit_key(candidate))


class SelectionPolicyAnnotator:
    """Mark candidate warnings/blockers before LLM review and queueing."""

    @classmethod
    def annotate(
        cls,
        candidates: list[dict[str, Any]],
        *,
        preferred_language: str | None = None,
        language_is_explicit: bool = False,
    ) -> None:
        preferred = LanguageTokenPolicy.canonical_token(preferred_language) if preferred_language else ""
        for candidate in candidates:
            warnings: list[str] = list(candidate.get("selection_warnings") or [])
            blockers: list[str] = list(candidate.get("selection_blockers") or [])
            seeders = SearchWorkspaceNumbers.safe_int(candidate.get("seeders"))
            candidate["availability_seeders"] = seeders
            if seeders <= 0:
                blockers.append("no seeder count reported")
            elif seeders < 5:
                blockers.append(f"very low seeders ({seeders})")
            elif seeders < 10:
                warnings.append(f"low seeders ({seeders})")

            if preferred:
                cls._annotate_language(candidate, preferred, preferred_language, language_is_explicit, warnings, blockers)
            elif "language_preference_status" not in candidate:
                candidate["language_preference_status"] = "not_applicable"

            bundle_context = candidate.get("bundle_context") or {}
            if isinstance(bundle_context, dict) and bundle_context.get("selective_download_required"):
                reason = str(bundle_context.get("selective_download_reason") or bundle_context.get("inspection_required_reason") or "contains extra category units; inspect/select only the requested files before queueing")
                warnings.append(reason)
                blockers.append("requires selective file inspection before queueing")

            candidate["selection_warnings"] = warnings
            candidate["selection_blockers"] = blockers
            if blockers:
                candidate["auto_queue_allowed"] = False
                candidate["auto_queue_blocked_reason"] = "; ".join(blockers)
            else:
                candidate["auto_queue_allowed"] = True
                candidate["auto_queue_blocked_reason"] = ""

    @staticmethod
    def _annotate_language(
        candidate: dict[str, Any],
        preferred: str,
        preferred_language: str | None,
        language_is_explicit: bool,
        warnings: list[str],
        blockers: list[str],
    ) -> None:
        languages = candidate.get("languages") or []
        if isinstance(languages, str):
            languages = [languages]
        normalized = LanguageTokenPolicy.canonical_tokens(languages)
        title = str(candidate.get("title") or "")
        title_has_preferred = LanguageTokenPolicy.title_has_language_token(title, preferred)
        multi = "multi" in normalized or LanguageTokenPolicy.title_has_multi_language_signal(title)
        if normalized and preferred not in normalized and not multi and not title_has_preferred:
            candidate["language_preference_status"] = "mismatch"
            blockers.append(f"does not advertise preferred media language {preferred_language}")
        elif preferred == "english" and normalized and preferred in normalized:
            extras = {lang for lang in normalized if lang not in {preferred, "multi"}}
            if extras or multi:
                candidate["language_preference_status"] = "preferred_with_extra_audio"
                warnings.append(
                    "advertises extra non-preferred audio languages; keep as fallback behind preferred-only or unknown-language candidates"
                )
            else:
                candidate["language_preference_status"] = "preferred_only"
        elif not normalized and not title_has_preferred:
            candidate["language_preference_status"] = "unknown_acceptable"
            message = f"language not advertised; preferred media language is {preferred_language}"
            if language_is_explicit and preferred != "english":
                blockers.append(message)
            else:
                warnings.append(message)
        elif title_has_preferred:
            candidate["language_preference_status"] = "preferred_by_title"
        elif multi:
            candidate["language_preference_status"] = "multi_language_fallback"


class SearchQualityChoicePolicy:
    """Detect and summarize material quality/size tradeoffs."""

    @classmethod
    def evaluate(cls, candidates: list[dict[str, Any]], constraints: dict[str, Any] | None = None) -> dict[str, Any]:
        constraints = constraints or {}
        if any(constraints.get(k) for k in ("target_bitrate_kbps", "preferred_bitrate_kbps", "max_bitrate_kbps", "current_bitrate_kbps")):
            return {"requires_user_choice": False, "reason": "bitrate preference already supplied"}
        if any(constraints.get(k) for k in ("target_size_gb", "max_size_gb", "min_size_gb", "current_size_gb")):
            return {"requires_user_choice": False, "reason": "size preference already supplied"}

        viable = [c for c in candidates if c.get("auto_queue_allowed") is not False and c.get("estimated_bitrate_kbps") and c.get("resolution")]
        explicit_resolution = str(constraints.get("required_resolution") or constraints.get("preferred_resolution") or "").strip().lower()
        if explicit_resolution:
            viable = [c for c in viable if str(c.get("resolution") or "").strip().lower() == explicit_resolution]
        if len(viable) < 2:
            return {"requires_user_choice": False}

        bundle_group = [c for c in viable if CandidateBundlePolicy.is_bundle(c) and CandidateBundlePolicy.covers_full_requested_bundle(c)]
        bundle_policy = cls._material_policy(
            bundle_group,
            reason="no_saved_bundle_quality_preference",
            message=(
                "Multiple matching bundle candidates differ materially in resolution/codec/bitrate/size; "
                "present the quality options instead of auto-queueing a compact bundle as the default."
            ),
            tradeoff_type="bundle_quality_tradeoff",
        )
        if bundle_policy.get("requires_user_choice"):
            return bundle_policy

        resolution = viable[0].get("resolution")
        group = [c for c in viable if c.get("resolution") == resolution]
        group = cls._same_scope_group(group)
        return cls._material_policy(
            group[:6],
            reason="no_saved_bitrate_preference",
            message=(
                "Multiple same-resolution candidates differ materially in bitrate/size; ask the user which "
                "quality-size tradeoff to use for this item, then store that bitrate preference when they choose."
            ),
            tradeoff_type="same_resolution_bitrate_tradeoff",
        )

    @classmethod
    def batch_candidate_score(
        cls,
        candidate: dict[str, Any],
        preferred_language: str | None = None,
        *,
        language_relevant: bool = True,
        use_global_quality_profile: bool = True,
    ) -> tuple:
        if language_relevant:
            languages = candidate.get("languages") or []
            normalized_languages = LanguageTokenPolicy.canonical_tokens(languages)
            preferred = LanguageTokenPolicy.canonical_token(preferred_language) if preferred_language else ""
            title = str(candidate.get("title") or "")
            title_has_multi = LanguageTokenPolicy.title_has_multi_language_signal(title)
            if preferred and preferred in normalized_languages:
                extras = {lang for lang in normalized_languages if lang not in {preferred, "multi"}}
                if preferred == "english" and (extras or "multi" in normalized_languages or title_has_multi):
                    lang_score = 1
                else:
                    lang_score = 3
            elif "multi" in normalized_languages or title_has_multi:
                lang_score = 2
            elif not normalized_languages:
                lang_score = 1
            else:
                lang_score = 0
        else:
            lang_score = 0

        if use_global_quality_profile:
            resolution = str(candidate.get("resolution") or "").lower()
            if "2160" in resolution or "4k" in resolution:
                resolution_score = 1
            elif "1080" in resolution:
                resolution_score = 4
            elif "720" in resolution:
                resolution_score = 3
            elif resolution:
                resolution_score = 2
            else:
                resolution_score = 0
            codec = str(candidate.get("codec") or "").lower()
            codec_score = 1 if codec in {"h265", "x265", "hevc", "av1", "h264", "x264"} else 0
        else:
            resolution_score = 0
            codec_score = 0

        seeders = SearchWorkspaceNumbers.safe_int(candidate.get("seeders"))
        quality_score = SearchWorkspaceNumbers.safe_float(candidate.get("quality_score"))
        size_bytes = SearchWorkspaceNumbers.safe_int(candidate.get("per_episode_size_bytes") or candidate.get("size_bytes"))
        size_tie = -size_bytes if size_bytes > 0 else 0
        return (lang_score, resolution_score, seeders, quality_score, codec_score, size_tie)

    @staticmethod
    def selected_candidate_ids_for_estimate(
        candidates: list[dict[str, Any]],
        *,
        batch_recommendation: dict[str, Any] | None,
        search_scope: str | None,
    ) -> list[str]:
        if batch_recommendation and batch_recommendation.get("candidate_ids"):
            return [str(cid) for cid in batch_recommendation.get("candidate_ids") or [] if cid]
        if SearchScopePolicy.is_bundle_scope(search_scope):
            for c in candidates:
                if c.get("is_bundle") and c.get("candidate_id"):
                    return [str(c.get("candidate_id"))]
        if candidates and candidates[0].get("candidate_id"):
            return [str(candidates[0].get("candidate_id"))]
        return []

    @staticmethod
    def estimated_total_size_bytes(candidates: list[dict[str, Any]], selected_ids: list[str]) -> int:
        if not selected_ids:
            return 0
        wanted = {str(cid) for cid in selected_ids}
        total = 0
        for c in candidates:
            if str(c.get("candidate_id")) not in wanted:
                continue
            try:
                bundle_context = c.get("bundle_context") or {}
                if isinstance(bundle_context, dict) and bundle_context.get("selective_download_required") and c.get("per_episode_size_bytes"):
                    count = SearchWorkspaceNumbers.safe_int(bundle_context.get("selected_unit_episode_count_hint")) or 10
                    total += int(c.get("per_episode_size_bytes") or 0) * max(1, count)
                else:
                    total += int(c.get("size_bytes") or 0)
            except (TypeError, ValueError):
                pass
        return total

    @classmethod
    def _same_scope_group(cls, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not candidates:
            return []
        first_key = CandidateBundlePolicy.quality_scope_key(candidates[0])
        if not first_key[1]:
            return candidates
        return [candidate for candidate in candidates if CandidateBundlePolicy.quality_scope_key(candidate) == first_key]

    @classmethod
    def _material_policy(cls, candidates: list[dict[str, Any]], *, reason: str, message: str, tradeoff_type: str) -> dict[str, Any]:
        candidates = cls._collapse_equivalent_quality_options(candidates)
        if len(candidates) < 2:
            return {"requires_user_choice": False}
        bitrates: list[float] = []
        sizes: list[float] = []
        resolutions: set[str] = set()
        codecs: set[str] = set()
        for c in candidates:
            try:
                bitrate = float(c.get("estimated_bitrate_kbps") or 0)
                if bitrate > 0:
                    bitrates.append(bitrate)
            except (TypeError, ValueError):
                pass
            try:
                size = float(c.get("size_bytes") or 0)
                if size > 0:
                    sizes.append(size)
            except (TypeError, ValueError):
                pass
            if c.get("resolution"):
                resolutions.add(str(c.get("resolution")))
            if c.get("codec"):
                codecs.add(str(c.get("codec")))
        if len(bitrates) < 2 and len(sizes) < 2 and len(resolutions) < 2:
            return {"requires_user_choice": False}
        bitrate_material = len(bitrates) >= 2 and max(bitrates) >= min(bitrates) * 1.25
        size_material = len(sizes) >= 2 and max(sizes) >= min(sizes) * 1.35
        resolution_material = len(resolutions) >= 2
        codec_material = len(codecs) >= 2 and (bitrate_material or size_material)
        if not (bitrate_material or size_material or resolution_material or codec_material):
            return {"requires_user_choice": False}

        def sort_key(row: dict[str, Any]) -> tuple[int, int, int, float, int]:
            try:
                bitrate = float(row.get("estimated_bitrate_kbps") or 0)
            except (TypeError, ValueError):
                bitrate = 0.0
            return (
                -cls._language_preference_rank(row),
                -cls._availability_bucket(row),
                -SearchWorkspaceNumbers.safe_int(row.get("seeders")),
                -bitrate,
                SearchWorkspaceNumbers.safe_int(row.get("index")) or 9999,
            )

        choices = []
        for c in sorted(candidates, key=sort_key)[:8]:
            choices.append({
                "candidate_id": c.get("candidate_id"),
                "title": c.get("title"),
                "resolution": c.get("resolution"),
                "codec": c.get("codec"),
                "size": c.get("size"),
                "size_bytes": c.get("size_bytes"),
                "per_episode_size": c.get("per_episode_size"),
                "per_episode_size_mb": c.get("per_episode_size_mb"),
                "estimated_bitrate_kbps": c.get("estimated_bitrate_kbps"),
                "seeders": c.get("seeders"),
                "languages": c.get("languages"),
                "language_preference_status": c.get("language_preference_status"),
                "selection_warnings": c.get("selection_warnings") or [],
                "requested_bundle_coverage": CandidateBundlePolicy.requested_bundle_coverage(c),
                "requested_season_coverage": c.get("requested_season_coverage"),
            })
        return {
            "requires_user_choice": True,
            "reason": reason,
            "tradeoff_type": tradeoff_type,
            "message": message,
            "candidate_ids": [c.get("candidate_id") for c in choices if c.get("candidate_id")],
            "choices": choices,
            "comparison": {
                "min_bitrate_kbps": min(bitrates) if bitrates else None,
                "max_bitrate_kbps": max(bitrates) if bitrates else None,
                "min_size_bytes": min(sizes) if sizes else None,
                "max_size_bytes": max(sizes) if sizes else None,
                "resolutions": sorted(resolutions),
                "codecs": sorted(codecs),
            },
        }

    @classmethod
    def _collapse_equivalent_quality_options(cls, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
        ordered_keys: list[tuple[Any, ...]] = []
        for candidate in candidates:
            key = cls._quality_equivalence_key(candidate)
            existing = grouped.get(key)
            if existing is None:
                grouped[key] = candidate
                ordered_keys.append(key)
                continue
            if cls._quality_option_health_key(candidate) > cls._quality_option_health_key(existing):
                grouped[key] = candidate
        return [grouped[key] for key in ordered_keys if key in grouped]

    @staticmethod
    def _quality_equivalence_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
        unit = candidate.get("unit_descriptor") or {}
        languages = tuple(sorted(str(lang).lower() for lang in (candidate.get("languages") or []) if lang))
        size_bucket = None
        try:
            size = int(candidate.get("size_bytes") or 0)
            if size > 0:
                size_bucket = round(size / max(size * 0 + 128 * 1024 * 1024, 1))
        except Exception:
            size_bucket = None
        bitrate_bucket = None
        try:
            bitrate = float(candidate.get("estimated_bitrate_kbps") or 0)
            if bitrate > 0:
                bitrate_bucket = round(bitrate / 200.0)
        except Exception:
            bitrate_bucket = None
        return (
            CandidateBundlePolicy.logical_unit_key(candidate),
            str(candidate.get("resolution") or "").lower(),
            str(candidate.get("codec") or "").lower(),
            tuple(languages),
            CandidateBundlePolicy.requested_bundle_coverage(candidate),
            int(candidate.get("bundle_unit_count") or 0),
            size_bucket,
            bitrate_bucket,
        )

    @staticmethod
    def _quality_option_health_key(candidate: dict[str, Any]) -> tuple[int, int, int]:
        seeders = SearchWorkspaceNumbers.safe_int(candidate.get("seeders"))
        has_seeders = 1 if candidate.get("seeders") is not None else 0
        index = SearchWorkspaceNumbers.safe_int(candidate.get("index")) or 9999
        return (has_seeders, seeders, -index)

    @staticmethod
    def _language_preference_rank(candidate: dict[str, Any]) -> int:
        status = str(candidate.get("language_preference_status") or "").lower()
        return {
            "preferred_only": 5,
            "preferred_by_title": 5,
            "unknown_acceptable": 4,
            "preferred_with_extra_audio": 3,
            "multi_language_fallback": 2,
            "not_applicable": 1,
            "mismatch": -100,
        }.get(status, 1)

    @staticmethod
    def _availability_bucket(candidate: dict[str, Any]) -> int:
        seeders = SearchWorkspaceNumbers.safe_int(candidate.get("seeders"))
        if seeders >= 100:
            return 5
        if seeders >= 30:
            return 4
        if seeders >= 10:
            return 3
        if seeders >= 5:
            return 2
        if seeders > 0:
            return 1
        return 0


class SearchBatchRecommendationBuilder:
    """Build deterministic multi-unit recommendations from category groups."""

    @classmethod
    def should_suppress(
        cls,
        *,
        batch_recommendation: dict[str, Any] | None,
        candidates: list[dict[str, Any]],
        llm_candidate_review: dict[str, Any] | None,
        quality_choice_policy: dict[str, Any] | None,
    ) -> bool:
        if not batch_recommendation:
            return False
        if quality_choice_policy and quality_choice_policy.get("requires_user_choice"):
            return True
        recommended = {str(cid) for cid in ((llm_candidate_review or {}).get("recommended_candidate_ids") or []) if cid}
        if not recommended:
            return False
        by_id = {str(c.get("candidate_id") or ""): c for c in candidates}
        for candidate_id in recommended:
            candidate = by_id.get(candidate_id) or {}
            if CandidateBundlePolicy.is_bundle(candidate) and CandidateBundlePolicy.covers_full_requested_bundle(candidate):
                return True
        return False

    @classmethod
    def build(
        cls,
        *,
        name: str,
        category_id: str | None,
        season: int | None,
        episode: int | None,
        search_scope: str | None = None,
        result_set_id: str,
        candidates: list[dict[str, Any]],
        category: object | None = None,
        preferred_language: str | None = None,
    ) -> dict[str, Any] | None:
        if episode is not None:
            return None
        if season is not None and episode is None and not SearchScopePolicy.is_individual_units_only(search_scope):
            for candidate in candidates or []:
                if CandidateBundlePolicy.is_bundle(candidate):
                    return None
        if season is None and SearchScopePolicy.normalize(search_scope) == SearchScopePolicy.DEFAULT:
            return None
        if SearchScopePolicy.is_bundle_scope(search_scope):
            return None
        if not category or not hasattr(category, "batch_group_for_candidate"):
            return None

        unit_groups: dict[str, dict[str, Any]] = {}
        request_context = {"season": season, "episode": episode, "category_id": category_id, "search_scope": search_scope}
        for c in candidates or []:
            group = category.batch_group_for_candidate(c, request_context)
            if not group:
                continue
            key = str(group.get("key") or "")
            if not key:
                continue
            unit_groups.setdefault(key, {"group": group, "candidates": []})["candidates"].append(c)

        if len(unit_groups) <= 1:
            return None

        ordered = sorted(unit_groups.values(), key=lambda data: data["group"].get("sort_key") or [data["group"].get("label") or ""])
        groups: list[dict[str, Any]] = []
        candidate_ids: list[str] = []
        for data in ordered:
            ranked = sorted(
                data["candidates"],
                key=lambda candidate: SearchQualityChoicePolicy.batch_candidate_score(
                    candidate,
                    preferred_language,
                    language_relevant=not category or not hasattr(category, "language_is_search_relevant") or bool(category.language_is_search_relevant()),
                    use_global_quality_profile=not category or not hasattr(category, "uses_global_quality_profile") or bool(category.uses_global_quality_profile()),
                ),
                reverse=True,
            )
            recommended = ranked[0]
            cid = recommended.get("candidate_id")
            if not cid:
                continue
            candidate_ids.append(cid)
            descriptor = data["group"].get("descriptor") or {}
            groups.append({
                "unit": data["group"].get("label") or data["group"].get("key"),
                "unit_descriptor": descriptor,
                "coordinates": descriptor.get("coordinates") or {},
                "recommended_candidate_id": cid,
                "candidate_count": len(ranked),
            })

        if len(candidate_ids) <= 1:
            return None

        queue_args = {
            "name": name,
            "category_id": category_id,
            "result_set_id": result_set_id,
            "candidate_ids": candidate_ids,
        }
        if season is not None:
            queue_args["season"] = season

        return {
            "intent": "multi_unit_download",
            "reason": "Multiple distinct category units have eligible ranked candidates.",
            "auto_expand_single_selection": False,
            "result_set_id": result_set_id,
            "candidate_ids": candidate_ids,
            "groups": groups,
            "queue_download_arguments": queue_args,
        }


class SearchWorkspaceNextActions:
    """Return prompt-safe affordances for a cached torrent result set."""

    @staticmethod
    def build(
        *,
        candidates: list[dict[str, Any]],
        search_scope: str | None,
        result_set_id: str,
        has_batch: bool,
        quality_choice_policy: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        scope = str(search_scope or "").lower()
        actions: list[dict[str, Any]] = []
        if not candidates:
            actions.append({
                "action": "broaden_search",
                "tool": "search_media_torrents",
                "reason": "No usable candidates were returned for this scope.",
                "args_hint": {"search_scope": SearchScopePolicy.BUNDLE_PREFERRED if SearchScopePolicy.is_bundle_only(scope) else SearchScopePolicy.DEFAULT},
            })
            if SearchScopePolicy.is_bundle_scope(scope):
                actions.append({
                    "action": "fallback_to_individual_units",
                    "tool": "search_media_torrents",
                    "reason": "A bundle was preferred but not found; per-unit fallback may be necessary unless the user asked for bundle-only.",
                    "args_hint": {"search_scope": "individual_units_only"},
                })
            return actions

        if quality_choice_policy and quality_choice_policy.get("requires_user_choice"):
            actions.append({
                "action": "ask_user_to_choose_quality_bitrate",
                "tool": None,
                "reason": quality_choice_policy.get("message") or "Multiple viable same-resolution candidates differ materially in bitrate/size and no item bitrate preference is saved yet.",
                "candidate_ids": quality_choice_policy.get("candidate_ids") or [],
            })

        if has_batch:
            actions.append({
                "action": "queue_batch_recommendation",
                "tool": "queue_download",
                "reason": "The category produced one recommended candidate per requested unit.",
                "args_source": "batch_recommendation.queue_download_arguments",
            })

        bundle_candidates = [c for c in candidates if CandidateBundlePolicy.is_bundle(c)]
        if bundle_candidates:
            actions.append({
                "action": "inspect_bundle_files",
                "tool": "inspect_torrent_candidate",
                "reason": "Bundle candidates may contain multiple category units or folders; inspect the file list/summary if coverage is ambiguous before queueing.",
                "args_hint": {"result_set_id": result_set_id, "candidate_id": bundle_candidates[0].get("candidate_id"), "detail": "file_list"},
            })

        first = candidates[0]
        if first.get("auto_queue_allowed") is False:
            actions.append({
                "action": "do_not_auto_queue_top_candidate",
                "tool": None,
                "reason": first.get("auto_queue_blocked_reason") or "The top candidate has selection warnings; ask the user or inspect alternatives before queueing.",
            })
            if SearchScopePolicy.is_bundle_preferred(scope):
                actions.append({
                    "action": "try_individual_units_before_queueing_weak_bundle",
                    "tool": "search_media_torrents",
                    "reason": "A bundle was found, but the best bundle is low-confidence; search individual units before accepting a weak bundle.",
                    "args_hint": {"search_scope": "individual_units_only"},
                })
        else:
            actions.append({
                "action": "queue_clear_candidate",
                "tool": "queue_download",
                "reason": "Use this only when the candidate clearly matches the user's target and constraints.",
                "args_hint": {"result_set_id": result_set_id, "candidate_id": first.get("candidate_id")},
            })
        actions.append({
            "action": "show_or_request_choice",
            "tool": None,
            "reason": "If multiple plausible candidates remain, summarize the best few by candidate_id/title/size/seeders and ask the user to choose.",
        })
        return actions


class SearchWorkspaceAuditLogger:
    """Emit compact, structured audit records for torrent workspaces."""

    @staticmethod
    def log(
        *,
        name: str,
        display_name: str,
        category_id: str | None,
        season: int | None,
        episode: int | None,
        language: str | None,
        search_scope: str | None,
        query: str | None,
        result_set_id: str,
        raw_candidate_count: int,
        clean_candidates: list[dict[str, Any]],
        quality_choice_policy: dict[str, Any] | None,
        llm_candidate_review: dict[str, Any] | None,
        llm_candidate_review_status: str,
        next_actions_preview: list[dict[str, Any]],
    ) -> None:
        recommended_ids = [str(cid) for cid in ((llm_candidate_review or {}).get("recommended_candidate_ids") or []) if cid]
        keep_ids = set(recommended_ids)
        if quality_choice_policy and isinstance(quality_choice_policy.get("candidate_ids"), list):
            keep_ids.update(str(cid) for cid in quality_choice_policy.get("candidate_ids") or [] if cid)
        top_rows = clean_candidates[:30]
        extra_rows = [candidate for candidate in clean_candidates[30:] if str(candidate.get("candidate_id") or "") in keep_ids]
        payload = {
            "event": "search_media_torrents_workspace_audit",
            "name": name,
            "display_name": display_name,
            "category_id": category_id,
            "season": season,
            "episode": episode,
            "language": language,
            "search_scope": search_scope,
            "query_summary": query,
            "result_set_id": result_set_id,
            "counts": {
                "raw_candidates_before_tool_cleaning": raw_candidate_count,
                "clean_candidates": len(clean_candidates),
                "logged_top_candidates": len(top_rows),
                "logged_extra_recommended_or_quality_options": len(extra_rows),
            },
            "quality_choice_policy": quality_choice_policy or {},
            "llm_candidate_review_status": llm_candidate_review_status,
            "llm_candidate_review": {
                "recommended_candidate_ids": recommended_ids,
                "confidence": (llm_candidate_review or {}).get("confidence"),
                "needs_user_choice": (llm_candidate_review or {}).get("needs_user_choice"),
                "should_queue_now": (llm_candidate_review or {}).get("should_queue_now"),
                "reason": (llm_candidate_review or {}).get("reason"),
                "answer_hint": (llm_candidate_review or {}).get("answer_hint"),
                "candidate_count_reviewed": (llm_candidate_review or {}).get("candidate_count_reviewed"),
                "chunk_count": (llm_candidate_review or {}).get("chunk_count"),
                "context_limit_tokens": (llm_candidate_review or {}).get("context_limit_tokens"),
            },
            "next_actions_preview": next_actions_preview,
            "candidates": [SearchWorkspaceAuditLogger._row(candidate) for candidate in [*top_rows, *extra_rows]],
            "omitted_clean_candidates": max(0, len(clean_candidates) - len(top_rows) - len(extra_rows)),
        }
        logger.info("SEARCH_MEDIA_TORRENTS_WORKSPACE_AUDIT " + json.dumps(payload, ensure_ascii=False, default=str))

    @staticmethod
    def _row(candidate: dict[str, Any]) -> dict[str, Any]:
        return {
            "candidate_id": candidate.get("candidate_id"),
            "index": candidate.get("index"),
            "title": candidate.get("title"),
            "source": candidate.get("source"),
            "size": candidate.get("size"),
            "size_bytes": candidate.get("size_bytes"),
            "seeders": candidate.get("seeders"),
            "languages": candidate.get("languages"),
            "resolution": candidate.get("resolution"),
            "codec": candidate.get("codec"),
            "per_episode_size": candidate.get("per_episode_size"),
            "per_episode_size_mb": candidate.get("per_episode_size_mb"),
            "estimated_bitrate_kbps": candidate.get("estimated_bitrate_kbps"),
            "unit_descriptor": candidate.get("unit_descriptor"),
            "is_bundle": candidate.get("is_bundle"),
            "bundle_scope": candidate.get("bundle_scope"),
            "pack_type": candidate.get("pack_type"),
            "requested_bundle_coverage": candidate.get("requested_bundle_coverage"),
            "requested_season_coverage": candidate.get("requested_season_coverage"),
            "expected_episode_count": candidate.get("expected_episode_count"),
            "auto_queue_allowed": candidate.get("auto_queue_allowed"),
            "auto_queue_blocked_reason": candidate.get("auto_queue_blocked_reason"),
            "selection_warnings": candidate.get("selection_warnings")[:5] if isinstance(candidate.get("selection_warnings"), list) else candidate.get("selection_warnings"),
            "selection_blockers": candidate.get("selection_blockers")[:5] if isinstance(candidate.get("selection_blockers"), list) else candidate.get("selection_blockers"),
            "llm_recommended": bool(candidate.get("llm_recommended")),
        }
