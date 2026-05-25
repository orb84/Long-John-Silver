"""Tests for category-based media name parsing."""

from src.core.categories.tv import TvShowCategory
from src.core.categories.movie import MovieCategory


class TestParseTVShow:
    def setup_method(self):
        self.parser = TvShowCategory()

    def test_standard_s01e01(self):
        result = self.parser.parse_name("Breaking.Bad.S01E01.720p.Bluray.x264")
        assert result.title == "Breaking Bad"
        assert result.season == 1
        assert result.episode == 1
        assert result.resolution == "720p"
        assert result.codec == "x264"

    def test_1x01_format(self):
        result = self.parser.parse_name("Game of Thrones 1x01 Winter Is Coming")
        assert result.title == "Game of Thrones"
        assert result.season == 1
        assert result.episode == 1

    def test_season_episode_format(self):
        result = self.parser.parse_name("The Office Season 2 Episode 5")
        assert result.title == "The Office"
        assert result.season == 2
        assert result.episode == 5

    def test_dots_in_title(self):
        result = self.parser.parse_name("Stranger.Things.S04E07.1080p.NF.WEB-DL.DDP5.1.x264")
        assert result.title == "Stranger Things"
        assert result.season == 4
        assert result.episode == 7
        assert result.resolution == "1080p"

    def test_season_pack_complete(self):
        result = self.parser.parse_name("Show.Name.S04.COMPLETE.1080p.WEBRiP")
        assert result.season == 4

    def test_season_pack_standalone(self):
        result = self.parser.parse_name("For.All.Mankind.S05.1080p.WEBRiP")
        assert result.season == 5


class TestParseAnime:
    def setup_method(self):
        self.parser = TvShowCategory()

    def test_anime_format(self):
        result = self.parser.parse_name("[SubGroup] Attack on Titan - 01 [1080p]")
        assert result.title == "Attack on Titan"
        assert result.episode == 1
        assert result.release_group == "SubGroup"
        assert result.is_anime is True
        assert result.resolution == "1080p"


class TestParseMovie:
    def setup_method(self):
        self.parser = MovieCategory()

    def test_movie_with_year(self):
        result = self.parser.parse_name("The Matrix (1999)")
        assert result.title == "The Matrix"
        assert result.year == 1999

    def test_parse_year_resolution(self):
        result = self.parser.parse_name("Some.Movie.Name.(2023).2160p.WEB-DL.DV.HDR10")
        assert result.year == 2023
        assert result.resolution == "2160p"


class TestExtractQuality:
    def setup_method(self):
        self.parser = TvShowCategory()

    def test_4k_detection(self):
        result = self.parser.parse_name("Some.Show.S01E01.4K.HEVC")
        assert result.resolution in ("2160p", "4k")
        assert result.codec == "hevc"

    def test_release_group(self):
        result = self.parser.parse_name("Show.S01E01.1080p-LOL")
        assert result.release_group == "LOL"
