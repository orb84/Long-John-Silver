"""Category item identity helpers.

These helpers remove path/template artefacts such as ``(None)`` without
encoding TV/movie-specific semantics.  They are intentionally tiny and safe to
use from scanners, path planners, schedulers, and download import code.
"""

from __future__ import annotations

import re

_NONE_TOKEN_RE = re.compile(r"\s*[\[(]\s*(?:none|null|undefined|unknown|n/?a)\s*[\])]\s*", re.IGNORECASE)
_EMPTY_BRACKETS_RE = re.compile(r"\s*[\[(]\s*[\])]\s*")
_SPACES_RE = re.compile(r"\s+")
_KEY_RE = re.compile(r"[^a-z0-9]+")
_PATH_SEP_RE = re.compile(r"[\\/]+")
# Keep path generation portable across Linux, macOS, and Windows.  Even when
# running on POSIX, LJS may be planning library paths for files discovered from
# Windows-style remote paths, and Windows rejects these characters outright.
_INVALID_PATH_SEGMENT_CHARS_RE = re.compile(r'[<>:"\\|?*\x00-\x1f]+')
_RESERVED_WINDOWS_BASENAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

_VIDEO_EXT_RE = re.compile(r"\.(?:mkv|mp4|avi|m4v|mov|mpg|mpeg|wmv|webm)$", re.IGNORECASE)
_CAMEL_BOUNDARY_1_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_CAMEL_BOUNDARY_2_RE = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")
_YEAR_RE = re.compile(r"(?:^|[^0-9])(19\d{2}|20\d{2})(?:[^0-9]|$)")
_TV_MARKER_RE = re.compile(
    r"(?P<title>.+?)(?:[\s._\-]+|\b)(?:S\d{1,2}\s*E\d{1,3}(?:\s*(?:-|E)\s*E?\d{1,3})?|S\d{1,2}\s*[-–]\s*\d{1,2}|S\d{1,2}\b|\d{1,2}x\d{1,3}|Season\s*\d{1,2}(?:\s*(?:-|to|through)\s*\d{1,2})?)\b",
    re.IGNORECASE,
)
_RELEASE_MARKER_RE = re.compile(
    r"\b(?:19\d{2}|20\d{2}|2160p|1080p|720p|480p|4k|uhd|hdr|dv|web\s*[- ]?dl|webrip|web|bluray|brrip|bdrip|dvdrip|hdtv|remux|dlmux|mux|x264|x265|h264|h265|hevc|av1|xvid|divx|ddp?5\s*1|dd5\s*1|dts|aac|ac3|truehd|atmos|ita|italian|italiano|eng|english|spa|spanish|sub|subs|subbed|multi|dual|mkv|mp4|m4v|avi)\b",
    re.IGNORECASE,
)
_TRAILING_RELEASE_GROUP_RE = re.compile(r"\s+(?:by)?[A-Za-z0-9]{2,18}$")


def split_camel_title(value: object) -> str:
    """Insert spaces into compact CamelCase/PascalCase title strings."""
    text = "" if value is None else str(value)
    # Do not touch strings that already have separators between words.
    text = _CAMEL_BOUNDARY_2_RE.sub(" ", text)
    text = _CAMEL_BOUNDARY_1_RE.sub(" ", text)
    return text


