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
from typing import Any, Protocol

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


class LocalObjectBuilder(Protocol):
    """Protocol implemented by category-local reconstruction builders."""

    def build_object(self, scanned: Any, facts: list[LocalFileFact]) -> dict[str, Any]:
        """Build category-local object evidence."""

    def build_units(self, scanned: Any, facts: list[LocalFileFact]) -> list[dict[str, Any]] | None:
        """Build category-local unit rows, or None for generic fallback."""

    def enrich_properties(self, properties: dict[str, Any], local: dict[str, Any]) -> None:
        """Attach builder-owned compact counters to item properties."""


class LocalObjectText:
    """Shared filename text helpers for local object reconstruction."""

    @staticmethod
    def clean_stem(value: str) -> str:
        """Return a display-safe stem from a filename or folder fragment."""
        text = re.sub(r"[_\.]+", " ", str(value or "")).strip()
        text = re.sub(r"\s+", " ", text)
        return text or "Untitled"

    @staticmethod
    def unit_key(prefix: str, fact: LocalFileFact) -> str:
        """Return a stable file-oriented unit key for a scanned fact."""
        digest = hashlib.sha1((fact.relative_path or fact.path).encode("utf-8", errors="ignore")).hexdigest()[:12]
        return f"{prefix}:{digest}"

    @staticmethod
    def file_payload(fact: LocalFileFact) -> dict[str, Any]:
        """Return generic file evidence for canonical local object payloads."""
        return {
            "file_path": fact.path,
            "relative_path": fact.relative_path,
            "format": fact.extension,
            "size_bytes": fact.size_bytes,
        }


class LocalFileFactExtractor:
    """Extract neutral file facts from scanner observations."""

    @classmethod
    def facts(cls, scanned: Any) -> list[LocalFileFact]:
        """Return normalized facts for all files on a scanned item."""
        return [cls.fact(file) for file in list(getattr(scanned, "files", []) or [])]

    @staticmethod
    def fact(scanned_file: Any) -> LocalFileFact:
        """Return normalized facts for one scanner file observation."""
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


class GenericLocalObjectBuilder:
    """Build generic local-file evidence for definition-backed categories."""

    def enrich_properties(self, properties: dict[str, Any], local: dict[str, Any]) -> None:
        """Attach no category-specific counters for generic local files."""
        _ = (properties, local)

    def build_object(self, scanned: Any, facts: list[LocalFileFact]) -> dict[str, Any]:
        """Return a neutral local-files object model."""
        return {
            "model_type": "local_files",
            "title": str(getattr(scanned, "name", "") or ""),
            "file_count": len(facts),
            "total_size_bytes": sum(f.size_bytes for f in facts),
            "formats": sorted({f.extension for f in facts if f.extension}),
            "files": [LocalObjectText.file_payload(f) for f in facts],
        }

    def build_units(self, scanned: Any, facts: list[LocalFileFact]) -> list[dict[str, Any]] | None:
        """Return None so the base category can build generic units."""
        _ = (scanned, facts)
        return None


