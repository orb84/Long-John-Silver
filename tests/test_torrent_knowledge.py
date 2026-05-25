"""
Tests for the torrent knowledge base and generic bundle handler.

Verifies release type detection, red flag detection, category-owned TV bundle
detection, per-unit size estimation, quality explanation generation, and the
comprehensive quality guide string for LLM prompt injection.
"""

import pytest
from src.utils.torrent_knowledge import TorrentKnowledge, get_quality_guide, TORRENT_QUALITY_GUIDE
from src.core.bundle_download import BundleDownloadHandler
from src.core.categories.tv_bundle import TVBundleKnowledge


class TestQualityGuide:
    """Tests for the LLM-consumable quality guide string."""

    def test_guide_is_nonempty_string(self):
        guide = get_quality_guide()
        assert isinstance(guide, str)
        assert len(guide) > 500

    def test_guide_includes_theater_recordings(self):
        guide = get_quality_guide()
        assert "CAM" in guide
        assert "THEATER RECORDINGS" in guide

    def test_guide_includes_streaming_platforms(self):
        guide = get_quality_guide()
        assert "AMZN" in guide
        assert "NF" in guide or "Netflix" in guide
        assert "ATVP" in guide

    def test_guide_includes_generic_bundle_section(self):
        guide = get_quality_guide()
        assert "Multi-file Bundles" in guide
        assert "category-provided bundle descriptors" in guide

    def test_guide_includes_red_flags(self):
        guide = get_quality_guide()
        assert "HDCAM" in guide
        assert "KORSUB" in guide

    def test_guide_includes_decision_rules(self):
        guide = get_quality_guide()
        assert "Quality Decision Rules" in guide or "NEVER select CAM" in guide

    def test_constant_matches_function(self):
        assert TORRENT_QUALITY_GUIDE == get_quality_guide()

    def test_guide_includes_video_codecs(self):
        guide = get_quality_guide()
        assert "x265" in guide or "HEVC" in guide
        assert "AV1" in guide


class TestTorrentKnowledgeRedFlags:
    """Tests for red flag detection in torrent titles."""

    def test_cam_detected(self):
        flags = TorrentKnowledge.detect_red_flags("Movie.2024.HDCAM.1080p")
        assert len(flags) > 0
        assert any("theater" in f["reason"].lower() or "camcorder" in f["reason"].lower() for f in flags)

    def test_hdts_detected(self):
        flags = TorrentKnowledge.detect_red_flags("Movie.2024.HDTS.1080p")
        assert len(flags) > 0

    def test_korsub_detected(self):
        flags = TorrentKnowledge.detect_red_flags("Movie.2024.1080p.KORSUB")
        assert len(flags) > 0
        assert any("korean" in f["reason"].lower() for f in flags)

    def test_hardsub_detected(self):
        flags = TorrentKnowledge.detect_red_flags("Show.S01E01.1080p.h264-GRP.hardsub")
        assert len(flags) > 0

    def test_clean_title_no_flags(self):
        flags = TorrentKnowledge.detect_red_flags("Show.S01E01.1080p.WEB-DL.DDP5.1-GROUP")
        assert len(flags) == 0

    def test_camrip_detected(self):
        flags = TorrentKnowledge.detect_red_flags("Movie.2024.camrip")
        assert len(flags) > 0


class TestTorrentKnowledgeReleaseTypes:
    """Tests for release type info lookup."""

    def test_cam_info(self):
        info = TorrentKnowledge.get_release_type_info("cam")
        assert info is not None
        assert info["quality_tier"] == "unacceptable"
        assert info["score_modifier"] < 0

    def test_webdl_info(self):
        info = TorrentKnowledge.get_release_type_info("web-dl")
        assert info is not None
        assert info["quality_tier"] == "very_good"
        assert info["score_modifier"] > 0

    def test_remux_info(self):
        info = TorrentKnowledge.get_release_type_info("remux")
        assert info is not None
        assert info["quality_tier"] == "best"

    def test_unknown_type(self):
        info = TorrentKnowledge.get_release_type_info("unknown_type")
        assert info is None


class TestTorrentKnowledgeSeasonPacks:
    """Tests for season pack detection."""

    def test_complete_season(self):
        result = TVBundleKnowledge.detect_season_pack("Show.S01.Complete.1080p.WEB-DL-GROUP")
        assert result is not None
        assert result["season"] == 1
        assert result["pack_type"] == "complete"

    def test_season_pack_keyword(self):
        result = TVBundleKnowledge.detect_season_pack("Show.Name.S02.1080p.BluRay.Season.Pack-GROUP")
        assert result is not None
        assert result["season"] == 2

    def test_implicit_season_pack(self):
        result = TVBundleKnowledge.detect_season_pack("Show.Name.S03.1080p.WEB-DL-GROUP")
        assert result is not None
        assert result["season"] == 3
        assert result["pack_type"] == "implicit"

    def test_single_episode_not_pack(self):
        result = TVBundleKnowledge.detect_season_pack("Show.S01E05.1080p.WEB-DL-GROUP")
        assert result is None

    def test_episode_range(self):
        result = TVBundleKnowledge.detect_season_pack("Show.S01E01-E12.1080p.WEB-DL-GROUP")
        assert result is not None
        assert result["season"] == 1
        assert result["pack_type"] == "partial_range"
        assert result["start"] == 1
        assert result["end"] == 12


    def test_multi_season_range(self):
        result = TVBundleKnowledge.detect_season_pack("Show.S01-S03.Complete.1080p.WEB-DL-GROUP")
        assert result is not None
        assert result["pack_type"] == "multi_season"
        assert result["season_start"] == 1
        assert result["season_end"] == 3

    def test_complete_series(self):
        result = TVBundleKnowledge.detect_season_pack("Show.Complete.Series.1080p.WEB-DL-GROUP")
        assert result is not None
        assert result["pack_type"] == "series_complete"
        assert result["scope"] == "series"

    def test_not_season_pack_no_s_number(self):
        result = TVBundleKnowledge.detect_season_pack("Movie.2024.1080p.WEB-DL-GROUP")
        assert result is None


