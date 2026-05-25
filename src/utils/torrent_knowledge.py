"""
Torrent scene reference guide for LJS.

A comprehensive, LLM-consumable reference of torrent release types, quality
tiers, naming conventions, and red flags. This is NOT used for hard filtering —
it is serialized into the AI prompt so the LLM can make informed quality
judgments about any torrent title, even ones with acronyms or naming patterns
the code has never seen before.

Also provides utility class methods for deterministic release-quality hints.
Bundle interpretation is category-owned; this module only provides generic
quality and red-flag context for the LLM.

Based on scene release terminology per Wikipedia and scenerules.org.
"""

import re

TORRENT_QUALITY_GUIDE = """\
## Torrent Release Quality Reference

You are evaluating torrent search results. Use this guide to understand what
each tag, acronym, and naming pattern means. Do NOT rely on pattern matching
alone — reason about what the title tells you about the source quality.

## Release Types (worst to best)

### THEATER RECORDINGS — Always reject, regardless of resolution claims

| Tag | Meaning |
|-----|---------|
| CAM, CAM-Rip, CamRip | Recorded in a movie theater with a camcorder or phone. Audio is from the camera mic — you hear the audience, footsteps, coughing. Video is tilted, shaking, or obstructed. "1080p CAM" is still garbage — the resolution is the camera's, not the content's. |
| TS, TELESYNC, HDTS, PDVD | Same as CAM but audio comes from a direct source (headphone jack, FM micro-broadcast for hearing-impaired). Video is still a theater camera recording. Only marginally better than CAM. "HDTS" is NOT high-definition video. |
| TC, TELECINE, HDTC | Transferred from a film reel using a telecine machine. Very rare. Better than CAM but worse than any digital source. Slight horizontal jitter, inferior color. |

### PRE-RELEASE — Variable quality, usually worse than retail

| Tag | Meaning |
|-----|---------|
| WP, WORKPRINT | Unfinished studio cut. Missing effects, timecode overlay, watermark. May have scenes not in final release (or missing scenes that are). |
| SCR, SCREENER, DVDSCR, BDSCR, WEBSCREENER | Promotional copy sent to reviewers/awards voters. Often has "FOR YOUR CONSIDERATION" watermark. Some scenes may be in black-and-white to discourage piracy. DVDSCR is close to DVD quality but with watermark. |
| DDC | Digital Distribution Copy — same as screener but sent digitally (FTP/HTTP). Quality between screener and R5. |
| R5, R5.LINE, R5.AC3.5.1.HQ | Studio-produced telecine for Russian market (DVD Region 5). Video is unprocessed telecine. Audio may be Russian; if synced to English line audio, tagged LiNE. Sound quality is worse than DVD-Rip. |

### DIGITAL SOURCES — These are the useful tiers

| Tag | Meaning | Quality Notes |
|-----|---------|---------------|
| DVDRip, DVDMux | Ripped from a retail DVD. | Max 480p/576p. Decent but outdated. Size ~700MB-4.5GB. |
| HDTV, HDTVRip, PDTV, DSRip, SATRip, DTHRip, DVBRip, TVRip | Captured from a TV broadcast. | May have channel logos, compression artifacts, occasional ad banner. HDTV at 1080i can look good. |
| VODRip, VODR | Recorded from Video-On-Demand (cable/satellite). | Better than HDTV but worse than WEB-DL. Screen-capture method may degrade quality. |
| HC, HC HDRip, HD-Rip | Hardcoded subtitles (HC = Hard-Coded). | Subtitles are burned into the video and cannot be removed. Usually from Korean VOD services (Naver). Screen-recorded, so quality is lower than WEB. If you see "KORSUB" or "HC", the subs are non-removable. |
| WEBRip, WEB Rip, WEB-Rip | Extracted from a streaming service (Netflix, Amazon, Hulu, Crunchyroll, etc.) and re-encoded. | Lossy re-encode. The video was decoded and re-encoded — quality loss from the original stream. Very common. Often mislabeled as WEB-DL. |
| WEBCap, WEB-Cap | Captured by screen-recording a streaming service. | Similar to WEBRip but with potential frame drops or sync issues. Rare now, mostly replaced by WEBRip. |
| HDRip | Transcoded from HDTV or WEB-DL source. | Any type of HD transcode — quality varies. |
| **WEB-DL**, WEBDL, WEB DL | Downloaded directly from a streaming service (iTunes, Amazon Video) without re-encoding. | The original digital file. Best non-disc source. No logos, no re-encoding artifacts. "Untouched" quality. This is the gold standard for non-Blu-ray releases. |
| BluRay, Blu-ray, BDRip, BRRip, COMPLETE.BLURAY | Encoded from a Blu-ray disc. | High quality. BDRip = encoded from BD to lower resolution. BRRip = transcoded from an already-encoded 1080p source (worse than BDRip). |
| REMUX | Blu-ray video/audio copied into an MKV container without any re-encoding. | Identical quality to the disc. The highest quality possible. File sizes are very large (30-60GB for a movie) and this is NORMAL for REMUX. Do not reject for file size. |

## Common Tags & Abbreviations

| Tag | Meaning |
|-----|---------|
| PROPER | A corrected version released by a DIFFERENT group because the original had errors (sync issues, wrong episode, glitch). Same source quality, not better. |
| REPACK | A corrected version released by the SAME group. Same as PROPER in quality terms. |
| MULTi | Release has at least 2 audio languages. |
| MULTiSUBS / Multi-Subs | Release has at least 6 subtitle languages. |
| DS4K | 4K downscaled to a lower resolution (downscaled 4K). |
| RM4K | 4K remaster presented in 1080p. |
| CBR | Constant Bit-Rate encoding. |
| VBR | Variable Bit-Rate encoding (usually better quality per byte). |

## Streaming Platform Abbreviations (source tags in titles)

These appear in titles to indicate the streaming source for WEB-DL/WEBRip releases:

| Tag | Platform |
|-----|---------|
| AMZN | Amazon Prime Video |
| ATVP | Apple TV+ |
| DSNP / DSPA | Disney+ |
| NF / Netflix | Netflix |
| CR | Crunchyroll |
| HMAX | HBO Max / Max |
| HULU | Hulu |
| PCOK | Paramount+ |
| PMTP | Peacock |
| ALL4 | Channel 4 (UK) |
| BBCi | BBC iPlayer |
| CNLP | Canal+ (France) |
| CRAV | Crave (Canada) |
| IT | iTunes |
| CMAX | Cinemax |
| SHO | Showtime |
| STL | Starz |
| AE | A&E |
| ABC | American Broadcasting Company |
| AMC | AMC |
| CW | The CW |
| SYFY | Syfy |
| USA | USA Network |
| FOX | Fox |
| NBC | NBC |
| CBS | CBS |
| CC | Comedy Central |
| MTV | MTV |
| DSCP | Discovery+ |
| CRIT | Criterion Channel |
| BNGE | Binge (Australia) |
| 9NOW | 9Now (Australia) |
| CBC | CBC Gem (Canada) |
| ARD | ARD (Germany) |

## Video Codecs

| Codec | Generation | Notes |
|-------|-----------|-------|
| XviD, DivX | Legacy | Obsolete. Max DVD-quality (~480p). Very small files. |
| x264, AVC, H.264 | Modern | Standard for good quality. Most common in 1080p releases. |
| x265, HEVC, H.265 | Current | Better compression than H.264 — same quality at smaller size. Common for 4K. |
| AV1 | Next-gen | Best compression efficiency. Newer, less common. Getting adopted by YouTube, Netflix. |

## Anime-Specific Conventions

| Pattern | Meaning |
|---------|---------|
| [GroupName] | Release group/subgroup name in brackets (e.g., [SubsPlease], [Erai-raws], [HorribleSubs]) |
| Title - 01 | Episode number without S01E format |
| 480p in anime | Very common for simulcasts; not a red flag for anime |
| 1080p in brackets [1080p] | From a premium web source (usually Crunchyroll/VRV) |

## Multi-file Bundles

Grouped torrents may contain several useful payloads. Generic code must treat total size and naming as soft signals and rely on category-provided bundle descriptors plus LLM judgment to decide which payloads are useful.

## Red Flags

| Pattern | Why it's a problem |
|---------|-------------------|
| HDCAM, HD-CAM | "HD" refers to the camera resolution, not the content. Still a theater camcorder. |
| HDTS, HD-TS | Same — HD camera, still theater audio. |
| KORSUB, HC, HCSUB | Hardcoded Korean/Chinese subtitles burned into the video. Cannot be removed. |
| HardSub | Hardcoded subtitles (any language). Non-removable. |
| CAMRip, TSrip | Explicitly labeled as theater recording. |
| Line audio (with CAM/TS) | Audio from a headphone jack — still paired with a camera recording. |

## Quality Decision Rules

1. **NEVER select CAM, TS, HDCAM, HDTS, TC, or any theater recording** regardless
   of what resolution they claim. "1080p HDCAM" is still a phone camera in a theater.

2. **WEB-DL > WEBRip > HDTV > DVDRip** for non-disc sources.

3. **REMUX > BluRay > WEB-DL** for the highest quality when file size is not a concern.

4. **Season packs are perfectly acceptable** — the system downloads only the needed
   episode. Don't reject for total size.

5. **Hardcoded subtitles (HC, KORSUB)** are annoying but may be the only source
   available for very recent releases. Accept as a last resort if no other source exists.

6. **PROPER/REPACK** tags are positive signals — they mean a known-bad release was
   fixed. Same quality as the source type.

7. **Source abbreviations** (AMZN, NF, ATVP, etc.) indicate WEB-DL origin and are
   quality indicators — they tell you exactly which streaming platform it came from.
   AMZN/NF/ATVP WEB-DLs are usually very good quality.

8. **Use your judgment** — if a title has an abbreviation or pattern not listed here,
   reason about what it likely means based on context (position in title, surrounding
   tags, file size, etc.).
"""


