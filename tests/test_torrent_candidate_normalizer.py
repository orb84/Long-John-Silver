"""
Tests for torrent candidate normalization.

Verifies that TorrentSelectionService produces compact
NormalizedTorrentCandidate objects with accurate llm_summary fields.
"""

import pytest


class TestCandidateNormalizer:
    """Tests for NormalizedTorrentCandidate building and llm_summary."""

    def _make_result(self, title, magnet=None, size="1.5 GB", seeders=100, source="1337x"):
        from src.core.models import SearchResult
        return SearchResult(
            title=title,
            magnet=magnet,
            size=size,
            seeders=seeders,
            source=source,
            url="https://example.com/torrent/123",
            quality_score=0.8,
        )

    def test_build_llm_summary_basic(self):
        from src.ai.torrent_selection import TorrentSelectionService
        from src.core.models import NormalizedTorrentCandidate
        n = NormalizedTorrentCandidate(
            title="Show Name S01E02 1080p WEB-DL H264-GROUP",
            source="1337x",
            magnet="magnet:?xt=urn:btih:abc",
            magnet_available=True,
            detail_url=None,
            size="1.5 GB",
            seeders=100,
            resolution="1080p",
            codec="h264",
            release_type="WEB-DL",
            season=1,
            episode=2,
        )
        summary = TorrentSelectionService._build_llm_summary(n)
        assert "1080p" in summary
        assert "S01E02" in summary
        assert "100 seeders" in summary
        assert "1.5 GB" in summary
        assert "magnet yes" in summary
        assert "source 1337x" in summary

    def test_build_llm_summary_bundle(self):
        from src.ai.torrent_selection import TorrentSelectionService
        from src.core.models import NormalizedTorrentCandidate
        n = NormalizedTorrentCandidate(
            title="Show Name Complete S01 1080p BluRay",
            source="TorrentGalaxy",
            magnet="magnet:?xt=urn:btih:def",
            magnet_available=True,
            detail_url=None,
            size="12.3 GB",
            seeders=50,
            is_bundle=True,
            bundle_type="season_pack",
            bundle_scope="season",
            resolution="1080p",
            release_type="BluRay",
        )
        summary = TorrentSelectionService._build_llm_summary(n)
        assert "bundle:season_pack/season" in summary
        assert "12.3 GB" in summary

    def test_build_llm_summary_red_flags(self):
        from src.ai.torrent_selection import TorrentSelectionService
        from src.core.models import NormalizedTorrentCandidate
        n = NormalizedTorrentCandidate(
            title="Show S03E01 CAM bad source",
            source="1337x",
            magnet=None,
            magnet_available=False,
            detail_url=None,
            size="800 MB",
            seeders=5,
            red_flags=["theater recording"],
        )
        summary = TorrentSelectionService._build_llm_summary(n)
        assert "magnet no" in summary
        assert "[flags:" in summary

    def test_normalize_candidates_filters_and_parses(self):
        from src.ai.torrent_selection import TorrentSelectionService
        svc = TorrentSelectionService()
        results = [
            self._make_result("Show.Name.S01E01.1080p.WEB-DL.H264-GROUP",
                              magnet="magnet:?xt=urn:btih:abc",
                              seeders=200, source="1337x"),
            self._make_result("Show.Name.S01E01.CAM.XviD-GROUP",
                              magnet="magnet:?xt=urn:btih:bad",
                              seeders=5, source="1337x"),
            self._make_result("Show.Name.S01E02.720p.HDTV.x264-GROUP",
                              magnet=None, seeders=50, source="BTDigg"),
        ]
        normalized = svc.normalize_candidates(results, require_magnet=True)
        # Magnet-less and theater-recording candidates are filtered before the LLM.
        assert len(normalized) == 1
        assert normalized[0].magnet_available
        assert "200 seeders" in normalized[0].llm_summary
