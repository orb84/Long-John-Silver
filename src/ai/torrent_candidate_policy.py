"""Hard torrent candidate eligibility and ordering helpers.

This module intentionally keeps deterministic logic narrow.  The LJS agent is
supposed to use the LLM for the messy semantic parts of torrent interpretation
(language acronyms, edition naming, title variants, bundles/packs, release groups).  The
helpers here only remove candidates that are not queueable or that violate
explicit hard user/app constraints before a prompt is sent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.core.models import NormalizedTorrentCandidate, QualityProfile, SearchResult
from src.utils.quality import extract_quality_tags
from src.utils.torrent_knowledge import TorrentKnowledge

REJECTED_RELEASE_TYPES = {"cam", "ts", "hdcam", "camrip", "tsrip", "hdts"}


@dataclass(frozen=True)
class CandidateEligibility:
    """Download eligibility verdict before LLM interpretation."""

    eligible: bool
    reason: str = ""
    hard_reject: bool = False


class TorrentCandidateEligibilityPolicy:
    """Applies only non-semantic hard rejects before LLM selection."""

    def evaluate(self, result: SearchResult, require_magnet: bool = True) -> CandidateEligibility:
        """Return whether a raw search result may be shown to the LLM."""
        tags = extract_quality_tags(result.title)
        return self._evaluate_common(
            title=result.title,
            release_type=tags.get("release_type"),
            red_flags=[self._flag_reason(flag) for flag in tags.get("red_flags", [])],
            content_blacklisted=bool(tags.get("content_blacklisted")),
            blacklist_reason=str(tags.get("blacklist_reason") or "blacklisted content"),
            magnet_available=bool(result.magnet),
            require_magnet=require_magnet,
        )

    def evaluate_normalized(
        self,
        candidate: NormalizedTorrentCandidate,
        require_magnet: bool = True,
        quality_profile: Optional[QualityProfile] = None,
    ) -> CandidateEligibility:
        """Return whether a normalized candidate is still queueable."""
        verdict = self._evaluate_common(
            title=candidate.title,
            release_type=candidate.release_type,
            red_flags=candidate.red_flags or [],
            content_blacklisted=False,
            blacklist_reason="blacklisted content",
            magnet_available=bool(candidate.magnet_available),
            require_magnet=require_magnet,
        )
        if not verdict.eligible:
            return verdict
        return self._evaluate_quality_budget(candidate, quality_profile)

    def _evaluate_common(
        self,
        title: str,
        release_type: object,
        red_flags: list[str],
        content_blacklisted: bool,
        blacklist_reason: str,
        magnet_available: bool,
        require_magnet: bool,
    ) -> CandidateEligibility:
        if content_blacklisted:
            return CandidateEligibility(False, blacklist_reason, True)
        release = str(release_type or "").lower()
        if release in REJECTED_RELEASE_TYPES or self._has_theater_flag(red_flags):
            return CandidateEligibility(False, "theater recording", True)
        if self._has_unplayable_payload_flag(red_flags):
            return CandidateEligibility(False, "unplayable payload", True)
        if require_magnet and not magnet_available:
            return CandidateEligibility(False, "missing magnet", True)
        return CandidateEligibility(True)

    @staticmethod
    def _evaluate_quality_budget(
        candidate: NormalizedTorrentCandidate,
        quality_profile: Optional[QualityProfile],
    ) -> CandidateEligibility:
        if not quality_profile or not quality_profile.max_file_size_mb:
            return CandidateEligibility(True)
        size_mb = TorrentCandidateRanking.per_episode_size_mb(candidate)
        if not size_mb:
            return CandidateEligibility(True)
        max_mb = float(quality_profile.max_file_size_mb)
        # Leave a small tolerance for indexer rounding, but do not ask the LLM
        # to pick files that clearly exceed an explicit configured size budget.
        if size_mb > max_mb * 1.05:
            return CandidateEligibility(False, f"above size budget ({size_mb:.0f} MB > {max_mb:.0f} MB)", True)
        return CandidateEligibility(True)

    @staticmethod
    def _flag_reason(flag: object) -> str:
        if isinstance(flag, dict):
            return str(flag.get("reason") or flag.get("flag_type") or "")
        return str(flag or "")

    @staticmethod
    def _has_theater_flag(red_flags: list[str]) -> bool:
        haystack = " ".join(red_flags).lower()
        return any(term in haystack for term in ("theater", "camcorder", "camrip", "ts-rip", "telesync"))

    @staticmethod
    def _has_unplayable_payload_flag(red_flags: list[str]) -> bool:
        haystack = " ".join(red_flags).lower()
        return any(term in haystack for term in ("blacklist", "executable", "malware"))


class TorrentCandidateRanking:
    """Deterministic ordering for prompt compactness, not final judgment."""

    @staticmethod
    def pre_score(result: SearchResult, preferred_language: str | None = None) -> tuple:
        """Order candidates before prompt construction without rejecting ambiguity."""
        tags = extract_quality_tags(result.title)
        preferred = (preferred_language or "").strip().lower()
        langs = [str(lang).lower() for lang in tags.get("languages", [])]
        lang_match = 1 if preferred and preferred in langs else 0
        multi = 1 if tags.get("is_multi_language") else 0
        return (lang_match, multi, result.quality_score, result.seeders or 0)

    @staticmethod
    def per_episode_size_mb(candidate: NormalizedTorrentCandidate) -> float:
        """Estimate useful per-unit/file size for releases and bundles."""
        if not candidate.size_bytes:
            return 0.0
        if candidate.is_bundle and candidate.estimated_unit_size_mb:
            return float(candidate.estimated_unit_size_mb)
        return candidate.size_bytes / (1024 * 1024)

    @staticmethod
    def target_episode_size_from_context(quality_context: str | None) -> float | None:
        """Extract machine-readable target_episode_size_mb from quality context."""
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

    @staticmethod
    def selection_score(
        candidate: NormalizedTorrentCandidate,
        preferred_language: str | None,
        preferred_resolution: str | None,
        target_episode_size_mb: float | None = None,
    ) -> tuple:
        """Rank candidates before/after LLM tie-breaking without semantic rejection."""
        lang_score = TorrentCandidateRanking.language_score(candidate, preferred_language)
        res_score = TorrentCandidateRanking.resolution_score(candidate, preferred_resolution)
        size_mb = TorrentCandidateRanking.per_episode_size_mb(candidate)
        target_score, undersized_penalty = TorrentCandidateRanking.size_scores(size_mb, target_episode_size_mb)
        codec_bonus = 1 if (candidate.codec or "").lower() in {"h265", "x265", "hevc", "av1"} else 0
        return (
            lang_score,
            res_score,
            target_score,
            undersized_penalty,
            codec_bonus,
            candidate.seeders or 0,
            candidate.quality_score,
            -size_mb if size_mb else 0,
        )

    @staticmethod
    def language_score(candidate: NormalizedTorrentCandidate, preferred_language: str | None) -> int:
        """Return a ranking score for candidate language fit."""
        if candidate.language and preferred_language:
            lang = candidate.language.lower()
            pref = preferred_language.lower()
            return 2 if pref in lang else 1 if "multi" in lang else 0
        return 1 if not preferred_language else 0

    @staticmethod
    def resolution_score(candidate: NormalizedTorrentCandidate, preferred_resolution: str | None) -> int:
        """Return a ranking score for candidate resolution fit."""
        if not candidate.resolution:
            return 0
        from src.utils.quality import QualityAnalyzer
        score = QualityAnalyzer.rank_resolution(candidate.resolution)
        if preferred_resolution and candidate.resolution == preferred_resolution:
            score += 2
        return score

    @staticmethod
    def size_scores(size_mb: float, target_episode_size_mb: float | None) -> tuple[float, int]:
        """Return target-distance and undersized penalty scores."""
        if not target_episode_size_mb or not size_mb:
            return 0.0, 0
        distance = abs(size_mb - target_episode_size_mb) / max(target_episode_size_mb, 1)
        undersized_penalty = -2 if size_mb < target_episode_size_mb * 0.65 else 0
        return -distance, undersized_penalty
