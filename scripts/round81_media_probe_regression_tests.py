"""Round 81 regression checks for stream metadata and gentle probing.

These are intentionally lightweight scenario traces. They do not require real
media files or ffprobe; they exercise the canonical scan/unit path with mocked
probe payloads and verify that multi-audio information survives into the TV and
movie library objects.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace

from src.core.categories.media_probe import MediaProbeInfo, AudioTrackInfo, SubtitleTrackInfo, probe_media_files_serial
import src.core.categories.media_probe as media_probe
from src.core.categories.movie import MovieCategory
from src.core.categories.tv import TvShowCategory
from src.core.categories.types import ScannedFileObservation, ScannedItem


def _probe_payload() -> dict:
    return MediaProbeInfo(
        path="/library/Show/Season 01/Show.S01E01.mkv",
        size_bytes=1_500_000_000,
        mtime_ns=123,
        duration_seconds=3600,
        bit_rate_kbps=4200,
        video_codecs=["h264"],
        width=1920,
        height=1080,
        audio_tracks=[
            AudioTrackInfo(index=1, language="Italian", codec="aac", title="ITA", channels=2),
            AudioTrackInfo(index=2, language="English", codec="ac3", title="ENG", channels=6),
        ],
        subtitle_tracks=[SubtitleTrackInfo(index=3, language="Italian", codec="subrip", title="Forced")],
        probed_at="2026-05-24T00:00:00+00:00",
    ).to_dict()


def test_tv_units_preserve_multi_audio_streams() -> None:
    tv = TvShowCategory()
    file_obs = ScannedFileObservation(
        season=1,
        episode=1,
        file_path="/library/Show/Season 01/Show.S01E01.mkv",
        quality="unknown",
        size_bytes=1_500_000_000,
        detected_language="Italian",
        audio_languages=["Italian", "English"],
        audio_tracks=_probe_payload()["audio_tracks"],
        subtitle_languages=["Italian"],
        subtitle_tracks=_probe_payload()["subtitle_tracks"],
        media_probe=_probe_payload(),
    )
    scanned = ScannedItem(
        name="Show",
        category_id="tv",
        detailed_episodes=[file_obs],
        episodes={1: [1]},
        seasons=1,
        file_count=1,
        total_size_bytes=file_obs.size_bytes,
        detected_language="Italian, English",
        detected_languages=["Italian", "English"],
        subtitle_languages=["Italian"],
    )

    units = tv.library_units_from_scan(scanned)
    assert len(units) == 1
    unit = units[0]
    assert unit["language"] == "Italian, English"
    assert unit["audio_languages"] == ["Italian", "English"]
    assert len(unit["audio_tracks"]) == 2
    assert unit["subtitle_languages"] == ["Italian"]
    assert unit["estimated_bitrate_kbps"] == 4200
    assert unit["resolution"] == "1080p"
    assert unit["codec"] == "h264"

    obj = tv.build_library_object(SimpleNamespace(
        item={"display_name": "Show"},
        item_id="Show",
        units=units,
        metadata_rows=[],
        settings_item=None,
    ))
    episode = obj["seasons"][0]["episodes"][0]
    assert episode["language"] == "Italian, English"
    assert episode["audio_languages"] == ["Italian", "English"]
    assert episode["primary_audio_language"] == "Italian"
    assert obj["computed"]["audio_languages"] == ["Italian", "English"]
    assert obj["computed"]["subtitle_languages"] == ["Italian"]


def test_movie_units_preserve_multi_audio_streams() -> None:
    movie = MovieCategory()
    file_obs = ScannedFileObservation(
        file_path="/library/Movie/Movie.2024.mkv",
        quality="unknown",
        size_bytes=4_500_000_000,
        detected_language="Italian",
        audio_languages=["Italian", "English"],
        audio_tracks=_probe_payload()["audio_tracks"],
        subtitle_languages=["Italian"],
        subtitle_tracks=_probe_payload()["subtitle_tracks"],
        media_probe=_probe_payload(),
    )
    scanned = ScannedItem(
        name="Movie",
        category_id="movie",
        detailed_episodes=[file_obs],
        file_count=1,
        total_size_bytes=file_obs.size_bytes,
        detected_language="Italian, English",
        detected_languages=["Italian", "English"],
        subtitle_languages=["Italian"],
    )
    units = movie.library_units_from_scan(scanned)
    assert units[0]["language"] == "Italian, English"
    assert units[0]["audio_languages"] == ["Italian", "English"]
    obj = movie.build_library_object(SimpleNamespace(
        item={"display_name": "Movie"},
        item_id="Movie",
        units=units,
        metadata_rows=[],
        settings_item=None,
    ))
    assert obj["computed"]["audio_languages"] == ["Italian", "English"]
    assert obj["computed"]["subtitle_languages"] == ["Italian"]


async def test_probe_many_is_serialized() -> None:
    active = 0
    max_active = 0

    async def fake_probe(path: Path):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return MediaProbeInfo(path=str(path), size_bytes=1, mtime_ns=1)

    original = media_probe.probe_media_file
    media_probe.probe_media_file = fake_probe  # type: ignore[assignment]
    try:
        with tempfile.TemporaryDirectory() as tmp:
            paths = [Path(tmp) / f"f{i}.mkv" for i in range(5)]
            await probe_media_files_serial(paths)
    finally:
        media_probe.probe_media_file = original  # type: ignore[assignment]
    assert max_active == 1, f"expected serial probes, saw concurrency {max_active}"


def main() -> None:
    test_tv_units_preserve_multi_audio_streams()
    test_movie_units_preserve_multi_audio_streams()
    asyncio.run(test_probe_many_is_serialized())
    print("Round 81 media probe regression checks passed")


if __name__ == "__main__":
    main()