class MusicLocalObjectBuilder:
    """Build local album/track evidence for the Music definition."""

    def enrich_properties(self, properties: dict[str, Any], local: dict[str, Any]) -> None:
        """Attach music-local counters to item properties."""
        properties["album_count"] = len(local.get("albums") or [])
        properties["track_count"] = int(local.get("track_count") or 0)

    def build_object(self, scanned: Any, facts: list[LocalFileFact]) -> dict[str, Any]:
        """Return a local music catalog object model."""
        albums: dict[str, dict[str, Any]] = {}
        tracks: list[dict[str, Any]] = []
        for index, fact in enumerate([f for f in facts if f.extension in _MUSIC_FORMATS], start=1):
            album = self._album_name(fact, scanned)
            parsed = self._parse_track(fact)
            track = {
                "unit_key": LocalObjectText.unit_key("track", fact),
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
        return self._catalog_payload(scanned, facts, albums, tracks)

    def build_units(self, scanned: Any, facts: list[LocalFileFact]) -> list[dict[str, Any]]:
        """Return one downloaded unit per local music track."""
        units: list[dict[str, Any]] = []
        for index, fact in enumerate([f for f in facts if f.extension in _MUSIC_FORMATS], start=1):
            parsed = self._parse_track(fact)
            units.append({
                "unit_key": LocalObjectText.unit_key("track", fact),
                "unit_type": "track",
                "display_name": parsed["title"],
                "status": "downloaded",
                "file_path": fact.path,
                "size_bytes": fact.size_bytes,
                "quality": fact.quality,
                "sort_index": int(parsed.get("track_number") or index),
                "properties": {
                    "artist": str(getattr(scanned, "name", "") or ""),
                    "album": self._album_name(fact, scanned),
                    "disc_number": parsed.get("disc_number"),
                    "track_number": parsed.get("track_number"),
                    "format": fact.extension,
                },
                "metadata": {"relative_path": fact.relative_path, "local_model_type": "track"},
            })
        return sorted(units, key=self._unit_sort_key)

    @staticmethod
    def _catalog_payload(scanned: Any, facts: list[LocalFileFact], albums: dict[str, dict[str, Any]], tracks: list[dict[str, Any]]) -> dict[str, Any]:
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

    @staticmethod
    def _unit_sort_key(unit: dict[str, Any]) -> tuple[str, int, int]:
        props = unit.get("properties") or {}
        return (
            str(props.get("album") or "").lower(),
            int(props.get("disc_number") or 1),
            int(props.get("track_number") or unit.get("sort_index") or 0),
        )

    @staticmethod
    def _album_name(fact: LocalFileFact, scanned: Any) -> str:
        parts = Path(fact.relative_path).parts
        if len(parts) >= 2:
            parent = parts[-2]
            if not _DISC_DIR_RE.match(parent) and parent.lower() not in {"cd1", "cd2", "disc1", "disc2"}:
                return LocalObjectText.clean_stem(parent)
        return LocalObjectText.clean_stem(str(getattr(scanned, "name", "") or "Album"))

    @classmethod
    def _parse_track(cls, fact: LocalFileFact) -> dict[str, Any]:
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
        return {"title": LocalObjectText.clean_stem(title), "disc_number": disc_number, "track_number": track_number}


class AudiobookLocalObjectBuilder:
    """Build local chapter/file evidence for Audiobooks."""

    def enrich_properties(self, properties: dict[str, Any], local: dict[str, Any]) -> None:
        """Attach audiobook-local counters to item properties."""
        properties["chapter_count"] = int(local.get("chapter_count") or 0)
        properties["audio_format_count"] = len(local.get("formats") or [])

    def build_object(self, scanned: Any, facts: list[LocalFileFact]) -> dict[str, Any]:
        """Return a local audiobook edition object model."""
        audio = [f for f in facts if f.extension in _AUDIOBOOK_FORMATS]
        chapters = []
        for index, fact in enumerate(audio, start=1):
            chapter_index = self._chapter_number(fact) or index
            chapters.append({
                "unit_key": LocalObjectText.unit_key("chapter", fact),
                "title": LocalObjectText.clean_stem(fact.stem),
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

    def build_units(self, scanned: Any, facts: list[LocalFileFact]) -> list[dict[str, Any]]:
        """Return one downloaded unit per audiobook file/chapter."""
        _ = scanned
        units: list[dict[str, Any]] = []
        audio = [f for f in facts if f.extension in _AUDIOBOOK_FORMATS]
        for index, fact in enumerate(audio, start=1):
            chapter_index = self._chapter_number(fact) or index
            unit_type = "audiobook_file" if len(audio) == 1 else "chapter"
            units.append({
                "unit_key": LocalObjectText.unit_key(unit_type, fact),
                "unit_type": unit_type,
                "display_name": LocalObjectText.clean_stem(fact.stem),
                "status": "downloaded",
                "file_path": fact.path,
                "size_bytes": fact.size_bytes,
                "quality": fact.quality,
                "sort_index": chapter_index,
                "properties": {"chapter_index": chapter_index, "format": fact.extension},
                "metadata": {"relative_path": fact.relative_path, "local_model_type": unit_type},
            })
        return sorted(units, key=lambda u: int(u.get("sort_index") or 0))

    @staticmethod
    def _chapter_number(fact: LocalFileFact) -> int | None:
        for text in [fact.stem, *Path(fact.relative_path).parts[:-1]]:
            match = _CHAPTER_RE.search(text)
            if match:
                return int(match.group("num"))
        return MusicLocalObjectBuilder._parse_track(fact).get("track_number")


class EbookLocalObjectBuilder:
    """Build local edition/file evidence for Ebooks."""

    def enrich_properties(self, properties: dict[str, Any], local: dict[str, Any]) -> None:
        """Attach ebook-local counters to item properties."""
        properties["format_count"] = len(local.get("formats") or [])
        properties["comic_archive_count"] = len([f for f in local.get("files") or [] if f.get("format") in _COMIC_FORMATS])

    def build_object(self, scanned: Any, facts: list[LocalFileFact]) -> dict[str, Any]:
        """Return a local ebook edition-set object model."""
        files = []
        for fact in [f for f in facts if f.extension in _EBOOK_FORMATS]:
            files.append({
                "unit_key": LocalObjectText.unit_key("edition_file", fact),
                "title": LocalObjectText.clean_stem(fact.stem),
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

    def build_units(self, scanned: Any, facts: list[LocalFileFact]) -> list[dict[str, Any]]:
        """Return one downloaded unit per ebook/comic archive file."""
        _ = scanned
        units: list[dict[str, Any]] = []
        for index, fact in enumerate([f for f in facts if f.extension in _EBOOK_FORMATS], start=1):
            unit_type = "comic_archive" if fact.extension in _COMIC_FORMATS else "ebook_file"
            units.append({
                "unit_key": LocalObjectText.unit_key(unit_type, fact),
                "unit_type": unit_type,
                "display_name": LocalObjectText.clean_stem(fact.stem),
                "status": "downloaded",
                "file_path": fact.path,
                "size_bytes": fact.size_bytes,
                "quality": fact.quality,
                "sort_index": index,
                "properties": {"format": fact.extension, "is_comic_archive": fact.extension in _COMIC_FORMATS},
                "metadata": {"relative_path": fact.relative_path, "local_model_type": unit_type},
            })
        return units


class LocalObjectReconstructor:
    """Coordinate category-owned local object reconstruction builders."""

    _BUILDERS: dict[str, LocalObjectBuilder] = {
        "music": MusicLocalObjectBuilder(),
        "audiobooks": AudiobookLocalObjectBuilder(),
        "ebooks": EbookLocalObjectBuilder(),
    }
    _GENERIC_BUILDER = GenericLocalObjectBuilder()

    @classmethod
    def scan(cls, category_id: str, scanned: Any) -> dict[str, Any]:
        """Build a category-owned local object model from scanned file observations."""
        facts = LocalFileFactExtractor.facts(scanned)
        return cls._builder(category_id).build_object(scanned, facts)

    @classmethod
    def enrich_item_payload(cls, category_id: str, payload: dict[str, Any], scanned: Any) -> dict[str, Any]:
        """Attach local object evidence to a category item payload."""
        enriched = dict(payload)
        metadata = dict(enriched.get("metadata") or {})
        properties = dict(enriched.get("properties") or {})
        local = cls.scan(category_id, scanned)
        metadata["local_object_model"] = local
        metadata["local_model_type"] = local.get("model_type", "")
        properties["local_unit_count"] = len(local.get("files") or local.get("tracks") or local.get("chapters") or [])
        cls._builder(category_id).enrich_properties(properties, local)
        enriched["metadata"] = metadata
        enriched["properties"] = properties
        return enriched

    @classmethod
    def category_units(cls, category_id: str, scanned: Any) -> list[dict[str, Any]] | None:
        """Return rich category units for known local models, or None for generic fallback."""
        facts = LocalFileFactExtractor.facts(scanned)
        return cls._builder(category_id).build_units(scanned, facts)

    @classmethod
    def _builder(cls, category_id: str) -> LocalObjectBuilder:
        """Return the builder registered for a definition-backed category id."""
        return cls._BUILDERS.get(category_id, cls._GENERIC_BUILDER)

