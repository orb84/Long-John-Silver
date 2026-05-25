"""
Quality profile utilities for LJS.

Provides helpers to evaluate and compare media quality against user preferences.
Scoring considers resolution, video codec, audio codec, HDR, release type,
file size reasonableness, release group reputation,
language extraction from titles, and red flag detection. Bundle semantics are
category-owned and are not inferred in this generic quality utility.
"""

import re
from loguru import logger
from src.core.models import QualityProfile
from src.utils.torrent_knowledge import TorrentKnowledge


"""
Quality profile utilities for LJS.

Provides helpers to evaluate and compare media quality against user preferences.
Scoring considers resolution, video codec, audio codec, HDR, release type,
file size reasonableness, release group reputation,
language extraction from titles, and red flag detection. Bundle semantics are
category-owned and are not inferred in this generic quality utility.
"""

import re
from typing import Optional
from src.core.models import QualityProfile
from src.utils.torrent_knowledge import TorrentKnowledge


class QualityAnalyzer:
    """Static class for analyzing media quality and scoring results."""

    # Scoring Weights (Constants)
    WEIGHT_RESOLUTION_MATCH = 0.30
    WEIGHT_RESOLUTION_HIGHER = 0.20
    WEIGHT_RESOLUTION_LOWER = 0.10
    PENALTY_RESOLUTION_EXCEEDED = 0.30

    WEIGHT_CODEC_MATCH = 0.20
    WEIGHT_CODEC_MID = 0.10
    WEIGHT_CODEC_LOW = 0.05

    WEIGHT_AUDIO_HIGH = 0.15
    WEIGHT_AUDIO_MID = 0.08
    WEIGHT_AUDIO_LOW = 0.03

    WEIGHT_RELEASE_HIGH = 0.15
    WEIGHT_RELEASE_MID = 0.10
    WEIGHT_RELEASE_LOW = 0.03
    PENALTY_RELEASE_CAM = 0.10

    WEIGHT_HDR_PREFER = 0.10
    WEIGHT_HDR_PREFER_FALLBACK = 0.08
    WEIGHT_HDR_NEUTRAL = 0.03
    WEIGHT_HDR_NEUTRAL_FALLBACK = 0.02

    WEIGHT_SIZE_IDEAL = 0.10
    WEIGHT_SIZE_SMALL = 0.03
    WEIGHT_SIZE_REASONABLE = 0.05
    PENALTY_SIZE_OVER = 0.05
    BONUS_SIZE_OVER_MIN = 0.02

    WEIGHT_LANGUAGE_MATCH = 0.15
    WEIGHT_LANGUAGE_MULTI = 0.10
    PENALTY_LANGUAGE_MISMATCH = 0.10

    PENALTY_RED_FLAG_UNIT = 0.05
    PENALTY_RED_FLAG_MAX = 0.25

    # Data Ranks
    RESOLUTION_RANK = {
        "4k": 4, "2160p": 4,
        "1080p": 3,
        "720p": 2,
        "480p": 1,
        "sd": 0,
    }

    CODEC_RANK = {
        "hevc": 3, "h265": 3, "x265": 3,
        "h264": 2, "x264": 2, "avc": 2,
        "av1": 4,
        "mpeg": 0,
    }

    AUDIO_CODEC_RANK = {
        "atmos": 5, "dtsx": 5, "truehd": 4,
        "dts-hd": 4, "dtshd": 4,
        "flac": 3,
        "aac": 2, "dd": 2, "ac3": 2, "ddp": 2, "dd+": 2,
        "mp3": 1,
    }

    # Release types: PROPER > REPACK > REMUX > WEB-DL > HDTV > DVDRIP > HDCAM > CAM/TS
    RELEASE_TYPE_RANK = {
        "proper": 4,
        "repack": 4,
        "remux": 3,
        "web-dl": 3, "webdl": 3,
        "bluray": 3, "brrip": 3, "bdrip": 3,
        "hdtv": 2,
        "dvdrip": 2,
        "hdcam": 1,
        "predvd": 0,
        "telecine": 0,
        "cam": 0,
    }

    # Language detection patterns: (regex pattern, canonical language name)
    _LANGUAGE_PATTERNS: list[tuple[str, str]] = [
        (r"\bITA\b|\biTALiAN\b|\bItaliano\b", "Italian"),
        (r"\bENG\b|\bEnglish\b", "English"),
        (r"\bFRE\b|\bFrench\b|\bFrancais\b", "French"),
        (r"\bGER\b|\bGerman\b|\bDeutsch\b", "German"),
        (r"\bSPA\b|\bSpanish\b|\bEspanol\b", "Spanish"),
        (r"\bJPN\b|\bJapanese\b", "Japanese"),
        (r"\bKOR\b|\bKorean\b", "Korean"),
        (r"\bCHI\b|\bChinese\b", "Chinese"),
        (r"\bRUS\b|\bRussian\b", "Russian"),
        (r"\bPOR\b|\bPortuguese\b|\bPortugues\b", "Portuguese"),
        (r"\bHINDI\b|\bHIN\b", "Hindi"),
        (r"\bTAMIL\b|\bTAM\b", "Tamil"),
        (r"\bTELUGU\b|\bTEL\b", "Telugu"),
        (r"\bARABIC\b|\bARA\b", "Arabic"),
        (r"\bPOLISH\b|\bPOL\b|\bPolski\b", "Polish"),
        (r"\bTURKISH\b|\bTUR\b", "Turkish"),
        (r"\bSWEDISH\b|\bSWE\b", "Swedish"),
        (r"\bNORWEGIAN\b|\bNOR\b", "Norwegian"),
        (r"\bDANISH\b|\bDAN\b", "Danish"),
        (r"\bFINNISH\b|\bFIN\b", "Finnish"),
        (r"\bDUTCH\b|\bNLD\b", "Dutch"),
        (r"\bCZECH\b|\bCZE\b", "Czech"),
        (r"\bHUNGARIAN\b|\bHUN\b", "Hungarian"),
        (r"\bROMANIAN\b|\bROM\b|\bRomana\b", "Romanian"),
        (r"\bGREEK\b|\bGRE\b", "Greek"),
        (r"\bHEBREW\b|\bHEB\b", "Hebrew"),
        (r"\bTHAI\b", "Thai"),
        (r"\bVIETNAMESE\b|\bVIE\b", "Vietnamese"),
        (r"\bINDONESIAN\b|\bIND\b", "Indonesian"),
        (r"\bMALAY\b|\bMAY\b", "Malay"),
        (r"\bNORWEG\b", "Norwegian"),
    ]

    # Multi-language indicators
    _MULTI_PATTERNS = [
        r"\bMULTI\b", r"\bMULTi\b", r"\bMULT\b",
        r"\bDUAL\b", r"\bDual\b",
    ]

    # Content type rejection patterns
    _CONTENT_BLACKLIST: list[tuple[str, str]] = [
        (r"\.part\d+\.rar$|\.r00$|\.r01$", "archive_files"),
    ]

    @classmethod
    def rank_resolution(cls, resolution: str) -> int:
        """Return a numeric rank for a resolution string."""
        return cls.RESOLUTION_RANK.get(resolution.lower(), 0)

    @classmethod
    def rank_codec(cls, codec: str) -> int:
        """Return a numeric rank for a video codec string."""
        return cls.CODEC_RANK.get(codec.lower(), 1)

    @classmethod
    def extract_quality_tags(cls, title: str) -> dict:
        """Extract quality indicators from a torrent title string."""
        lower = title.lower()
        tags = {
            "resolution": None,
            "codec": None,
            "audio_codec": None,
            "hdr": False,
            "dolby_vision": False,
            "release_type": None,
            "estimated_size_gb": None,
            "red_flags": [],
            "languages": [],
            "is_multi_language": False,
            "content_blacklisted": False,
            "blacklist_reason": None,
        }

        # Content blacklist check
        blacklist_reason = cls._check_content_blacklist(title)
        if blacklist_reason:
            tags["content_blacklisted"] = True
            tags["blacklist_reason"] = blacklist_reason

        # Language detection
        tags["languages"] = cls._detect_languages(title)
        tags["is_multi_language"] = cls._detect_is_multi_language(title)

        # Resolution
        for res in ["2160p", "1080p", "720p", "480p"]:
            if res in lower:
                tags["resolution"] = res
                break
        if "4k" in lower and tags["resolution"] is None:
            tags["resolution"] = "2160p"

        # Video codec
        for codec in ["hevc", "h265", "x265", "h264", "x264", "av1"]:
            if codec in lower:
                tags["codec"] = codec
                break

        # Audio codec
        for audio in ["atmos", "dtsx", "truehd", "dts-hd", "dtshd", "flac",
                      "aac", "dd+", "ddp", "dd", "ac3", "mp3"]:
            if audio in lower:
                tags["audio_codec"] = audio
                break
        if not tags["audio_codec"] and ("7.1" in lower or "5.1" in lower):
            tags["audio_codec"] = "aac"

        # HDR / Dolby Vision
        if any(hdr in lower for hdr in ["hdr", "hdr10", "hdr10+"]):
            tags["hdr"] = True
        if any(dv in lower for dv in ["dolby vision", "dovi", ".dv.", "-dv-"]):
            tags["dolby_vision"] = True

        # Release type
        for rtype, _ in sorted(cls.RELEASE_TYPE_RANK.items(), key=lambda x: -len(x[0])):
            if rtype in lower:
                tags["release_type"] = rtype
                break

        if not tags["release_type"]:
            short_types = {
                r"\bts\b": "cam",
                r"\btc\b": "telecine",
                r"\bscr\b": "cam",
                r"\bweb\b": "web-dl",
            }
            for pattern, rtype in short_types.items():
                if re.search(pattern, lower):
                    tags["release_type"] = rtype
                    break

        # Estimated size
        size_match = re.search(r"(\d+(?:\.\d+)?)\s*(gb|mb|tb)", lower)
        if size_match:
            value = float(size_match.group(1))
            unit = size_match.group(2)
            if unit == "tb":
                tags["estimated_size_gb"] = value * 1024
            elif unit == "gb":
                tags["estimated_size_gb"] = value
            elif unit == "mb":
                tags["estimated_size_gb"] = value / 1024

        tags["red_flags"] = TorrentKnowledge.detect_red_flags(title)
        return tags

    @classmethod
    def score_result(cls, title: str, profile: QualityProfile | None = None,
                     preferred_language: str | None = None) -> float:
        """Score a search result based on how well it matches the quality profile."""
        tags = cls.extract_quality_tags(title)
        if tags.get("content_blacklisted"):
            return 0.0
        if profile is None:
            return 0.5

        score = 0.0

        # Resolution
        if tags["resolution"]:
            desired_rank = cls.rank_resolution(profile.preferred_resolution)
            result_rank = cls.rank_resolution(tags["resolution"])
            if result_rank == desired_rank:
                score += cls.WEIGHT_RESOLUTION_MATCH
            elif result_rank > desired_rank:
                # Exceeding the preferred resolution is heavily penalized (e.g. 4k when 1080p is preferred)
                score -= cls.PENALTY_RESOLUTION_EXCEEDED
            else:
                score += cls.WEIGHT_RESOLUTION_LOWER

        # Video codec
        if tags["codec"]:
            best_codec_rank = max(
                (cls.rank_codec(c) for c in profile.preferred_codecs),
                default=2,
            )
            result_codec_rank = cls.rank_codec(tags["codec"])
            if result_codec_rank >= best_codec_rank:
                score += cls.WEIGHT_CODEC_MATCH
            elif result_codec_rank >= 2:
                score += cls.WEIGHT_CODEC_MID
            else:
                score += cls.WEIGHT_CODEC_LOW

        # Audio codec
        if tags["audio_codec"]:
            audio_rank = cls.AUDIO_CODEC_RANK.get(tags["audio_codec"], 1)
            if audio_rank >= 4:
                score += cls.WEIGHT_AUDIO_HIGH
            elif audio_rank >= 2:
                score += cls.WEIGHT_AUDIO_MID
            else:
                score += cls.WEIGHT_AUDIO_LOW

        # Release type
        if tags["release_type"]:
            release_rank = cls.RELEASE_TYPE_RANK.get(tags["release_type"], 1)
            if release_rank >= 3:
                score += cls.WEIGHT_RELEASE_HIGH
            elif release_rank >= 2:
                score += cls.WEIGHT_RELEASE_MID
            elif release_rank >= 1:
                score += cls.WEIGHT_RELEASE_LOW
            else:
                score -= cls.PENALTY_RELEASE_CAM

        # HDR / Dolby Vision
        if tags["dolby_vision"] and profile.prefer_hdr:
            score += cls.WEIGHT_HDR_PREFER
        elif tags["hdr"] and profile.prefer_hdr:
            score += cls.WEIGHT_HDR_PREFER_FALLBACK
        elif tags["dolby_vision"] and not profile.prefer_hdr:
            score += cls.WEIGHT_HDR_NEUTRAL
        elif tags["hdr"] and not profile.prefer_hdr:
            score += cls.WEIGHT_HDR_NEUTRAL_FALLBACK

        # File size
        if tags["estimated_size_gb"] is not None and profile.max_file_size_mb:
            max_gb = profile.max_file_size_mb / 1024
            size_gb = tags["estimated_size_gb"]

            if size_gb > max_gb:
                logger.info(
                    f"soft-penalizing '{title}': advertised size {size_gb:.1f}GB exceeds profile limit {max_gb:.1f}GB; category/LLM bundle checks may still accept useful payloads"
                )
                score -= cls.PENALTY_SIZE_OVER

            ratio = size_gb / max_gb if max_gb > 0 else 1.0
            score += cls.WEIGHT_SIZE_IDEAL if ratio >= 0.3 else cls.WEIGHT_SIZE_SMALL
        elif tags["estimated_size_gb"] is not None:
            check_gb = tags["estimated_size_gb"]
            if 0.3 <= check_gb <= 5.0:
                score += cls.WEIGHT_SIZE_REASONABLE

        # Language match
        detected_languages = tags.get("languages", [])
        is_multi = tags.get("is_multi_language", False)
        if preferred_language and detected_languages and not is_multi:
            preferred_lower = preferred_language.lower()
            if any(preferred_lower in lang.lower() or lang.lower() in preferred_lower
                   for lang in detected_languages):
                score += cls.WEIGHT_LANGUAGE_MATCH
            else:
                score -= cls.PENALTY_LANGUAGE_MISMATCH
        elif preferred_language and is_multi:
            score += cls.WEIGHT_LANGUAGE_MULTI

        # Red flags
        red_flags = tags.get("red_flags", [])
        if red_flags:
            penalty = min(len(red_flags) * cls.PENALTY_RED_FLAG_UNIT, cls.PENALTY_RED_FLAG_MAX)
            score -= penalty

        return max(0.0, score)

    @classmethod
    def _detect_languages(cls, title: str) -> list[str]:
        detected: list[str] = []
        for pattern, lang in cls._LANGUAGE_PATTERNS:
            if re.search(pattern, title, re.IGNORECASE):
                if lang not in detected:
                    detected.append(lang)
        return detected

    @classmethod
    def _detect_is_multi_language(cls, title: str) -> bool:
        for pattern in cls._MULTI_PATTERNS:
            if re.search(pattern, title, re.IGNORECASE):
                return True
        return len(cls._detect_languages(title)) > 1

    @classmethod
    def _check_content_blacklist(cls, title: str) -> str | None:
        for pattern, reason in cls._CONTENT_BLACKLIST:
            if re.search(pattern, title, re.IGNORECASE):
                return reason
        return None

    @classmethod
    def format_size(cls, size_bytes_or_str: int | str | None) -> str:
        """Format a size string or raw bytes count into a human-readable string (e.g. '2.3 GB').

        Args:
            size_bytes_or_str: Either a bytes integer or a size string.

        Returns:
            Human readable size string, e.g. "2.2 GB".
        """
        if size_bytes_or_str is None:
            return 'Unknown'

        try:
            # If it's already a string with a unit, return as-is
            if isinstance(size_bytes_or_str, str):
                s = size_bytes_or_str.strip()
                if re.search(r'^[0-9.]+\s*(?:gb|mb|tb|kb|bytes|b)$', s, re.IGNORECASE):
                    return s
                # If it's a digit string, parse to int
                if s.isdigit():
                    bytes_val = int(s)
                else:
                    return s
            else:
                bytes_val = int(size_bytes_or_str)
        except (ValueError, TypeError):
            return str(size_bytes_or_str)

        # Convert raw bytes value to a human-readable scale
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if bytes_val < 1024.0:
                return f"{bytes_val:.1f} {unit}" if unit != 'B' else f"{bytes_val} B"
            bytes_val /= 1024.0
        return f"{bytes_val:.1f} PB"


