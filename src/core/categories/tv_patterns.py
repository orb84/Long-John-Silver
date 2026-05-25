"""Compiled filename patterns shared by TV category services."""

from __future__ import annotations

import re

# Regex patterns for episode identification in filenames
_SEASON_DIR = re.compile(
    r"(?:^|\b)(?:season|stagione|s)\s*[\._ -]*0*(\d{1,3})(?:\b|$)",
    re.IGNORECASE,
)
_EPISODE_FILE = re.compile(
    r"[Ss](\d+)[Ee](\d+)|(\d+)x(\d+)|[\.\s_]E(\d+)",
    re.IGNORECASE,
)

# TV name parsing patterns (moved from utils/media_parser.py)
_TV_PATTERNS = [
    # S01E01, S01E01E02, S01E01-02
    re.compile(
        r'(?P<title>.+?)[\s.\-]+S(?P<season>\d{1,2})E(?P<episode>\d{1,2})(?:E\d{1,2}|-?\d{1,2})?',
        re.IGNORECASE,
    ),
    # 1x01
    re.compile(
        r'(?P<title>.+?)[\s.\-]+(?P<season>\d{1,2})x(?P<episode>\d{1,2})',
        re.IGNORECASE,
    ),
    # Season 1 Episode 1
    re.compile(
        r'(?P<title>.+?)[\s.\-]+Season[\s.]+(?P<season>\d{1,2})[\s.\-]+Episode[\s.]+(?P<episode>\d{1,2})',
        re.IGNORECASE,
    ),
    # Season pack: S01 Complete, S01COMPLETE, S01.COMPLETE
    re.compile(
        r'(?P<title>.+?)[\s.\-]+S(?P<season>\d{1,2})[\s.\-]*(?:COMPLETE|COMPL)',
        re.IGNORECASE,
    ),
    # Season pack: Season 1 Complete
    re.compile(
        r'(?P<title>.+?)[\s.\-]+Season[\s.]+(?P<season>\d{1,2})[\s.\-]*(?:COMPLETE|COMPL)',
        re.IGNORECASE,
    ),
    # S01 standalone with quality tag
    re.compile(
        r'(?P<title>.+?)[\s.\-]+S(?P<season>\d{1,2})(?:[\s.\-]+|$)(?=.*(?:1080p|720p|2160p|WEB|BluRay|HDTV|x264|x265|HEVC))',
        re.IGNORECASE,
    ),
    # Season 1 standalone
    re.compile(
        r'(?P<title>.+?)[\s.\-]+Season[\s.]+(?P<season>\d{1,2})(?:[\s.\-]+|$)',
        re.IGNORECASE,
    ),
]

# Anime patterns: [SubGroup] Title - NN
_ANIME_PATTERNS = [
    re.compile(r'\[(?P<release_group>[^\]]+)\]\s*(?P<title>.+?)\s*[-\s]+(?P<episode>\d{1,4})'),
]

# Release group at end: -GroupName or [GroupName]
_RELEASE_GROUP_RE = re.compile(r'[\-\[](?P<release_group>[A-Za-z0-9]+)$')


