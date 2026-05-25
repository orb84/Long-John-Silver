"""Tests for the first-class Jackett JSON torrent provider."""

from src.search.jackett import JackettSearch


def test_jackett_json_parser_extracts_magnet_and_size() -> None:
    """Jackett native JSON rows become LJS SearchResult objects."""
    provider = JackettSearch("http://127.0.0.1:9117", "key")
    results = provider._parse_payload({
        "Results": [
            {
                "Title": "Example.Movie.2026.1080p.WEB-DL.x265-GRP",
                "MagnetUri": "magnet:?xt=urn:btih:abc123",
                "Size": 2147483648,
                "Seeders": 42,
                "Tracker": "1337x",
                "Details": "https://example.invalid/details",
            }
        ]
    })

    assert len(results) == 1
    assert results[0].title.startswith("Example.Movie")
    assert results[0].magnet == "magnet:?xt=urn:btih:abc123"
    assert results[0].size_bytes == 2147483648
    assert results[0].seeders == 42
    assert results[0].source == "Jackett:1337x"