# ─── Legacy Standalone Wrappers ─────────────────────────────────────
# Provided for backward compatibility with the rest of the codebase.

def rank_resolution(resolution: str) -> int:
    """Execute the public rank_resolution behavior.

    This method is a supported extension point for callers outside the
    class.  Keep its input/output contract stable and move specialized
    logic into collaborators or protected helpers as the feature grows.
    """
    return QualityAnalyzer.rank_resolution(resolution)


def rank_codec(codec: str) -> int:
    """Execute the public rank_codec behavior.

    This method is a supported extension point for callers outside the
    class.  Keep its input/output contract stable and move specialized
    logic into collaborators or protected helpers as the feature grows.
    """
    return QualityAnalyzer.rank_codec(codec)


def extract_quality_tags(title: str) -> dict:
    """Execute the public extract_quality_tags behavior.

    This method is a supported extension point for callers outside the
    class.  Keep its input/output contract stable and move specialized
    logic into collaborators or protected helpers as the feature grows.
    """
    return QualityAnalyzer.extract_quality_tags(title)


def score_result(title: str, profile: QualityProfile | None = None,
                 preferred_language: str | None = None) -> float:
    """Execute the public score_result behavior.

    This method is a supported extension point for callers outside the
    class.  Keep its input/output contract stable and move specialized
    logic into collaborators or protected helpers as the feature grows.
    """
    return QualityAnalyzer.score_result(title, profile, preferred_language)


def format_size(size_bytes_or_str: int | str | None) -> str:
    """Format data for the size surface.

    Return presentation-ready text or values without mutating domain
    objects.  Keep formatting stable because chat, UI, and tests may rely
    on the resulting shape.
    """
    return QualityAnalyzer.format_size(size_bytes_or_str)
