"""Torrent URL resolver for LJS.

Provides dynamic async HTTP/HTTPS fetching of torrent files and transforms
them into fully qualified standard magnet URIs using an in-memory BencodeDecoder.
"""

import httpx
import hashlib
from typing import Optional
from loguru import logger
from urllib.parse import quote
from src.utils.bencode import BencodeDecoder


class TorrentUrlResolver:
    """Resolves external HTTP/HTTPS torrent download links to magnet URIs.

    Uses an in-memory BencodeDecoder to parse torrent metadata bytes, compute
    the SHA-1 infohash, and format a fully qualified magnet link.
    """

    def __init__(self, decoder: BencodeDecoder) -> None:
        """Initialize the resolver with its bencode decoder dependency.

        Args:
            decoder: The bencode decoder collaborator.
        """
        self._decoder = decoder

    async def resolve_to_magnet(self, url: str) -> str:
        """Fetch the torrent file and convert it into a magnet URI.

        Args:
            url: The HTTP/HTTPS url of the torrent file.

        Returns:
            A fully qualified magnet:?xt=urn:btih:<infohash>&dn=<name> URI.

        Raises:
            ValueError: If the file fetch fails, or the payload is not a valid bencoded torrent.
        """
        if url.startswith("magnet:"):
            logger.info(f"URL is already a magnet link: {url}")
            return url

        current_url = url
        torrent_bytes = b""
        
        try:
            # Manually follow redirects up to 5 times to catch magnet: redirects
            for redirect_depth in range(5):
                async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
                    try:
                        response = await client.get(current_url)
                    except httpx.UnsupportedProtocol as e:
                        # Intercept unsupported protocol redirects (e.g. magnet URIs)
                        if e.request and e.request.url:
                            req_url = str(e.request.url)
                            if req_url.startswith("magnet:"):
                                logger.info(f"Intercepted httpx.UnsupportedProtocol redirect to magnet URI: {req_url}")
                                return req_url
                        raise
                    
                    # If it's a redirect, check the target Location header
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("Location")
                        if not location:
                            raise ValueError(f"Redirect status {response.status_code} received but missing Location header")
                        
                        # If redirect target is a magnet URI, return it immediately!
                        if location.startswith("magnet:"):
                            logger.info(f"Redirect resolved directly to magnet URI: {location}")
                            return location
                            
                        # If redirect target is another HTTP/HTTPS URL, follow it
                        if location.startswith("http://") or location.startswith("https://"):
                            current_url = location
                            continue
                        else:
                            raise ValueError(f"Unsupported redirect protocol in Location: {location}")
                            
                    elif response.status_code != 200:
                        raise ValueError(f"HTTP fetch failed with status code {response.status_code}")
                        
                    torrent_bytes = response.content
                    break
            else:
                raise ValueError("Too many redirects")
        except httpx.UnsupportedProtocol as e:
            if e.request and e.request.url:
                req_url = str(e.request.url)
                if req_url.startswith("magnet:"):
                    logger.info(f"Intercepted top-level httpx.UnsupportedProtocol redirect to magnet URI: {req_url}")
                    return req_url
            logger.error(f"Unsupported protocol error: {e}")
            raise ValueError(f"Unsupported protocol: {e}") from e
        except Exception as e:
            logger.error(f"Failed to fetch or resolve torrent URL {url}: {e}")
            raise ValueError(f"Failed to download torrent file: {e}") from e

        try:
            decoded, _, info_start, info_end = self._decoder.decode_val(torrent_bytes, 0)
            if info_start is None or info_end is None:
                raise ValueError("Bencoded metadata is missing a valid 'info' dictionary")
        except Exception as e:
            logger.error(f"Failed to bdecode torrent metadata: {e}")
            raise ValueError(f"Failed to parse bencoded torrent file: {e}") from e

        # Slice b'info' payload exactly and compute SHA-1 hash
        info_bytes = torrent_bytes[info_start:info_end]
        info_hash = hashlib.sha1(info_bytes).hexdigest()

        # Extract display name and tracker/webseed metadata.  Earlier builds
        # converted .torrent files to bare infohash magnets, losing announce and
        # announce-list URLs.  That can make LJS much slower than a desktop client
        # that was fed the original .torrent file, especially for private or
        # tracker-dependent public releases.
        display_name = ""
        if isinstance(decoded, dict) and b"info" in decoded:
            info_dict = decoded[b"info"]
            if isinstance(info_dict, dict) and b"name" in info_dict:
                name_bytes = info_dict[b"name"]
                if isinstance(name_bytes, bytes):
                    try:
                        display_name = quote(name_bytes.decode("utf-8", errors="ignore"), safe="")
                    except Exception:
                        pass

        trackers = self._extract_trackers(decoded)
        webseeds = self._extract_url_list(decoded)

        magnet_uri = f"magnet:?xt=urn:btih:{info_hash}"
        if display_name:
            magnet_uri += f"&dn={display_name}"
        for tracker in trackers:
            magnet_uri += f"&tr={quote(tracker, safe='')}"
        for webseed in webseeds:
            magnet_uri += f"&ws={quote(webseed, safe='')}"

        logger.info(
            "Successfully resolved {} to magnet with {} tracker(s) and {} webseed(s)",
            url, len(trackers), len(webseeds),
        )
        return magnet_uri

    @staticmethod
    def _decode_text(value) -> str | None:
        if isinstance(value, bytes):
            try:
                return value.decode("utf-8", errors="ignore")
            except Exception:
                return None
        if isinstance(value, str):
            return value
        return None

    @classmethod
    def _extract_trackers(cls, decoded) -> list[str]:
        """Extract announce and announce-list URLs from decoded torrent data."""
        trackers: list[str] = []

        def add(value) -> None:
            """Append a decoded tracker URL once, preserving announce-list order."""
            text = cls._decode_text(value)
            if text and text not in trackers:
                trackers.append(text)

        if not isinstance(decoded, dict):
            return trackers
        add(decoded.get(b"announce") or decoded.get("announce"))
        announce_list = decoded.get(b"announce-list") or decoded.get("announce-list") or []
        if isinstance(announce_list, list):
            for tier in announce_list:
                if isinstance(tier, list):
                    for tracker in tier:
                        add(tracker)
                else:
                    add(tier)
        return trackers

    @classmethod
    def _extract_url_list(cls, decoded) -> list[str]:
        """Extract BEP-19 webseed URLs from decoded torrent data."""
        if not isinstance(decoded, dict):
            return []
        raw = decoded.get(b"url-list") or decoded.get("url-list")
        values = raw if isinstance(raw, list) else [raw]
        result: list[str] = []
        for value in values:
            text = cls._decode_text(value)
            if text and text not in result:
                result.append(text)
        return result
