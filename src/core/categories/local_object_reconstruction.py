"""Local object reconstruction for definition-backed library scans.

This module turns neutral file observations into category-owned object evidence.
It intentionally uses lightweight filename/path heuristics only. The output is
not a substitute for provider metadata or LLM disambiguation; it gives the agent
and UI structured local facts such as albums/tracks, audiobook chapters, ebook
formats, and comic archives without leaking those concepts into the scheduler.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from pathlib import Path
from typing import Any

_TRACK_RE = re.compile(r"^(?:(?P<disc>\d+)[-.])?(?P<track>\d{1,3})\s*[-_. ]+\s*(?P<title>.+)$")
_DISC_DIR_RE = re.compile(r"^(?:cd|disc|disk|book|part)\s*(?P<num>\d+)$", re.IGNORECASE)
_CHAPTER_RE = re.compile(r"(?:chapter|ch\.?|track|part|cd)\s*(?P<num>\d{1,4})", re.IGNORECASE)
_EBOOK_FORMATS = {"epub", "pdf", "azw3", "mobi", "djvu", "cbz", "cbr"}
_AUDIOBOOK_FORMATS = {"m4b", "m4a", "mp3", "flac", "aac", "ogg", "opus"}
_MUSIC_FORMATS = {"flac", "alac", "m4a", "mp3", "aac", "ogg", "opus", "wav", "aiff", "ape"}
_COMIC_FORMATS = {"cbz", "cbr"}


@dataclass(frozen=True)
class LocalFileFact:
    """Normalized scan facts for one local file."""

    path: str
    relative_path: str
    filename: str
    stem: str
    extension: str
    size_bytes: int
    quality: str = ""


def scan_local_object(category_id: str, scanned: Any) -> dict[str, Any]:
    """Build a category-owned local object model from scanned file observations."""
    facts = [_fact(file) for file in list(getattr(scanned, "files", []) or [])]
    if category_id == "music":
        return _music_object(scanned, facts)
    if category_id == "audiobooks":
        return _audiobook_object(scanned, facts)
    if category_id == "ebooks":
        return _ebook_object(scanned, facts)
    return _generic_object(scanned, facts)


def enrich_item_payload(category_id: str, payload: dict[str, Any], scanned: Any) -> dict[str, Any]:
    """Attach local object evidence to a category item payload."""
    enriched = dict(payload)
    metadata = dict(enriched.get("metadata") or {})
    properties = dict(enriched.get("properties") or {})
    local = scan_local_object(category_id, scanned)
    metadata["local_object_model"] = local
    metadata["local_model_type"] = local.get("model_type", "")
    properties["local_unit_count"] = len(local.get("files") or local.get("tracks") or local.get("chapters") or [])
    if category_id == "music":
        properties["album_count"] = len(local.get("albums") or [])
        properties["track_count"] = int(local.get("track_count") or 0)
    elif category_id == "audiobooks":
        properties["chapter_count"] = int(local.get("chapter_count") or 0)
        properties["audio_format_count"] = len(local.get("formats") or [])
    elif category_id == "ebooks":
        properties["format_count"] = len(local.get("formats") or [])
        properties["comic_archive_count"] = len([f for f in local.get("files") or [] if f.get("format") in _COMIC_FORMATS])
    enriched["metadata"] = metadata
    enriched["properties"] = properties
    return enriched


def category_units_from_local_object(category_id: str, scanned: Any) -> list[dict[str, Any]] | None:
    """Return rich category units for Music/Ebooks/Audiobooks, or None for generic fallback."""
    facts = [_fact(file) for file in list(getattr(scanned, "files", []) or [])]
    if category_id == "music":
        return _music_units(scanned, facts)
    if category_id == "audiobooks":
        return _audiobook_units(scanned, facts)
    if category_id == "ebooks":
        return _ebook_units(scanned, facts)
    return None


def _fact(scanned_file: Any) -> LocalFileFact:
    probe = dict(getattr(scanned_file, "media_probe", {}) or {})
    local_scan = probe.get("local_scan") if isinstance(probe.get("local_scan"), dict) else {}
    file_path = str(getattr(scanned_file, "file_path", "") or "")
    path = Path(file_path)
    relative = str(local_scan.get("relative_path") or path.name)
    extension = path.suffix.lower().lstrip(".")
    return LocalFileFact(
        path=file_path,
        relative_path=relative,
        filename=path.name,
        stem=path.stem,
        extension=extension,
        size_bytes=int(getattr(scanned_file, "size_bytes", 0) or 0),
        quality=str(getattr(scanned_file, "quality", "") or extension),
    )


def _generic_object(scanned: Any, facts: list[LocalFileFact]) -> dict[str, Any]:
    return {
        "model_type": "local_files",
        "title": str(getattr(scanned, "name", "") or ""),
        "file_count": len(facts),
        "total_size_bytes": sum(f.size_bytes for f in facts),
        "formats": sorted({f.extension for f in facts if f.extension}),
        "files": [_file_payload(f) for f in facts],
    }


def _music_object(scanned: Any, facts: list[LocalFileFact]) -> dict[str, Any]:
    albums: dict[str, dict[str, Any]] = {}
    tracks: list[dict[str, Any]] = []
    for index, fact in enumerate([f for f in facts if f.extension in _MUSIC_FORMATS], start=1):
        album = _music_album_name(fact, scanned)
        parsed = _parse_track(fact)
        track = {
            "unit_key": _unit_key("track", fact),
            "title": parsed["title"],
            "artist": str(getattr(scanned, "name", "") or ""),
            "album": album,
            "disc_number": parsed.get("disc_number"),
            "track_number": parsed.get("track_number") or index,
            "format": fact.extension,
            "file_path": fact.path,
            "relative_path": fact.relative_path,
            "size_bytes": fact.size_bytes,
        }
        tracks.append(track)
        bucket = albums.setdefault(album, {"title": album, "tracks": [], "formats": set(), "disc_numbers": set()})
        bucket["tracks"].append(track)
        bucket["formats"].add(fact.extension)
        if track.get("disc_number"):
            bucket["disc_numbers"].add(track["disc_number"])
    album_payloads = []
    for album in albums.values():
        album_tracks = sorted(album["tracks"], key=lambda t: (int(t.get("disc_number") or 1), int(t.get("track_number") or 0), t.get("title") or ""))
        album_payloads.append({
            "title": album["title"],
            "track_count": len(album_tracks),
            "disc_count": len(album["disc_numbers"]) or None,
            "formats": sorted(album["formats"]),
            "tracks": album_tracks,
        })
    return {
        "model_type": "local_music_catalog",
        "artist_or_catalog": str(getattr(scanned, "name", "") or ""),
        "album_count": len(album_payloads),
        "track_count": len(tracks),
        "formats": sorted({f.extension for f in facts if f.extension in _MUSIC_FORMATS}),
        "albums": sorted(album_payloads, key=lambda a: str(a.get("title") or "").lower()),
    }


def _audiobook_object(scanned: Any, facts: list[LocalFileFact]) -> dict[str, Any]:
    audio = [f for f in facts if f.extension in _AUDIOBOOK_FORMATS]
    chapters = []
    for index, fact in enumerate(audio, start=1):
        chapter_index = _chapter_number(fact) or index
        chapters.append({
            "unit_key": _unit_key("chapter", fact),
            "title": _clean_stem(fact.stem),
            "chapter_index": chapter_index,
            "format": fact.extension,
            "file_path": fact.path,
            "relative_path": fact.relative_path,
            "size_bytes": fact.size_bytes,
        })
    has_single_m4b = len(audio) == 1 and audio[0].extension == "m4b"
    return {
        "model_type": "local_audiobook_edition",
        "title": str(getattr(scanned, "name", "") or ""),
        "formats": sorted({f.extension for f in audio}),
        "chapter_count": 1 if has_single_m4b else len(chapters),
        "has_chapter_files": len(chapters) > 1,
        "single_chaptered_file": has_single_m4b,
        "chapters": sorted(chapters, key=lambda c: (int(c.get("chapter_index") or 0), c.get("title") or "")),
    }


def _ebook_object(scanned: Any, facts: list[LocalFileFact]) -> dict[str, Any]:
    files = []
    for fact in [f for f in facts if f.extension in _EBOOK_FORMATS]:
        files.append({
            "unit_key": _unit_key("edition_file", fact),
            "title": _clean_stem(fact.stem),
            "format": fact.extension,
            "file_path": fact.path,
            "relative_path": fact.relative_path,
            "size_bytes": fact.size_bytes,
            "is_comic_archive": fact.extension in _COMIC_FORMATS,
        })
    return {
        "model_type": "local_ebook_edition_set",
        "title": str(getattr(scanned, "name", "") or ""),
        "formats": sorted({f["format"] for f in files}),
        "has_comic_archives": any(f["is_comic_archive"] for f in files),
        "files": sorted(files, key=lambda f: (str(f.get("title") or "").lower(), str(f.get("format") or ""))),
    }


def _music_units(scanned: Any, facts: list[LocalFileFact]) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    for index, fact in enumerate([f for f in facts if f.extension in _MUSIC_FORMATS], start=1):
        parsed = _parse_track(fact)
        album = _music_album_name(fact, scanned)
        unit = {
            "unit_key": _unit_key("track", fact),
            "unit_type": "track",
            "display_name": parsed["title"],
            "status": "downloaded",
            "file_path": fact.path,
            "size_bytes": fact.size_bytes,
            "quality": fact.quality,
            "sort_index": int(parsed.get("track_number") or index),
            "properties": {
                "artist": str(getattr(scanned, "name", "") or ""),
                "album": album,
                "disc_number": parsed.get("disc_number"),
                "track_number": parsed.get("track_number"),
                "format": fact.extension,
            },
            "metadata": {"relative_path": fact.relative_path, "local_model_type": "track"},
        }
        units.append(unit)
    return sorted(units, key=lambda u: (str((u.get("properties") or {}).get("album") or "").lower(), int((u.get("properties") or {}).get("disc_number") or 1), int((u.get("properties") or {}).get("track_number") or u.get("sort_index") or 0)))


def _audiobook_units(scanned: Any, facts: list[LocalFileFact]) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    audio = [f for f in facts if f.extension in _AUDIOBOOK_FORMATS]
    for index, fact in enumerate(audio, start=1):
        chapter_index = _chapter_number(fact) or index
        unit_type = "audiobook_file" if len(audio) == 1 else "chapter"
        units.append({
            "unit_key": _unit_key(unit_type, fact),
            "unit_type": unit_type,
            "display_name": _clean_stem(fact.stem),
            "status": "downloaded",
            "file_path": fact.path,
            "size_bytes": fact.size_bytes,
            "quality": fact.quality,
            "sort_index": chapter_index,
            "properties": {"chapter_index": chapter_index, "format": fact.extension},
            "metadata": {"relative_path": fact.relative_path, "local_model_type": unit_type},
        })
    return sorted(units, key=lambda u: int(u.get("sort_index") or 0))


def _ebook_units(scanned: Any, facts: list[LocalFileFact]) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    for index, fact in enumerate([f for f in facts if f.extension in _EBOOK_FORMATS], start=1):
        unit_type = "comic_archive" if fact.extension in _COMIC_FORMATS else "ebook_file"
        units.append({
            "unit_key": _unit_key(unit_type, fact),
            "unit_type": unit_type,
            "display_name": _clean_stem(fact.stem),
            "status": "downloaded",
            "file_path": fact.path,
            "size_bytes": fact.size_bytes,
            "quality": fact.quality,
            "sort_index": index,
            "properties": {"format": fact.extension, "is_comic_archive": fact.extension in _COMIC_FORMATS},
            "metadata": {"relative_path": fact.relative_path, "local_model_type": unit_type},
        })
    return units


def _music_album_name(fact: LocalFileFact, scanned: Any) -> str:
    parts = Path(fact.relative_path).parts
    if len(parts) >= 2:
        parent = parts[-2]
        if not _DISC_DIR_RE.match(parent) and parent.lower() not in {"cd1", "cd2", "disc1", "disc2"}:
            return _clean_stem(parent)
    return _clean_stem(str(getattr(scanned, "name", "") or "Album"))


def _parse_track(fact: LocalFileFact) -> dict[str, Any]:
    stem = fact.stem
    match = _TRACK_RE.match(stem)
    disc_number = None
    track_number = None
    title = stem
    if match:
        disc_number = int(match.group("disc")) if match.group("disc") else None
        track_number = int(match.group("track")) if match.group("track") else None
        title = match.group("title") or stem
    for part in Path(fact.relative_path).parts[:-1]:
        disc = _DISC_DIR_RE.match(part)
        if disc:
            disc_number = disc_number or int(disc.group("num"))
    return {"title": _clean_stem(title), "disc_number": disc_number, "track_number": track_number}


def _chapter_number(fact: LocalFileFact) -> int | None:
    for text in [fact.stem, *Path(fact.relative_path).parts[:-1]]:
        match = _CHAPTER_RE.search(text)
        if match:
            return int(match.group("num"))
    parsed = _parse_track(fact)
    return parsed.get("track_number")


def _clean_stem(value: str) -> str:
    text = re.sub(r"[_\.]+", " ", str(value or "")).strip()
    text = re.sub(r"\s+", " ", text)
    return text or "Untitled"


def _file_payload(fact: LocalFileFact) -> dict[str, Any]:
    return {
        "file_path": fact.path,
        "relative_path": fact.relative_path,
        "format": fact.extension,
        "size_bytes": fact.size_bytes,
    }


def _unit_key(prefix: str, fact: LocalFileFact) -> str:
    digest = hashlib.sha1((fact.relative_path or fact.path).encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"{prefix}:{digest}"
