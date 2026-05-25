"""Round 82 regression traces for media metadata and Discord bridge repairs.

These are lightweight code-path simulations that avoid touching real media files
or Discord. They exercise the exact transformations that regressed: stream-title
language parsing, cache invalidation, canonical TV unit metadata preservation,
and Discord channel/package handling.
"""

from __future__ import annotations

from pathlib import Path
import sys
import types

assistant_stub = types.ModuleType("src.ai.assistant")
assistant_stub.AIAssistant = type("AIAssistant", (), {})
sys.modules.setdefault("src.ai.assistant", assistant_stub)

from src.core.categories.media_probe import _cached_probe_is_current, _language_from_tags, _parse_probe_payload
from src.core.categories.tv import TvShowCategory
from src.core.models import ScannedLibraryItem, ScannedMediaFile
from src.web.discord_bridge import _channel_id, _split_discord_content


def assert_equal(actual, expected, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def test_stream_title_language_fallback() -> None:
    assert_equal(_language_from_tags({"language": "und", "title": "Italian DTS-HD MA 5.1"}), "Italian", "Italian title fallback")
    assert_equal(_language_from_tags({"language": "und", "handler_name": "English AAC 2.0"}), "English", "English handler fallback")
    assert_equal(_language_from_tags({"language": "ita", "title": "whatever"}), "Italian", "Direct ISO language")


def test_probe_payload_preserves_multiaudio_and_resolution() -> None:
    payload = {
        "format": {"duration": "3600.0", "bit_rate": "4500000"},
        "streams": [
            {"index": 0, "codec_type": "video", "codec_name": "hevc", "width": 1920, "height": 1080},
            {"index": 1, "codec_type": "audio", "codec_name": "aac", "channels": 6, "tags": {"language": "und", "title": "Italian 5.1"}},
            {"index": 2, "codec_type": "audio", "codec_name": "aac", "channels": 2, "tags": {"language": "eng", "title": "English Stereo"}},
            {"index": 3, "codec_type": "subtitle", "codec_name": "subrip", "tags": {"language": "ita", "title": "Italian forced"}},
        ],
    }
    info = _parse_probe_payload(payload, path=Path("/tmp/show/S01E01.mkv"), size_bytes=123, mtime_ns=456)
    data = info.to_dict()
    assert_equal(data["audio_languages"], ["Italian", "English"], "Multi-audio languages")
    assert_equal(data["subtitle_languages"], ["Italian"], "Subtitle language")
    assert_equal(data["height"], 1080, "Video height")
    assert_equal(data["bit_rate_kbps"], 4500, "Probe bitrate")
    assert data.get("parser_version", 0) >= 2, "probe cache rows must carry parser version"


def test_old_probe_cache_is_not_trusted() -> None:
    old = {"size_bytes": 123, "mtime_ns": 456, "audio_languages": [], "height": 1080}
    new = {"size_bytes": 123, "mtime_ns": 456, "audio_languages": ["Italian"], "height": 1080, "parser_version": 2}
    assert_equal(_cached_probe_is_current(old, 123, 456), False, "Old parser cache invalidated")
    assert_equal(_cached_probe_is_current(new, 123, 456), True, "Current parser cache trusted")


def test_tv_units_use_probe_streams_not_filename_language() -> None:
    category = TvShowCategory()
    scanned = ScannedLibraryItem(
        name="Pluribus",
        category_id="tv",
        files=[ScannedMediaFile(
            season=1,
            episode=1,
            file_path="/library/Pluribus/Season 1/01.mkv",
            quality="unknown",
            size_bytes=1000,
            detected_language="Italian",
            audio_languages=["Italian", "English"],
            audio_tracks=[{"index": 1, "language": "Italian"}, {"index": 2, "language": "English"}],
            subtitle_languages=["Italian"],
            media_probe={"height": 1080, "width": 1920, "video_codecs": ["hevc"], "bit_rate_kbps": 4200, "audio_languages": ["Italian", "English"]},
        )],
        episodes={1: [1]},
        seasons=1,
        file_count=1,
        total_size_bytes=1000,
    )
    units = category.library_units_from_scan(scanned)
    assert_equal(len(units), 1, "One canonical file unit")
    unit = units[0]
    assert_equal(unit["audio_languages"], ["Italian", "English"], "TV canonical audio languages")
    assert_equal(unit["resolution"], "1080p", "TV canonical resolution from probe")
    assert_equal(unit["codec"], "hevc", "TV canonical codec from probe")


def test_discord_helpers_are_safe() -> None:
    assert_equal(_channel_id("1234567890"), 1234567890, "Discord channel id string normalization")
    assert_equal(_channel_id("not-a-number"), None, "Discord bad channel id")
    chunks = _split_discord_content("x" * 4100, limit=1900)
    assert len(chunks) == 3 and all(len(chunk) <= 1900 for chunk in chunks), "Discord content splitting"


def main() -> None:
    test_stream_title_language_fallback()
    test_probe_payload_preserves_multiaudio_and_resolution()
    test_old_probe_cache_is_not_trusted()
    test_tv_units_use_probe_streams_not_filename_language()
    test_discord_helpers_are_safe()
    print("Round 82 metadata/Discord regression traces passed")


if __name__ == "__main__":
    main()
