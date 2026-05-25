"""
Gentle media metadata probing for local library scans.

The library scanner needs actual stream metadata (audio/subtitle languages,
video codec, runtime, bitrate) but must not stampede the user's disk.  This
module centralizes ffprobe usage behind a process-wide semaphore and an
unchanged-file cache so category scanners can enrich local file observations
without each category inventing its own probing behavior.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from loguru import logger

from src.core.security.command_policy import CommandPolicy

_CACHE_PATH = Path("data/cache/media_probe_cache.json")
_PROBE_TIMEOUT_SECONDS = 15.0
_MAX_CACHE_ENTRIES = 20000
_PROBE_PARSER_VERSION = 3
_probe_semaphore: asyncio.Semaphore | None = None
_cache_lock: asyncio.Lock | None = None
_cache_loaded = False
_cache_dirty = False
_cache: dict[str, dict[str, Any]] = {}
_ffprobe_available: bool | None = None

_ISO_LANGUAGE_MAP: dict[str, str] = {
    "ita": "Italian",
    "it": "Italian",
    "italian": "Italian",
    "eng": "English",
    "en": "English",
    "english": "English",
    "fre": "French",
    "fra": "French",
    "fr": "French",
    "french": "French",
    "ger": "German",
    "deu": "German",
    "de": "German",
    "german": "German",
    "spa": "Spanish",
    "es": "Spanish",
    "spanish": "Spanish",
    "jpn": "Japanese",
    "ja": "Japanese",
    "japanese": "Japanese",
    "kor": "Korean",
    "ko": "Korean",
    "chi": "Chinese",
    "zho": "Chinese",
    "zh": "Chinese",
    "por": "Portuguese",
    "pt": "Portuguese",
    "rus": "Russian",
    "ru": "Russian",
    "pol": "Polish",
    "pl": "Polish",
    "dut": "Dutch",
    "nld": "Dutch",
    "nl": "Dutch",
    "swe": "Swedish",
    "sv": "Swedish",
    "nor": "Norwegian",
    "no": "Norwegian",
    "dan": "Danish",
    "da": "Danish",
    "fin": "Finnish",
    "fi": "Finnish",
}
_UNKNOWN_LANGUAGE_CODES = {"", "und", "unk", "unknown", "none", "mul"}


@dataclass(slots=True)
class AudioTrackInfo:
    """One audio stream extracted from ffprobe metadata."""

    index: int = 0
    language: str = ""
    codec: str = ""
    title: str = ""
    channels: int | None = None


@dataclass(slots=True)
class SubtitleTrackInfo:
    """One subtitle stream extracted from ffprobe metadata."""

    index: int = 0
    language: str = ""
    codec: str = ""
    title: str = ""


@dataclass(slots=True)
class MediaProbeInfo:
    """Cached stream facts for one local media file."""

    path: str = ""
    size_bytes: int = 0
    mtime_ns: int = 0
    duration_seconds: float | None = None
    bit_rate_kbps: int | None = None
    video_codecs: list[str] = field(default_factory=list)
    width: int | None = None
    height: int | None = None
    audio_tracks: list[AudioTrackInfo] = field(default_factory=list)
    subtitle_tracks: list[SubtitleTrackInfo] = field(default_factory=list)
    probed_at: str = ""
    probe_status: str = "ok"

    @property
    def audio_languages(self) -> list[str]:
        """Return unique known audio languages preserving stream order."""
        return _unique(track.language for track in self.audio_tracks if track.language)

    @property
    def subtitle_languages(self) -> list[str]:
        """Return unique known subtitle languages preserving stream order."""
        return _unique(track.language for track in self.subtitle_tracks if track.language)

    @property
    def primary_audio_language(self) -> str:
        """Return the first known audio language, if any."""
        return self.audio_languages[0] if self.audio_languages else ""

    @property
    def video_resolution_label(self) -> str:
        """Return a conventional resolution label from probed video dimensions.

        This is intentionally derived from ffprobe stream width/height, not
        file size. File size plus duration can estimate bitrate, but it cannot
        tell whether a file is 720p, 1080p, or 2160p. Width checks are included
        because many cropped films have heights such as 800px while still being
        1080p releases.
        """
        return resolution_label_from_dimensions(self.width, self.height) or ""

    def language_display(self) -> str:
        """Return a compact display string for all known audio languages."""
        return ", ".join(self.audio_languages)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for cache and canonical library payloads."""
        return {
            "path": self.path,
            "size_bytes": self.size_bytes,
            "mtime_ns": self.mtime_ns,
            "duration_seconds": self.duration_seconds,
            "bit_rate_kbps": self.bit_rate_kbps,
            "video_codecs": list(self.video_codecs),
            "width": self.width,
            "height": self.height,
            "video_width": self.width,
            "video_height": self.height,
            "video_resolution": self.video_resolution_label,
            "resolution_source": "ffprobe_video_stream" if self.video_resolution_label else "",
            "audio_tracks": [asdict(track) for track in self.audio_tracks],
            "audio_languages": self.audio_languages,
            "primary_audio_language": self.primary_audio_language,
            "subtitle_tracks": [asdict(track) for track in self.subtitle_tracks],
            "subtitle_languages": self.subtitle_languages,
            "probed_at": self.probed_at,
            "probe_status": self.probe_status,
            "parser_version": _PROBE_PARSER_VERSION,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MediaProbeInfo":
        """Deserialize cache payloads from older or current shapes."""
        audio_tracks = [
            AudioTrackInfo(
                index=int(row.get("index") or 0),
                language=str(row.get("language") or ""),
                codec=str(row.get("codec") or ""),
                title=str(row.get("title") or ""),
                channels=_safe_int(row.get("channels")),
            )
            for row in list(data.get("audio_tracks") or [])
            if isinstance(row, dict)
        ]
        subtitle_tracks = [
            SubtitleTrackInfo(
                index=int(row.get("index") or 0),
                language=str(row.get("language") or ""),
                codec=str(row.get("codec") or ""),
                title=str(row.get("title") or ""),
            )
            for row in list(data.get("subtitle_tracks") or [])
            if isinstance(row, dict)
        ]
        return cls(
            path=str(data.get("path") or ""),
            size_bytes=int(data.get("size_bytes") or 0),
            mtime_ns=int(data.get("mtime_ns") or 0),
            duration_seconds=_safe_float(data.get("duration_seconds")),
            bit_rate_kbps=_safe_int(data.get("bit_rate_kbps")),
            video_codecs=[str(v) for v in list(data.get("video_codecs") or []) if v],
            width=_safe_int(data.get("width") or data.get("video_width")),
            height=_safe_int(data.get("height") or data.get("video_height")),
            audio_tracks=audio_tracks,
            subtitle_tracks=subtitle_tracks,
            probed_at=str(data.get("probed_at") or ""),
            probe_status=str(data.get("probe_status") or "ok"),
        )


def _get_probe_semaphore() -> asyncio.Semaphore:
    """Return the process-wide probe semaphore.

    One probe at a time is the safe default. ffprobe normally reads headers and
    stream tables, but serializing it avoids a burst of random reads when a fresh
    install scans a large library.
    """
    global _probe_semaphore
    if _probe_semaphore is None:
        _probe_semaphore = asyncio.Semaphore(1)
    return _probe_semaphore


def _get_cache_lock() -> asyncio.Lock:
    """Return the process-wide cache lock."""
    global _cache_lock
    if _cache_lock is None:
        _cache_lock = asyncio.Lock()
    return _cache_lock


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def resolution_label_from_dimensions(width: Any, height: Any) -> str | None:
    """Return a resolution label from video stream dimensions only.

    File size is deliberately ignored here. Size/duration can estimate bitrate;
    it cannot identify video resolution. Width thresholds handle cropped
    widescreen files where ffprobe reports e.g. 1920x800 for a 1080p source.
    """
    w = _safe_int(width) or 0
    h = _safe_int(height) or 0
    if h >= 2000 or w >= 3800:
        return "2160p"
    if h >= 1000 or w >= 1900:
        return "1080p"
    if h >= 700 or w >= 1200:
        return "720p"
    if h >= 400 or w >= 700:
        return "480p"
    return None


def resolution_label_from_probe_payload(probe: dict[str, Any] | None) -> str | None:
    """Return resolution from serialized ffprobe payload dimensions."""
    if not isinstance(probe, dict):
        return None
    return resolution_label_from_dimensions(
        probe.get("width") or probe.get("video_width"),
        probe.get("height") or probe.get("video_height"),
    )


def _language_name(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    if text in _UNKNOWN_LANGUAGE_CODES:
        return ""
    return _ISO_LANGUAGE_MAP.get(text, text.capitalize() if text else "")


def _language_from_tags(tags: dict[str, Any]) -> str:
    """Return a language from ffprobe tags, including loose track-title hints.

    Many local files have audio streams tagged as ``und`` even though the track
    title says things like ``Italian DTS 5.1`` or ``English AAC``.  Filename
    language guesses are not good enough for library ownership, so stream tags
    are parsed more carefully before falling back to unknown.
    """
    direct = _language_name(tags.get("language"))
    if direct:
        return direct
    haystack = " ".join(str(tags.get(key) or "") for key in ("title", "handler_name", "variant_bitrate"))
    lower = haystack.lower()
    # Prefer longer/name tokens before short ISO tokens to avoid accidental hits.
    token_map = [
        ("italian", "Italian"), ("italiano", "Italian"), (" ita", "Italian"), ("ita ", "Italian"),
        ("english", "English"), (" inglese", "English"), (" eng", "English"), ("eng ", "English"),
        ("french", "French"), ("français", "French"), (" francais", "French"),
        ("german", "German"), ("deutsch", "German"),
        ("spanish", "Spanish"), ("español", "Spanish"), ("espanol", "Spanish"),
        ("japanese", "Japanese"), ("jpn", "Japanese"),
        ("korean", "Korean"), ("kor", "Korean"),
        ("chinese", "Chinese"), ("mandarin", "Chinese"), ("cantonese", "Chinese"),
        ("portuguese", "Portuguese"), ("russian", "Russian"), ("polish", "Polish"),
        ("dutch", "Dutch"), ("swedish", "Swedish"), ("norwegian", "Norwegian"),
        ("danish", "Danish"), ("finnish", "Finnish"),
    ]
    padded = f" {lower} "
    for token, language in token_map:
        if token.strip() in {"ita", "eng"}:
            if token in padded:
                return language
        elif token in lower:
            return language
    return ""


def _cached_probe_is_current(cached: dict[str, Any] | None, size_bytes: int, mtime_ns: int) -> bool:
    """Return true when a cached probe can be trusted by this parser version."""
    if not cached:
        return False
    if int(cached.get("size_bytes") or 0) != size_bytes or int(cached.get("mtime_ns") or 0) != mtime_ns:
        return False
    # Round 83 tightened probed video-dimension handling and resolution-source
    # fields. Older cache rows are still stream probes, but rebuilding them lets
    # canonical units reliably prefer ffprobe width/height over filename tags.
    return int(cached.get("parser_version") or 0) >= _PROBE_PARSER_VERSION


def _stat_signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
        return int(stat.st_size), int(stat.st_mtime_ns)
    except OSError:
        return None


async def _load_cache() -> None:
    """Load the media probe cache once per process."""
    global _cache_loaded, _cache
    async with _get_cache_lock():
        if _cache_loaded:
            return
        try:
            if _CACHE_PATH.exists():
                text = await asyncio.to_thread(_CACHE_PATH.read_text, encoding="utf-8")
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    _cache = {str(k): v for k, v in parsed.get("files", parsed).items() if isinstance(v, dict)}
        except Exception as exc:
            logger.debug(f"Failed to load media probe cache: {exc}")
            _cache = {}
        _cache_loaded = True


async def flush_probe_cache() -> None:
    """Persist cache updates in one small write after a scan batch."""
    global _cache_dirty
    async with _get_cache_lock():
        if not _cache_dirty:
            return
        try:
            _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            # Keep cache bounded and biased toward most recently probed entries.
            entries = list(_cache.items())[-_MAX_CACHE_ENTRIES:]
            payload = {
                "schema_version": 1,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "files": dict(entries),
            }
            await asyncio.to_thread(
                _CACHE_PATH.write_text,
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            _cache_dirty = False
        except Exception as exc:
            logger.debug(f"Failed to save media probe cache: {exc}")


async def probe_media_file(path: Path) -> MediaProbeInfo | None:
    """Return stream metadata for one file using a serialized, cached ffprobe.

    The cache key includes absolute path, size, and mtime. If a file is unchanged
    between scans, no subprocess is launched and the disk is not touched beyond a
    cheap stat call.
    """
    await _load_cache()
    normalized = Path(path).expanduser()
    signature = _stat_signature(normalized)
    if signature is None:
        return None
    size_bytes, mtime_ns = signature
    cache_key = str(normalized.resolve(strict=False))

    cached = _cache.get(cache_key)
    if _cached_probe_is_current(cached, size_bytes, mtime_ns):
        return MediaProbeInfo.from_dict(cached)

    global _ffprobe_available
    if _ffprobe_available is False:
        return None

    async with _get_probe_semaphore():
        if _ffprobe_available is False:
            return None
        # Another waiting scan may have populated the cache while we waited.
        cached = _cache.get(cache_key)
        if _cached_probe_is_current(cached, size_bytes, mtime_ns):
            return MediaProbeInfo.from_dict(cached)

        info = await _run_ffprobe(normalized, size_bytes=size_bytes, mtime_ns=mtime_ns)
        if info is not None:
            global _cache_dirty
            _cache[cache_key] = info.to_dict()
            _cache_dirty = True
        return info


async def probe_media_files_serial(paths: Iterable[Path]) -> dict[str, MediaProbeInfo]:
    """Probe files sequentially and flush cache once.

    This helper deliberately awaits each file in order.  It exists so category
    scans do not accidentally create one ffprobe task per file.
    """
    results: dict[str, MediaProbeInfo] = {}
    try:
        for path in paths:
            info = await probe_media_file(path)
            if info is not None:
                results[str(Path(path).resolve(strict=False))] = info
    finally:
        await flush_probe_cache()
    return results


async def _run_ffprobe(path: Path, *, size_bytes: int, mtime_ns: int) -> MediaProbeInfo | None:
    """Launch ffprobe for one file and parse stream metadata."""
    global _ffprobe_available
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await CommandPolicy().create_subprocess_exec(
            cmd,
            purpose="media_probe.ffprobe",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_PROBE_TIMEOUT_SECONDS)
        _ffprobe_available = True
        if proc.returncode != 0:
            return MediaProbeInfo(
                path=str(path),
                size_bytes=size_bytes,
                mtime_ns=mtime_ns,
                probe_status=f"ffprobe_exit_{proc.returncode}",
                probed_at=datetime.now(timezone.utc).isoformat(),
            )
        data = json.loads(stdout or b"{}")
        return _parse_probe_payload(data, path=path, size_bytes=size_bytes, mtime_ns=mtime_ns)
    except asyncio.TimeoutError:
        if proc is not None:
            with contextlib_suppress_process_errors():
                proc.kill()
        logger.warning(f"Media probe timed out for {path.name}; skipping stream metadata for this scan")
        return MediaProbeInfo(
            path=str(path),
            size_bytes=size_bytes,
            mtime_ns=mtime_ns,
            probe_status="timeout",
            probed_at=datetime.now(timezone.utc).isoformat(),
        )
    except FileNotFoundError:
        _ffprobe_available = False
        logger.warning("ffprobe is not installed; stream language extraction is unavailable")
        return None
    except Exception as exc:
        logger.debug(f"Media probe failed for {path.name}: {exc}")
        return MediaProbeInfo(
            path=str(path),
            size_bytes=size_bytes,
            mtime_ns=mtime_ns,
            probe_status="error",
            probed_at=datetime.now(timezone.utc).isoformat(),
        )


class contextlib_suppress_process_errors:
    """Tiny local suppressor to avoid importing contextlib in hot code."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return True


def _parse_probe_payload(data: dict[str, Any], *, path: Path, size_bytes: int, mtime_ns: int) -> MediaProbeInfo:
    format_info = data.get("format") if isinstance(data.get("format"), dict) else {}
    streams = data.get("streams") if isinstance(data.get("streams"), list) else []
    audio_tracks: list[AudioTrackInfo] = []
    subtitle_tracks: list[SubtitleTrackInfo] = []
    video_codecs: list[str] = []
    width: int | None = None
    height: int | None = None
    for stream in streams:
        if not isinstance(stream, dict):
            continue
        stream_type = str(stream.get("codec_type") or "")
        tags = stream.get("tags") if isinstance(stream.get("tags"), dict) else {}
        if stream_type == "audio":
            audio_tracks.append(AudioTrackInfo(
                index=int(stream.get("index") or len(audio_tracks)),
                language=_language_from_tags(tags),
                codec=str(stream.get("codec_name") or ""),
                title=str(tags.get("title") or tags.get("handler_name") or ""),
                channels=_safe_int(stream.get("channels")),
            ))
        elif stream_type == "subtitle":
            subtitle_tracks.append(SubtitleTrackInfo(
                index=int(stream.get("index") or len(subtitle_tracks)),
                language=_language_from_tags(tags),
                codec=str(stream.get("codec_name") or ""),
                title=str(tags.get("title") or tags.get("handler_name") or ""),
            ))
        elif stream_type == "video":
            codec = str(stream.get("codec_name") or "").lower()
            if codec:
                video_codecs.append(codec)
            width = width or _safe_int(stream.get("width"))
            height = height or _safe_int(stream.get("height"))
    bit_rate = _safe_int(format_info.get("bit_rate"))
    return MediaProbeInfo(
        path=str(path),
        size_bytes=size_bytes,
        mtime_ns=mtime_ns,
        duration_seconds=_safe_float(format_info.get("duration")),
        bit_rate_kbps=int(bit_rate / 1000) if bit_rate else None,
        video_codecs=_unique(video_codecs),
        width=width,
        height=height,
        audio_tracks=audio_tracks,
        subtitle_tracks=subtitle_tracks,
        probed_at=datetime.now(timezone.utc).isoformat(),
        probe_status="ok",
    )