COMPACT_TORRENT_QUALITY_GUIDE = """\
## Compact Torrent Quality Guide

Use this compact guide while selecting candidates. Keep category coverage,
language, magnet availability, seeders, size/bitrate, and user preferences above
raw title prestige.

Reject or strongly avoid:
- CAM, HDCAM, TS/HDTS, TC/HDTC, WORKPRINT, SCREENER/DVDSCR/BDSCR, R5, HC/KORSUB hardcoded-sub releases unless the user explicitly accepts them.
- Wrong language/audio, wrong unit coverage, missing magnet, very low seeders when safer candidates exist, obvious mislabeled titles.

Prefer, roughly best to acceptable:
- REMUX / BluRay / BDRip for high quality when size is acceptable.
- WEB-DL / WEBDL as the best normal streaming source.
- WEBRip / HDTV when no better source is available.
- DVDRip only for old content.

Useful title tags:
- MULTi usually means multiple audio languages; verify it satisfies the requested language.
- SUBS/MULTiSUBS are subtitles, not audio.
- PROPER/REPACK usually means a corrected release, not inherently higher source quality.
- x265/HEVC is efficient; x264 is broadly compatible.
- 10bit/HDR/DV can be excellent but may reduce compatibility.

For episodic or other unit-based categories, exact unit coverage and pack safety
come from the category search hook and local library context, not from generic
TorrentKnowledge heuristics alone.
"""


