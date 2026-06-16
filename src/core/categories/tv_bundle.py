"""TV-owned torrent bundle helpers.

This module keeps TV-specific season/range pack parsing inside the TV category
package. Generic torrent, queue, and downloader services interact with these
semantics only through category hooks that return neutral bundle descriptors.
"""

from __future__ import annotations

import re

_SEASON_PACK_COMPLETE_RE = re.compile(
    r"S(\d{1,2})[\s._-]*(?:Complete|COMPLETE|Season|Full|Pack)", re.IGNORECASE,
)
_SEASON_PACK_SEASON_RE = re.compile(
    r"Season[\s._-]*(\d{1,2})[\s._-]*(?:Complete|COMPLETE|Pack|Full)", re.IGNORECASE,
)
_SEASON_PACK_IMPLICIT_RE = re.compile(
    r"(?:^|[\s._-])S(\d{1,2})(?:[\s._-]|$)", re.IGNORECASE,
)
_SEASON_RANGE_RE = re.compile(
    r"(?:^|[\s._-])S(\d{1,2})[\s._-]*(?:-|to|thru|through)[\s._-]*S?(\d{1,2})(?:[\s._-]|$)",
    re.IGNORECASE,
)
_SEASON_WORD_RANGE_RE = re.compile(
    r"\bSeasons?[\s._-]*(\d{1,2})[\s._-]*(?:-|to|thru|through)[\s._-]*(\d{1,2})\b",
    re.IGNORECASE,
)
_SEASON_WORD_ADJACENT_LIST_RE = re.compile(
    # Examples: "Season 01 02 [COMPLETE]", "Season 1.2 Complete".
    # The COMPLETE guard keeps this from misreading random title numbers as a
    # season range.
    r"\bSeason[\s._-]+(\d{1,2})[\s._-]+(\d{1,2})\b(?=.*\b(?:Complete|COMPLETE)\b)",
    re.IGNORECASE,
)
_SERIES_COMPLETE_RE = re.compile(
    r"\b(?:Complete[\s._-]*(?:Series|Show|Collection)|(?:Series|Show)[\s._-]*Complete|All[\s._-]*Seasons)\b",
    re.IGNORECASE,
)
_SEASON_PACK_RANGE_RE = re.compile(
    r"S(\d{1,2})E(\d{1,2})[\s._-]*(?:-|to|thru|through)[\s._-]*E?(\d{1,2})",
    re.IGNORECASE,
)
_SEASON_PACK_ADJACENT_EPISODE_RANGE_RE = re.compile(
    # Examples from public trackers: "S01e01 10" or "S01E01 08".
    # The negative lookahead prevents misreading S01E01 1080p as episode 10.
    r"S(\d{1,2})E(\d{1,2})[\s._-]+E?(\d{1,2})(?!\d)",
    re.IGNORECASE,
)
_SEASON_EPISODE_RE = re.compile(r"S(\d{1,2})E(\d{1,2})", re.IGNORECASE)
_ANIME_INDICATORS = re.compile(r"\[.*?\]|subsplease|erai-raws|horriblesubs|crunchyroll", re.IGNORECASE)
_STANDARD_SHOW_EP_RANGE = (18, 24)
_ANIME_EP_RANGE = (12, 13)
_SERIES_FALLBACK_EPISODES = 60


class TVBundleKnowledge:
    """TV-specific parsing and sizing for season/range torrent bundles."""

    @staticmethod
    def detect_season_pack(title: str) -> dict | None:
        """Detect TV season/range/series packs from a torrent title.

        Returns a TV-owned descriptor.  Generic code must not inspect the
        meaning of these keys directly; it receives normalized bundle hints via
        ``TvShowCategory.torrent_bundle_candidate_context``.
        """
        if not title:
            return None

        # Episode ranges must be checked before season ranges so S01E01-E05 is
        # not confused with a multi-season S01-S05 pack.
        m = _SEASON_PACK_RANGE_RE.search(title)
        if m:
            return {
                "season": int(m.group(1)),
                "season_start": int(m.group(1)),
                "season_end": int(m.group(1)),
                "pack_type": "partial_range",
                "scope": "episode_range",
                "start": int(m.group(2)),
                "end": int(m.group(3)),
            }

        m = _SEASON_PACK_ADJACENT_EPISODE_RANGE_RE.search(title)
        if m:
            start = int(m.group(2))
            end = int(m.group(3))
            if end > start and end <= 60:
                return {
                    "season": int(m.group(1)),
                    "season_start": int(m.group(1)),
                    "season_end": int(m.group(1)),
                    "pack_type": "partial_range",
                    "scope": "episode_range",
                    "start": start,
                    "end": end,
                }

        for pattern in (_SEASON_RANGE_RE, _SEASON_WORD_RANGE_RE, _SEASON_WORD_ADJACENT_LIST_RE):
            m = pattern.search(title)
            if m:
                start = int(m.group(1))
                end = int(m.group(2))
                if end < start:
                    start, end = end, start
                if start == end:
                    continue
                return {
                    "season": start,
                    "season_start": start,
                    "season_end": end,
                    "pack_type": "multi_season",
                    "scope": "season_range",
                }

        if _SERIES_COMPLETE_RE.search(title):
            return {
                "season": None,
                "season_start": None,
                "season_end": None,
                "pack_type": "series_complete",
                "scope": "series",
            }

        m = _SEASON_PACK_COMPLETE_RE.search(title)
        if m:
            season = int(m.group(1))
            return {"season": season, "season_start": season, "season_end": season, "pack_type": "complete", "scope": "season"}
        m = _SEASON_PACK_SEASON_RE.search(title)
        if m:
            season = int(m.group(1))
            return {"season": season, "season_start": season, "season_end": season, "pack_type": "complete", "scope": "season"}
        m = _SEASON_PACK_IMPLICIT_RE.search(title)
        if m and not _SEASON_EPISODE_RE.search(title):
            season = int(m.group(1))
            return {"season": season, "season_start": season, "season_end": season, "pack_type": "implicit", "scope": "season"}
        return None

    @staticmethod
    def approximate_episode_count(title: str, season_count: int = 1) -> int:
        """Return a conservative episode-count estimate for TV bundle sizing."""
        low, high = _ANIME_EP_RANGE if _ANIME_INDICATORS.search(title) else _STANDARD_SHOW_EP_RANGE
        return max(1, int(round(((low + high) / 2) * max(1, int(season_count or 1)))))

    @staticmethod
    def estimate_per_episode_size_mb(total_size_bytes: int, title: str, episode_count: int | None = None) -> float:
        """Estimate useful per-episode size for a TV bundle."""
        total_mb = total_size_bytes / (1024 * 1024)
        if episode_count and episode_count > 0:
            return total_mb / episode_count

        pack = TVBundleKnowledge.detect_season_pack(title)
        if pack and pack.get("pack_type") == "partial_range":
            try:
                count = int(pack["end"]) - int(pack["start"]) + 1
                if count > 0:
                    return total_mb / count
            except (KeyError, TypeError, ValueError):
                pass
        if pack and pack.get("pack_type") == "multi_season":
            try:
                count = int(pack["season_end"]) - int(pack["season_start"]) + 1
                return total_mb / TVBundleKnowledge.approximate_episode_count(title, count)
            except (KeyError, TypeError, ValueError):
                pass
        if pack and pack.get("pack_type") == "series_complete":
            return total_mb / _SERIES_FALLBACK_EPISODES

        return total_mb / TVBundleKnowledge.approximate_episode_count(title, 1)
