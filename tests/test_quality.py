"""Tests for quality scoring."""

from src.utils.quality import extract_quality_tags, score_result, QualityProfile


class TestExtractQualityTags:
    def test_1080p_h264(self):
        tags = extract_quality_tags("Show.S01E01.1080p.h264")
        assert tags["resolution"] == "1080p"
        assert tags["codec"] == "h264"

    def test_4k_hevc(self):
        tags = extract_quality_tags("Movie.2023.4K.HEVC")
        assert tags["resolution"] == "2160p"
        assert tags["codec"] == "hevc"

    def test_hdr(self):
        tags = extract_quality_tags("Show.S01E01.2160p.HDR10")
        assert tags["hdr"] is True

    def test_no_quality_info(self):
        tags = extract_quality_tags("Show.Season.1")
        assert tags["resolution"] is None
        assert tags["codec"] is None


class TestScoreResult:
    def test_perfect_match(self):
        profile = QualityProfile(preferred_resolution="1080p", preferred_codecs=["h264"])
        score = score_result("Show.S01E01.1080p.h264", profile)
        # 6-axis scoring: resolution match + codec match, no audio/release info
        assert score > 0.3

    def test_lower_resolution(self):
        profile = QualityProfile(preferred_resolution="1080p")
        score_high = score_result("Show.S01E01.1080p", profile)
        score_low = score_result("Show.S01E01.720p", profile)
        assert score_high > score_low

    def test_hdr_preference(self):
        profile = QualityProfile(preferred_resolution="1080p", prefer_hdr=True)
        score_hdr = score_result("Show.S01E01.1080p.HDR", profile)
        score_no_hdr = score_result("Show.S01E01.1080p", profile)
        assert score_hdr > score_no_hdr

    def test_exceeded_resolution_penalty(self):
        profile = QualityProfile(preferred_resolution="1080p")
        score_preferred = score_result("Show.S01E01.1080p", profile)
        score_exceeded = score_result("Show.S01E01.2160p", profile)
        # Exceeded resolution should score significantly lower than preferred due to PENALTY_RESOLUTION_EXCEEDED
        assert score_preferred > score_exceeded

    def test_exceeded_size_is_soft_penalty(self):
        profile = QualityProfile(max_file_size_mb=7168)  # 7GB limit
        # Total advertised size is only a deterministic signal. Bundles may
        # contain a useful file/unit inside a much larger torrent.
        score_allowed = score_result("Show.S01E01.1080p.3.5GB.WEB-DL", profile)
        score_large = score_result("Show.S01E01.1080p.15GB.WEB-DL", profile)
        assert score_allowed > 0.0
        assert score_large > 0.0
        assert score_allowed >= score_large


class TestFormatSize:
    def test_format_size_bytes(self):
        from src.utils.quality import format_size, QualityAnalyzer
        assert format_size(1024) == "1.0 KB"
        assert format_size(1048576) == "1.0 MB"
        assert format_size(1073741824) == "1.0 GB"
        assert QualityAnalyzer.format_size("2362232064") == "2.2 GB"
        assert QualityAnalyzer.format_size("Unknown") == "Unknown"
        assert QualityAnalyzer.format_size(None) == "Unknown"
        assert QualityAnalyzer.format_size("2.3 GB") == "2.3 GB"
        assert QualityAnalyzer.format_size("InvalidString") == "InvalidString"