def get_compact_quality_guide() -> str:
    """Return a compact torrent quality guide suitable for every download prompt."""
    return COMPACT_TORRENT_QUALITY_GUIDE


def get_quality_guide() -> str:
    """Return the full torrent quality reference guide for offline explainability/tests."""
    return TORRENT_QUALITY_GUIDE


# Release type classification used by TorrentKnowledge class methods
# and by quality.py for RELEASE_TYPE_RANK
_RELEASE_TYPE_INFO = {
    "cam": {"quality_tier": "unacceptable", "score_modifier": -0.20, "description": "Recorded in a theater with a camcorder"},
    "ts": {"quality_tier": "unacceptable", "score_modifier": -0.18, "description": "Telesync: theater camera with direct audio"},
    "hdcam": {"quality_tier": "unacceptable", "score_modifier": -0.20, "description": "HD camera recording in theater — still a camcorder"},
    "hdts": {"quality_tier": "unacceptable", "score_modifier": -0.18, "description": "HD telesync — still theater video"},
    "telecine": {"quality_tier": "unacceptable", "score_modifier": -0.15, "description": "Telecine transfer from film reel"},
    "scr": {"quality_tier": "poor", "score_modifier": -0.10, "description": "Screener: promotional copy, usually watermarked"},
    "dvdscr": {"quality_tier": "poor", "score_modifier": -0.08, "description": "DVD screener: near-DVD quality but watermarked"},
    "r5": {"quality_tier": "poor", "score_modifier": -0.08, "description": "Region 5 telecine for Russian market"},
    "wp": {"quality_tier": "poor", "score_modifier": -0.10, "description": "Workprint: unfinished studio cut"},
    "dvdrip": {"quality_tier": "fair", "score_modifier": 0.0, "description": "Ripped from retail DVD, max 480p/576p"},
    "hdtv": {"quality_tier": "good", "score_modifier": 0.05, "description": "Captured from TV broadcast, may have logos"},
    "webrip": {"quality_tier": "good", "score_modifier": 0.08, "description": "Re-encoded from streaming source"},
    "web-dl": {"quality_tier": "very_good", "score_modifier": 0.15, "description": "Original digital file from streaming, untouched"},
    "webdl": {"quality_tier": "very_good", "score_modifier": 0.15, "description": "Same as WEB-DL"},
    "bluray": {"quality_tier": "very_good", "score_modifier": 0.12, "description": "Encoded from Blu-ray disc"},
    "bdrip": {"quality_tier": "very_good", "score_modifier": 0.12, "description": "Blu-ray disc rip, lower resolution"},
    "brrip": {"quality_tier": "good", "score_modifier": 0.08, "description": "Re-encoded from existing Blu-ray encode"},
    "remux": {"quality_tier": "best", "score_modifier": 0.20, "description": "Lossless Blu-ray copy, no re-encoding"},
    "proper": {"quality_tier": "neutral_positive", "score_modifier": 0.05, "description": "Fixed release by a different group"},
    "repack": {"quality_tier": "neutral_positive", "score_modifier": 0.05, "description": "Fixed release by the same group"},
}

