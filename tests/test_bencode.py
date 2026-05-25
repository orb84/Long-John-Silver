"""Unit tests for the custom recursive bencode parser and URL resolver."""

import pytest
import hashlib
from unittest.mock import AsyncMock, patch
from src.utils.bencode import BencodeDecoder
from src.core.torrent_resolver import TorrentUrlResolver


class TestBencodeDecoder:
    """Tests for the BencodeDecoder recursive parsing logic."""

    def test_decode_integer(self) -> None:
        """Verify integer decoding."""
        decoder = BencodeDecoder()
        val, idx, i_s, i_e = decoder.decode_val(b"i42e", 0)
        assert val == 42
        assert idx == 4
        
        val, idx, i_s, i_e = decoder.decode_val(b"i-100e", 0)
        assert val == -100
        assert idx == 6

    def test_decode_string(self) -> None:
        """Verify length-prefixed string decoding."""
        decoder = BencodeDecoder()
        val, idx, i_s, i_e = decoder.decode_val(b"4:spam", 0)
        assert val == b"spam"
        assert idx == 6
        
        val, idx, i_s, i_e = decoder.decode_val(b"0:", 0)
        assert val == b""
        assert idx == 2

    def test_decode_list(self) -> None:
        """Verify list decoding."""
        decoder = BencodeDecoder()
        val, idx, i_s, i_e = decoder.decode_val(b"li42e4:spame", 0)
        assert val == [42, b"spam"]
        assert idx == 12

    def test_decode_dict(self) -> None:
        """Verify dictionary decoding."""
        decoder = BencodeDecoder()
        val, idx, i_s, i_e = decoder.decode_val(b"d3:bar4:spame", 0)
        assert val == {b"bar": b"spam"}
        assert idx == 13

    def test_decode_info_hash_extraction(self) -> None:
        """Verify exact start and end byte offsets of 'info' dictionary are captured."""
        decoder = BencodeDecoder()
        # d4:infoi1234e4:name4:spame
        data = b"d4:infoi1234e4:name4:spame"
        val, idx, i_s, i_e = decoder.decode_val(data, 0)
        assert val == {b"info": 1234, b"name": b"spam"}
        assert data[i_s:i_e] == b"i1234e"

    def test_malformed_bencode_raises(self) -> None:
        """Malformed payloads should raise descriptive ValueErrors."""
        decoder = BencodeDecoder()
        with pytest.raises(ValueError, match="Unexpected EOF"):
            decoder.decode_val(b"", 0)
        with pytest.raises(ValueError, match="Unterminated integer"):
            decoder.decode_val(b"i123", 0)
        with pytest.raises(ValueError, match="Unterminated string length"):
            decoder.decode_val(b"4spam", 0)
        with pytest.raises(ValueError, match="String length out of bounds"):
            decoder.decode_val(b"10:spam", 0)


@pytest.mark.asyncio
class TestTorrentUrlResolver:
    """Tests for the TorrentUrlResolver class."""

    async def test_resolve_to_magnet_success(self) -> None:
        """Should fetch torrent bytes, decode, compute infohash and return magnet URI."""
        decoder = BencodeDecoder()
        resolver = TorrentUrlResolver(decoder)

        # Build a valid dummy torrent payload
        # d8:announce14:http://tracker4:infod6:lengthi1024e4:name4:testee
        data = b"d8:announce14:http://tracker4:infod6:lengthi1024e4:name4:testee"
        info_slice = b"d6:lengthi1024e4:name4:teste"
        expected_hash = hashlib.sha1(info_slice).hexdigest()

        # Mock the httpx AsyncClient
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.content = data

        with patch("httpx.AsyncClient.get", return_value=mock_response):
            magnet = await resolver.resolve_to_magnet("https://example.com/file.torrent")
            assert f"magnet:?xt=urn:btih:{expected_hash}" in magnet
            assert "dn=test" in magnet

    async def test_resolve_to_magnet_http_failure_raises(self) -> None:
        """Should raise ValueError if the HTTP request returns an error code."""
        decoder = BencodeDecoder()
        resolver = TorrentUrlResolver(decoder)

        mock_response = AsyncMock()
        mock_response.status_code = 404

        with patch("httpx.AsyncClient.get", return_value=mock_response):
            with pytest.raises(ValueError, match="HTTP fetch failed with status code 404"):
                await resolver.resolve_to_magnet("https://example.com/missing.torrent")