def extract_release_year(value: object) -> int | None:
    """Extract a plausible release/start year from a folder or file name."""
    text = "" if value is None else str(value)
    match = _YEAR_RE.search(text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def clean_release_title(value: object, *, fallback: str = "Unknown", media_hint: str | None = None) -> str:
    """Return a clean display title from a dirty release/folder name.

    Library folders are often named as torrent releases, for example
    ``Silicon.Valley.S01-06.ITA.DLMUX.x264-mkeagle3``.  This helper strips
    technical release markers before library scan, metadata lookup, and UI
    presentation so the user sees and searches for the media title, not the
    release payload name.  It remains conservative: when no reliable release
    markers are present, it only normalizes separators and CamelCase.
    """
    raw = clean_display_title(value, fallback="")
    if not raw:
        return fallback
    raw = _VIDEO_EXT_RE.sub("", raw)
    normalized = raw.replace(".", " ").replace("_", " ").replace("+", " ")
    normalized = split_camel_title(normalized)
    normalized = re.sub(r"[\[\]{}]+", " ", normalized)
    normalized = _SPACES_RE.sub(" ", normalized).strip(" ._-")

    truncated_at_marker = False
    # TV releases have the most reliable delimiter: the first season/episode
    # marker generally starts the release metadata, not the title.
    if str(media_hint or "").lower() in {"tv", "show", "series", "episodic"}:
        match = _TV_MARKER_RE.search(normalized)
        if match and match.start("title") <= 1:
            normalized = match.group("title")
            truncated_at_marker = True
        else:
            match = _TV_MARKER_RE.search(normalized)
            if match and match.start() > 1:
                normalized = normalized[:match.start()]
                truncated_at_marker = True
    else:
        # Movies and unknown media: cut at the first strong release marker.
        marker = _RELEASE_MARKER_RE.search(normalized)
        if marker and marker.start() > 2:
            normalized = normalized[:marker.start()]
            truncated_at_marker = True

    normalized = re.sub(r"\b(?:complete|proper|repack|rerip|extended|unrated|internal)\b", " ", normalized, flags=re.IGNORECASE)
    normalized = _TRAILING_RELEASE_GROUP_RE.sub("", normalized) if _RELEASE_MARKER_RE.search(raw) and not truncated_at_marker else normalized
    normalized = _SPACES_RE.sub(" ", normalized).strip(" ._-")
    return clean_display_title(normalized, fallback=fallback)


def looks_like_dirty_release_title(value: object) -> bool:
    """Return True when a display title still appears to be a release name."""
    text = "" if value is None else str(value)
    if not text:
        return False
    return bool(
        _VIDEO_EXT_RE.search(text)
        or _TV_MARKER_RE.search(text.replace(".", " ").replace("_", " "))
        or _RELEASE_MARKER_RE.search(text.replace(".", " ").replace("_", " "))
        or re.search(r"[a-z][A-Z]", text)
        or "." in text
        or "_" in text
    )


def clean_display_title(value: object, fallback: str = "Unknown") -> str:
    """Return a user/library-safe display title.

    ``str(None)`` and naming templates with ``({year})`` often produce folder
    names like ``For All Mankind (None)``.  This function strips those sentinel
    artefacts while preserving real titles and years.
    """
    text = "" if value is None else str(value)
    text = text.replace("{year}", "").replace("{episode_title}", "")
    text = _NONE_TOKEN_RE.sub(" ", text)
    text = _EMPTY_BRACKETS_RE.sub(" ", text)
    text = _SPACES_RE.sub(" ", text).strip(" ._-")
    return text or fallback


def canonical_item_key(value: object) -> str:
    """Return a loose comparison key for category item names."""
    cleaned = clean_display_title(value, fallback="")
    return _KEY_RE.sub(" ", cleaned.lower()).strip()


def clean_path_segment(value: object, fallback: str = "Unknown") -> str:
    """Return one portable filesystem path segment.

    This deliberately applies the Windows-invalid character set on every
    platform.  Otherwise a library path that works on Linux/macOS can fail on
    Windows, or a Windows-style remote/Soulseek name can create different
    directory layouts depending on the host OS.
    """
    text = clean_display_title(value, fallback="")
    text = _PATH_SEP_RE.sub(" ", text)
    text = _INVALID_PATH_SEGMENT_CHARS_RE.sub(" ", text)
    text = re.sub(r"\s+([.])", r"\1", text)
    text = _SPACES_RE.sub(" ", text).strip(" ._-")
    if not text or text in {".", ".."}:
        return fallback
    # Windows treats reserved device basenames as invalid even with extensions
    # such as CON.mp3.  Prefix instead of dropping the user-visible label.
    basename = text.split(".", 1)[0].upper()
    if basename in _RESERVED_WINDOWS_BASENAMES:
        text = f"_{text}"
    return text or fallback


def clean_path_fragment(value: object, fallback: str = "Unknown") -> str:
    """Clean a formatted relative path fragment after template substitution.

    Forward slashes in category templates remain intentional hierarchy
    separators.  Backslashes are normalized to the same separator before each
    component is sanitized, preventing host-dependent behavior where POSIX would
    keep a backslash inside a filename while Windows would treat it as a directory.
    """
    text = clean_display_title(value, fallback=fallback).replace("\\", "/")
    parts = [clean_path_segment(part, fallback="") for part in text.split("/")]
    return "/".join(part for part in parts if part) or fallback


def basename_from_pathish(value: object, fallback: str = "file") -> str:
    """Return a portable basename from a POSIX, Windows, or remote path string."""
    text = "" if value is None else str(value)
    text = text.replace("\\", "/").strip()
    while text.endswith("/"):
        text = text[:-1]
    name = text.rsplit("/", 1)[-1] if text else ""
    if name.endswith(".downloading"):
        name = name[:-12]
    return clean_path_segment(name, fallback=fallback)


def clean_category_item_name(value: object, category_id: str | None = None, *, fallback: str = "Unknown") -> str:
    """Return the canonical display item name for a category scan/settings key.

    This is stronger than :func:`clean_display_title`: it also strips release
    payloads such as ``S01-06.ITA.DLMUX.x264`` for TV and movie categories.
    The helper is intentionally conservative for normal titles: if there are no
    release markers, the title survives unchanged apart from whitespace/CamelCase
    cleanup.  It gives scanners, settings migration, and DB reconciliation the
    same identity key so dirty auto-discovered folders do not create
    ``TRACKED``/``MISSING_FROM_LIBRARY`` duplicates.
    """
    cleaned = clean_display_title(value, fallback="")
    if not cleaned:
        return fallback
    category = str(category_id or "").lower()
    if category in {"tv", "show", "series", "episodic"}:
        return clean_release_title(cleaned, fallback=cleaned, media_hint="tv")
    if category in {"movie", "movies", "film", "feature"}:
        return clean_release_title(cleaned, fallback=cleaned, media_hint="movie")
    if looks_like_dirty_release_title(cleaned):
        return clean_release_title(cleaned, fallback=cleaned, media_hint=None)
    return clean_display_title(cleaned, fallback=fallback)
