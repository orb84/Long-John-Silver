"""Round 83 scenario traces for media resolution/bitrate provenance.

These are intentionally lightweight code-path tests: they instantiate the TV and
movie category canonical unit builders with fake scanned observations. They
verify that local-file resolution comes from ffprobe video dimensions first and
that file-size estimates are used only for bitrate fallback, never resolution.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from src.core.categories.media_probe import resolution_label_from_probe_payload
from src.core.categories.movie import MovieCategory
from src.core.categories.tv import TvShowCategory


def _obs(**kwargs):
    defaults = {
        "season": 0,
        "episode": 0,
        "file_path": "",
        "quality": "",
        "size_bytes": 0,
        "media_probe": {},
        "audio_languages": [],
        "audio_tracks": [],
        "subtitle_languages": [],
        "subtitle_tracks": [],
        "detected_language": "",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _scanned(name: str, files: list[object]):
    return SimpleNamespace(
        name=name,
        files=files,
        detected_language="",
        detected_languages=[],
        subtitle_languages=[],
    )


def assert_resolution_labels_from_dimensions() -> None:
    assert resolution_label_from_probe_payload({"width": 3840, "height": 1600}) == "2160p"
    assert resolution_label_from_probe_payload({"width": 1920, "height": 800}) == "1080p"
    assert resolution_label_from_probe_payload({"width": 1280, "height": 536}) == "720p"
    assert resolution_label_from_probe_payload({"width": None, "height": None}) is None


def assert_tv_prefers_ffprobe_resolution_over_filename() -> None:
    cat = TvShowCategory()
    units = cat.library_units_from_scan(_scanned("Example Show", [
        _obs(
            season=1,
            episode=1,
            file_path="/library/Example Show/Example.Show.S01E01.720p.WEB.mkv",
            quality="720p WEB",
            size_bytes=2_500_000_000,
            media_probe={
                "width": 1920,
                "height": 1080,
                "bit_rate_kbps": 5420,
                "video_codecs": ["hevc"],
                "audio_languages": ["Italian", "English"],
                "audio_tracks": [{"index": 1, "language": "Italian"}, {"index": 2, "language": "English"}],
            },
        )
    ]))
    unit = units[0]
    assert unit["resolution"] == "1080p", unit
    assert unit["resolution_source"] == "ffprobe_video_stream", unit
    assert unit["video_width"] == 1920 and unit["video_height"] == 1080, unit
    assert unit["estimated_bitrate_kbps"] == 5420, unit
    assert unit["bitrate_source"] == "ffprobe_format", unit
    assert unit["audio_languages"] == ["Italian", "English"], unit


def assert_size_only_never_creates_resolution() -> None:
    cat = TvShowCategory()
    units = cat.library_units_from_scan(_scanned("Mystery Show", [
        _obs(
            season=1,
            episode=2,
            file_path="/library/Mystery Show/02.mkv",
            quality="unknown",
            size_bytes=8_000_000_000,
            media_probe={},
        )
    ]))
    unit = units[0]
    assert unit["resolution"] is None, unit
    assert unit["resolution_source"] == "", unit
    assert unit["estimated_bitrate_kbps"] and unit["estimated_bitrate_kbps"] > 0, unit
    assert unit["bitrate_source"] == "size_duration_estimate", unit


def assert_movie_prefers_ffprobe_resolution_over_filename() -> None:
    cat = MovieCategory()
    units = cat.library_units_from_scan(_scanned("Example Movie", [
        _obs(
            file_path="/library/Example Movie/Example.Movie.2025.720p.mkv",
            quality="720p",
            size_bytes=14_000_000_000,
            media_probe={
                "width": 3840,
                "height": 1600,
                "bit_rate_kbps": 18200,
                "video_codecs": ["hevc"],
                "audio_languages": ["English"],
            },
        )
    ]))
    unit = units[0]
    assert unit["resolution"] == "2160p", unit
    assert unit["resolution_source"] == "ffprobe_video_stream", unit
    assert unit["estimated_bitrate_kbps"] == 18200, unit
    assert unit["bitrate_source"] == "ffprobe_format", unit


def main() -> None:
    assert_resolution_labels_from_dimensions()
    assert_tv_prefers_ffprobe_resolution_over_filename()
    assert_size_only_never_creates_resolution()
    assert_movie_prefers_ffprobe_resolution_over_filename()
    print("Round 83 media resolution/bitrate trace tests passed")


if __name__ == "__main__":
    main()