# Red flag detection patterns: (regex_pattern, flag_type, reason)
_RED_FLAG_PATTERNS = [
    (r"hdcam|hd-cam", "theater_recording", "HDCAM: HD camera recording in theater — still a camcorder recording"),
    (r"hdts|hd-ts", "theater_recording", "HDTS: HD telesync — still theater video from a camera"),
    (r"\bcam\b|camrip|cam-rip", "theater_recording", "CAM: Recorded in a theater with a camcorder — unacceptable quality"),
    (r"\btsrip\b", "theater_recording", "TS-Rip: Explicitly labeled theater recording"),
    (r"korsub|korsub", "hardcoded_subs", "KORSUB: Hardcoded Korean subtitles burned into video, cannot be removed"),
    (r"\bhc\b|hcsub|hc-sub", "hardcoded_subs", "HC: Hardcoded subtitles burned into video, cannot be removed"),
    (r"hardsub|hard-sub", "hardcoded_subs", "HardSub: Hardcoded subtitles (any language), cannot be removed"),
]


class TorrentKnowledge:
    """Utility class for deterministic torrent quality analysis.

    Provides static methods for release-quality hints, red flags, release
    type info, and quality explanations. Category-specific bundle handling
    lives behind category hooks. The LLM gets the TORRENT_QUALITY_GUIDE for
    its own reasoning — these methods are soft signals only.
    """

    @staticmethod
    def detect_red_flags(title: str) -> list[dict]:
        """Detect red flags in a torrent title.

        Red flags indicate problems like theater recordings or hardcoded
        subtitles. These are presented as soft hints to the LLM — they
        are NOT hard rejection criteria.

        Args:
            title: Torrent title string.

        Returns:
            List of dicts with 'flag_type' and 'reason' keys.
        """
        flags = []
        for pattern, flag_type, reason in _RED_FLAG_PATTERNS:
            if re.search(pattern, title, re.IGNORECASE):
                flags.append({"flag_type": flag_type, "reason": reason})
        return flags

    @staticmethod
    def get_release_type_info(release_type: str) -> dict | None:
        """Look up quality information for a release type.

        Args:
            release_type: Lowercase release type string (e.g., "cam", "web-dl").

        Returns:
            Dict with quality_tier, score_modifier, and description, or None.
        """
        return _RELEASE_TYPE_INFO.get(release_type.lower())

    @staticmethod
    def build_quality_explanation(title: str, tags: dict) -> str:
        """Build a human-readable quality explanation for a torrent result.

        Used to annotate search results with context about what quality
        issues or strengths the title indicates.

        Args:
            title: Torrent title string.
            tags: Quality tags dict from extract_quality_tags().

        Returns:
            Multi-line explanation string.
        """
        parts = []

        # Release type explanation
        rtype = tags.get("release_type")
        if rtype:
            info = TorrentKnowledge.get_release_type_info(rtype)
            if info:
                tier = info["quality_tier"].replace("_", " ").title()
                parts.append(f"Release type: {rtype.upper()} ({tier} quality)")

        # Red flags
        red_flags = tags.get("red_flags", [])
        for flag in red_flags[:3]:
            parts.append(f"WARNING: {flag.get('reason', 'Unknown quality issue')}")

        if not parts:
            return "No quality concerns detected."

        return "\n".join(parts)