class TestTorrentKnowledgePerEpisodeSize:
    """Tests for per-episode size estimation."""

    def test_standard_show(self):
        # 40GB for a standard ~22 episodes
        per_ep = TVBundleKnowledge.estimate_per_episode_size_mb(
            40 * 1024 * 1024 * 1024, "Show.S01.Complete.1080p.WEB-DL"
        )
        # Standard shows: (18+24)/2 = 21 eps
        # 40GB / 21 eps ~= 1.9GB per ep ~= ~1950MB
        assert 1500 < per_ep < 2500

    def test_anime(self):
        # 15GB for anime ~12 episodes
        per_ep = TVBundleKnowledge.estimate_per_episode_size_mb(
            15 * 1024 * 1024 * 1024, "[SubsPlease] Show - S01 1080p"
        )
        # Anime: (12+13)/2 = 12.5 eps — but int division yields 12
        # 15GB / 12 = ~1280MB
        assert 800 < per_ep < 1500

    def test_known_episode_count(self):
        per_ep = TVBundleKnowledge.estimate_per_episode_size_mb(
            10 * 1024 * 1024 * 1024, "Show", episode_count=10
        )
        expected = 10 * 1024 / 10  # 10GB / 10 eps = 1GB = 1024MB
        assert abs(per_ep - expected) < 50


class TestTorrentKnowledgeQualityExplanation:
    """Tests for the quality explanation builder."""

    def test_cam_explanation(self):
        tags = {"release_type": "cam", "estimated_size_gb": None}
        explanation = TorrentKnowledge.build_quality_explanation("Movie.HDCAM", tags)
        assert "CAM" in explanation or "camera" in explanation.lower()

    def test_bundle_explanation_is_category_owned(self):
        tags = {
            "release_type": "web-dl",
            "estimated_size_gb": 45.0,
        }
        explanation = TorrentKnowledge.build_quality_explanation(
            "Grouped.Payload.1080p.WEB-DL", tags
        )
        assert "WEB-DL" in explanation

    def test_red_flag_explanation(self):
        tags = {"release_type": None, "estimated_size_gb": None, "red_flags": [
            {"flag_type": "hardcoded_subs", "reason": "KORSUB: Hardcoded Korean subtitles"}
        ]}
        explanation = TorrentKnowledge.build_quality_explanation("Movie.KORSUB.1080p", tags)
        assert "WARNING" in explanation or "Korean" in explanation

    def test_no_concerns_explanation(self):
        tags = {"release_type": None, "estimated_size_gb": None, "red_flags": []}
        explanation = TorrentKnowledge.build_quality_explanation("Show.1080p.WEB-DL", tags)
        assert "No quality concerns" in explanation


class TestBundleDownloadHandler:
    """Tests for category-owned bundle handling."""

    def test_describes_tv_season_bundle(self):
        handler = BundleDownloadHandler()
        result = handler.describe_candidate(
            "Show.S01.Complete.1080p.WEB-DL",
            category_id="tv",
        )
        assert result is not None
        assert result["bundle_type"] == "tv_bundle"
        assert result["season"] == 1

    def test_does_not_describe_single_episode_as_bundle(self):
        handler = BundleDownloadHandler()
        result = handler.describe_candidate(
            "Show.S01E05.1080p.WEB-DL",
            category_id="tv",
        )
        assert result is None

    def test_compute_per_unit_limit_with_profile(self):
        handler = BundleDownloadHandler()
        total_bytes = 40 * 1024 * 1024 * 1024  # 40GB
        limit = handler.compute_per_unit_limit_mb(
            total_bytes, "Show.S01.Complete.1080p",
            category_id="tv",
            profile_max_mb=2000,
        )
        # When profile has a per-file/unit limit, that limit applies per useful unit.
        assert limit == 2000

    def test_compute_per_unit_limit_no_profile(self):
        handler = BundleDownloadHandler()
        total_bytes = 40 * 1024 * 1024 * 1024  # 40GB
        limit = handler.compute_per_unit_limit_mb(
            total_bytes, "Show.S01.Complete.1080p",
            category_id="tv",
        )
        assert limit is not None
        assert limit > 0
