"""Tests for library scanner."""

import tempfile
from pathlib import Path
import pytest
from src.utils.library_scanner import LibraryScanner
from src.core.models import ScannedLibraryItem, Settings


@pytest.mark.asyncio
class TestLibraryScanner:
    async def _scan_category(self, category_id: str, root_path: str):
        scanner = LibraryScanner()
        settings = Settings(library_paths={category_id: root_path})
        result = await scanner.full_scan(settings)
        return result.by_category(category_id)

    async def test_scan_empty_dir(self):
        with tempfile.TemporaryDirectory() as d:
            items = await self._scan_category("tv", d)
            assert len(items) == 0

    async def test_scan_episodic_category(self):
        with tempfile.TemporaryDirectory() as d:
            show_dir = Path(d) / "Breaking Bad"
            season_dir = show_dir / "Season 01"
            season_dir.mkdir(parents=True)
            (season_dir / "Breaking Bad - S01E01.mp4").write_bytes(b"x" * 51200)
            (season_dir / "Breaking Bad - S01E02.mkv").write_bytes(b"x" * 51200)

            shows = await self._scan_category("tv", d)
            assert len(shows) == 1
            assert shows[0].name == "Breaking Bad"
            assert shows[0].file_count == 2
            assert 1 in shows[0].episodes
            assert len(shows[0].episodes[1]) == 2

    async def test_scan_movie_category(self):
        with tempfile.TemporaryDirectory() as d:
            movie_dir = Path(d) / "The Matrix (1999)"
            movie_dir.mkdir()
            (movie_dir / "The Matrix (1999).mkv").write_bytes(b"x" * 51200)

            movies = await self._scan_category("movie", d)
            assert movies[0].name == "The Matrix"
            assert movies[0].year == 1999

    async def test_scan_nonexistent_path(self):
        shows = await self._scan_category("tv", "/nonexistent/path")
        assert len(shows) == 0


class TestSmartQualityInferrer:
    def test_infer_from_scanned_item(self):
        from src.core.smart_quality import SmartQualityInferrer

        inferrer = SmartQualityInferrer()
        scanned = ScannedLibraryItem(
            category_id="tv",
            name="Test Show",
            file_count=5,
            total_size_bytes=5 * 2 * 1024 * 1024 * 1024,
            avg_file_size_mb=2048.0,
            codecs=["h265"],
            resolutions=["1080p"],
        )
        profile = inferrer.infer_for_item(scanned)
        assert profile.preferred_resolution == "1080p"
        assert profile.max_file_size_mb == 2662  # 1.3x
        assert "h265" in profile.preferred_codecs

    def test_smart_accepts_within_limit(self):
        from src.core.smart_quality import SmartQualityInferrer
        from src.core.models import QualityProfile, SearchResult, SizeLimitMode

        inferrer = SmartQualityInferrer()
        profile = QualityProfile(max_file_size_mb=4000, size_limit_mode=SizeLimitMode.FILE_SIZE)
        ok = SearchResult(title="ok", magnet="m:1", size_bytes=3 * 1024 * 1024 * 1024)
        accepted, _ = inferrer.should_accept_result(ok, profile)
        assert accepted is True

    def test_smart_passes_over_limit_to_llm(self):
        """Over-limit files are passed to the LLM — it decides quality."""
        from src.core.smart_quality import SmartQualityInferrer
        from src.core.models import QualityProfile, SearchResult, SizeLimitMode

        inferrer = SmartQualityInferrer()
        profile = QualityProfile(max_file_size_mb=4000, size_limit_mode=SizeLimitMode.FILE_SIZE)
        big = SearchResult(title="big", magnet="m:1", size_bytes=8 * 1024 * 1024 * 1024)
        accepted, reason = inferrer.should_accept_result(big, profile)
        assert accepted is True
        assert "llm" in reason.lower() or "evaluation" in reason.lower()

    def test_smart_passes_large_payloads_to_llm_and_category_hooks(self):
        """Large totals may be legitimate bundles/collections, not automatic rejects."""
        from src.core.smart_quality import SmartQualityInferrer
        from src.core.models import QualityProfile, SearchResult, SizeLimitMode

        inferrer = SmartQualityInferrer()
        profile = QualityProfile(max_file_size_mb=4000, size_limit_mode=SizeLimitMode.FILE_SIZE)
        large = SearchResult(title="large collection", magnet="m:1", size_bytes=55 * 1024 * 1024 * 1024)
        accepted, reason = inferrer.should_accept_result(large, profile)
        assert accepted is True
        assert "llm" in reason.lower() and "category" in reason.lower()

    def test_build_quality_context(self):
        from src.core.smart_quality import SmartQualityInferrer
        from src.core.models import QualityProfile

        inferrer = SmartQualityInferrer()
        scanned = ScannedLibraryItem(
            category_id="tv",
            name="Test", file_count=3, total_size_bytes=3 * 1024 * 1024 * 1024,
            avg_file_size_mb=1024.0, codecs=["h264"], resolutions=["1080p"],
        )
        profile = QualityProfile(max_file_size_mb=1331, size_limit_mode="smart")
        ctx = inferrer.build_quality_context("Test", profile, scanned)
        assert "1024MB" in ctx
        assert "1080p" in ctx
