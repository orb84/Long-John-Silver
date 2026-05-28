"""Category-owned helpers for video sidecar files.

External subtitles and similar sidecars must follow the media file when a video
is renamed into the library.  This module is deliberately imported by video
categories instead of hard-coding subtitle rules in the generic download layer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_TEXT_SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa", ".vtt", ".smi"}
_IMAGE_SUBTITLE_EXTENSIONS = {".idx", ".sub"}
_DEFAULT_VIDEO_SIDECAR_EXTENSIONS = _TEXT_SUBTITLE_EXTENSIONS | _IMAGE_SUBTITLE_EXTENSIONS


def plan_video_sidecar_imports(
    *,
    source_path: Path,
    imported_path: Path,
    allowed_extensions: set[str] | None = None,
) -> list[dict[str, str]]:
    """Return source/target plans for sidecars that belong to one video file.

    The matching rule follows the common Plex/Kodi/Emby convention: the
    sidecar's filename must share the video stem, optionally followed by
    language/forced/default/SDH tokens before the subtitle extension.  When the
    video is renamed, the exact sidecar suffix after the original stem is
    preserved, so ``Release.S01E01.en.forced.srt`` becomes
    ``Show - S01E01.en.forced.srt``.
    """
    source = Path(source_path)
    imported = Path(imported_path)
    source_dir = source.parent
    if not source_dir.exists() or not source_dir.is_dir():
        return []
    source_stem = source.stem
    imported_stem = imported.stem
    allowed = {ext.lower() for ext in (allowed_extensions or _DEFAULT_VIDEO_SIDECAR_EXTENSIONS)}
    planned: list[dict[str, str]] = []
    seen_targets: set[str] = set()
    for candidate in sorted(source_dir.iterdir(), key=lambda p: p.name.lower()):
        if not candidate.is_file():
            continue
        if candidate.resolve(strict=False) == source.resolve(strict=False):
            continue
        extension = candidate.suffix.lower()
        if extension not in allowed:
            continue
        tail = _sidecar_tail_for_video_stem(candidate.name, source_stem)
        if not tail:
            continue
        target = imported.with_name(f"{imported_stem}{tail}")
        target_key = str(target.resolve(strict=False)).casefold()
        if target_key in seen_targets:
            continue
        seen_targets.add(target_key)
        planned.append({
            "source": str(candidate),
            "target": str(target),
            "kind": "video_subtitle_sidecar",
            "source_suffix": tail,
        })
    return planned


def _sidecar_tail_for_video_stem(filename: str, video_stem: str) -> str:
    """Return the suffix to preserve after ``video_stem`` or an empty string."""
    if not filename or not video_stem:
        return ""
    if filename == video_stem:
        return ""
    prefix = f"{video_stem}."
    if filename.startswith(prefix):
        return filename[len(video_stem):]
    return ""
