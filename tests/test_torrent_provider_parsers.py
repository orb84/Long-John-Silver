"""
Tests for torrent provider HTML parsers using offline fixtures.

Validates title, magnet, size, and seeders extraction from static
HTML snapshots of provider pages. Ensures parsers work without
network access and flags regressions when site layouts change.
"""

from pathlib import Path
import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "search"


def _read_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


class Test1337xParser:
    """Tests for the 1337x search and detail page parsers."""

    def test_search_page_extracts_titles(self):
        from src.search.search_1337x import Search1337x
        html = _read_fixture("1337x_search.html")
        provider = Search1337x()
        results = provider._parse_results(html)
        assert len(results) == 3
        titles = [r.title for r in results]
        assert "Show Name S01E01 1080p WEB-DL H264" in titles
        assert "The Movie 2024 2160p HEVC" in titles
        assert "Another Show S02E03 720p HDTV" in titles

    def test_search_page_extracts_seeders(self):
        from src.search.search_1337x import Search1337x
        html = _read_fixture("1337x_search.html")
        provider = Search1337x()
        results = provider._parse_results(html)
        assert results[0].seeders == 256
        assert results[1].seeders == 1024
        assert results[2].seeders == 89

    def test_search_page_extracts_size(self):
        from src.search.search_1337x import Search1337x
        html = _read_fixture("1337x_search.html")
        provider = Search1337x()
        results = provider._parse_results(html)
        assert results[0].size == "1.5 GB"
        assert results[1].size == "22.3 GB"
        assert results[2].size == "892.4 MB"

    def test_search_page_no_magnets_yet(self):
        from src.search.search_1337x import Search1337x
        html = _read_fixture("1337x_search.html")
        provider = Search1337x()
        results = provider._parse_results(html)
        for r in results:
            assert r.magnet is None

    def test_detail_page_extracts_magnet(self):
        from src.search.search_1337x import Search1337x
        html = _read_fixture("1337x_detail.html")
        magnet = Search1337x._extract_magnet(html)
        assert magnet is not None
        assert magnet.startswith("magnet:")
        assert "abc123def456" in magnet


class TestNyaaParser:
    """Tests for the Nyaa.si search page parser."""

    def test_search_page_extracts_titles(self):
        from src.search.nyaa import NyaaSearch
        html = _read_fixture("nyaa_search.html")
        provider = NyaaSearch()
        results = provider._parse_results(html)
        assert len(results) == 2
        titles = [r.title for r in results]
        assert "[SubsPlease] Anime Show - 01 (1080p)" in titles

    def test_search_page_extracts_magnets(self):
        from src.search.nyaa import NyaaSearch
        html = _read_fixture("nyaa_search.html")
        provider = NyaaSearch()
        results = provider._parse_results(html)
        for r in results:
            assert r.magnet is not None
            assert r.magnet.startswith("magnet:")

    def test_search_page_extracts_seeders(self):
        from src.search.nyaa import NyaaSearch
        html = _read_fixture("nyaa_search.html")
        provider = NyaaSearch()
        results = provider._parse_results(html)
        assert results[0].seeders == 512
        assert results[1].seeders == 1280

    def test_no_results_page_returns_empty(self):
        from src.search.nyaa import NyaaSearch
        html = "<html><body>No results found</body></html>"
        provider = NyaaSearch()
        results = provider._parse_results(html)
        assert results == []


class TestBTDiggParser:
    """Tests for the BTDigg search page parser."""

    def test_search_page_extracts_results(self):
        from src.search.btdigg import BTDiggSearch
        html = _read_fixture("btdigg_search.html")
        provider = BTDiggSearch()
        results = provider._parse_results(html)
        assert len(results) == 2
        assert results[0].title == "Show.Name.S01E01.1080p.WEB-DL.H264-GROUP"
        assert results[0].magnet is not None
        assert results[0].size == "1.50 GB"

    def test_requires_both_title_and_magnet(self):
        from src.search.btdigg import BTDiggSearch
        html = """<div class="one_result">
            <span class="attr_val">1.5 GB</span>
            <a href="magnet:?xt=urn:btih:xxx">magnet</a>
        </div>"""
        provider = BTDiggSearch()
        results = provider._parse_results(html)
        assert results == []


class TestTorrentGalaxyParser:
    """Tests for the TorrentGalaxy search page parser."""

    def test_search_page_extracts_results(self):
        from src.search.torrentgalaxy import TorrentGalaxySearch
        html = _read_fixture("torrentgalaxy_search.html")
        provider = TorrentGalaxySearch()
        results = provider._parse_results(html)
        assert len(results) == 2
        assert results[0].title == "Show Name S01E01 1080p WEB-DL"
        assert results[0].magnet is not None
        assert results[0].seeders == 256
        assert results[0].size == "1.5 GB"


class TestCloudflareDetection:
    """Tests that parsers correctly handle Cloudflare pages."""

    def test_is_cloudflare_block_detects_challenge(self):
        from src.search.http_utils import is_cloudflare_block
        html = _read_fixture("cloudflare.html")
        assert is_cloudflare_block(200, html)

    def test_is_cloudflare_block_ignores_normal_page(self):
        from src.search.http_utils import is_cloudflare_block
        assert not is_cloudflare_block(200, "<html><body>Normal content</body></html>")

    def test_1337x_parser_returns_empty_on_cloudflare(self):
        from src.search.search_1337x import Search1337x
        html = _read_fixture("cloudflare.html")
        provider = Search1337x()
        results = provider._parse_results(html)
        assert results == []

    def test_classify_error_categories(self):
        from src.search.http_utils import classify_error

        class DNSException(Exception):
            def __str__(self):
                return "No address associated with hostname"

        category = classify_error("test", DNSException())
        assert category == "dns"
