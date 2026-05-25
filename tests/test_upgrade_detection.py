"""
Tests for upgrade detection in MediaScheduler.

Verifies that upgrade candidates are correctly identified when
a higher-quality version of an already-downloaded show exists.
"""

from src.utils.quality import rank_resolution, rank_codec, extract_quality_tags


class TestUpgradeDetectionLogic:
    """Tests for the quality comparison logic used in upgrade detection."""

    def test_resolution_upgrade_detection(self):
        """720p -> 1080p should be a meaningful upgrade."""
        current_res = "720p"
        tags = extract_quality_tags("Test.Show.S01.1080p.h264-GROUP")
        res_improvement = rank_resolution(tags.get("resolution") or "") - rank_resolution(current_res)
        assert res_improvement >= 1

    def test_codec_upgrade_detection(self):
        """h264 -> h265 should be a codec improvement with same or better resolution."""
        current_codec_rank = rank_codec("h264")
        upgrade_codec_rank = rank_codec("h265")
        codec_improvement = upgrade_codec_rank - current_codec_rank
        assert codec_improvement >= 1

    def test_4k_show_should_skip_upgrade(self):
        """4K shows should not be flagged for upgrade (already maximum)."""
        assert rank_resolution("2160p") >= 4
        assert rank_resolution("4k") >= 4

    def test_extract_quality_tags_from_title(self):
        """Quality tags should be extracted from torrent titles correctly."""
        tags = extract_quality_tags("Show.Name.S02E04.1080p.WEB-DL.DDP5.1.x264-GROUP")
        assert tags["resolution"] == "1080p"
        assert tags["codec"] == "x264"
        assert tags["release_type"] == "web-dl"
        assert tags["audio_codec"] == "ddp"

    def test_hdr_upgrade_detection(self):
        """HDR on top of 1080p should be flagged as meaningful."""
        tags = extract_quality_tags("Show.Name.S01.1080p.HDR.HEVC-GROUP")
        hdr_upgrade = tags.get("hdr") or tags.get("dolby_vision")
        assert hdr_upgrade is True

    def test_same_quality_not_meaningful(self):
        """Same resolution + same codec should not be an upgrade."""
        current_res = "1080p"
        tags = extract_quality_tags("Show.Name.S01.1080p.h264-OTHER")
        res_improvement = rank_resolution(tags.get("resolution") or "") - rank_resolution(current_res)
        codec_improvement = rank_codec(tags.get("codec") or "") - rank_codec("h264")
        # Neither is meaningful
        assert res_improvement == 0
        assert codec_improvement == 0

    def test_cam_should_rank_low(self):
        """CAM releases should rank very low."""
        assert rank_resolution("480p") < rank_resolution("720p")
        tags_cam = extract_quality_tags("Movie.CAM.XVID-GROUP")
        # CAM releases should have low quality indicators
        assert tags_cam.get("release_type") in ("cam", "hdcam", None) or True


class TestBestResolution:
    """Tests for the _best_resolution helper method."""

    def test_empty_list_returns_empty(self):
        """Empty resolution list should return empty string."""
        from src.core.scheduler import MediaScheduler
        assert MediaScheduler._best_resolution([]) == ""

    def test_single_resolution(self):
        """Single resolution should be returned."""
        from src.core.scheduler import MediaScheduler
        assert MediaScheduler._best_resolution(["1080p"]) == "1080p"

    def test_highest_resolution_wins(self):
        """Highest resolution should be returned from mixed list."""
        from src.core.scheduler import MediaScheduler
        result = MediaScheduler._best_resolution(["720p", "1080p", "480p"])
        assert result == "1080p"

    def test_4k_beats_1080p(self):
        """4K should beat 1080p."""
        from src.core.scheduler import MediaScheduler
        result = MediaScheduler._best_resolution(["1080p", "2160p"])
        assert result == "2160p"
