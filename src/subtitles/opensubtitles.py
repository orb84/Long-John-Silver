"""
OpenSubtitles integration for LJS.

Automatically fetches subtitle files for downloaded media.
"""

import httpx
from loguru import logger
from pathlib import Path
from typing import Optional
from src.core.models import SubtitleResult


class OpenSubtitlesClient:
    """Client for the OpenSubtitles REST API (v3)."""

    BASE_URL = "https://api.opensubtitles.com/api/v1"

    def __init__(self, api_key: str):
        self._api_key = api_key
        self._headers = {
            "Api-Key": api_key,
            "User-Agent": "LJS v1.0",
        }

    async def search_subtitles(
        self, query: str, language: str = "en"
    ) -> list[SubtitleResult]:
        """Search for subtitles by query string."""
        logger.info(f"Searching subtitles for '{query}' (lang={language})")
        params = {"query": query, "languages": language}

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    f"{self.BASE_URL}/subtitles",
                    params=params,
                    headers=self._headers,
                )
                response.raise_for_status()
                data = response.json()
                return self._parse_results(data, language)
        except Exception as e:
            logger.error(f"Subtitle search failed: {e}")
            return []

    async def search_by_file(
        self, file_path: Path, language: str = "en"
    ) -> list[SubtitleResult]:
        """Search for subtitles matching a specific file."""
        movie_hash = self._compute_hash(file_path)
        params = {
            "moviehash": movie_hash,
            "languages": language,
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    f"{self.BASE_URL}/subtitles",
                    params=params,
                    headers=self._headers,
                )
                response.raise_for_status()
                data = response.json()
                return self._parse_results(data, language)
        except Exception as e:
            logger.error(f"Subtitle search by file failed: {e}")
            return []

    async def download_subtitle(self, file_id: int, target_dir: Path,
                                 file_name: str) -> Optional[Path]:
        """Download a subtitle file by its ID."""
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    f"{self.BASE_URL}/download",
                    json={"file_id": file_id},
                    headers=self._headers,
                )
                response.raise_for_status()
                data = response.json()
                download_url = data.get("link")
                if not download_url:
                    return None

                subtitle_response = await client.get(download_url)
                subtitle_response.raise_for_status()

                target_dir.mkdir(parents=True, exist_ok=True)
                target = target_dir / f"{file_name}.srt"
                target.write_bytes(subtitle_response.content)
                logger.info(f"Downloaded subtitle: {target}")
                return target
        except Exception as e:
            logger.error(f"Subtitle download failed: {e}")
            return None

    def _parse_results(self, data: dict, language: str) -> list[SubtitleResult]:
        """Parse OpenSubtitles API response."""
        results = []
        for sub in data.get("data", []):
            attrs = sub.get("attributes", {})
            results.append(SubtitleResult(
                title=attrs.get("release_name", ""),
                language=language,
                download_url=attrs.get("url", ""),
                file_name=attrs.get("file_name", ""),
                source="opensubtitles",
            ))
        return results[:10]  # Limit to top 10 results

    @staticmethod
    def _compute_hash(file_path: Path) -> str:
        """Compute OpenSubtitles hash for a file (first+last 64KB)."""
        import hashlib
        try:
            file_size = file_path.stat().st_size
            with open(file_path, "rb") as f:
                head = f.read(65536)
                f.seek(max(0, file_size - 65536))
                tail = f.read(65536)
            combined = head + tail + file_size.to_bytes(8, "little")
            return hashlib.md5(combined).hexdigest()
        except Exception:
            return ""