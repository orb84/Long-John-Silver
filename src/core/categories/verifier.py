"""
Media verification for LJS.

Provides MediaVerifier as a focused collaborator extracted from MediaCategory.
Uses async ffprobe to validate media files without blocking the event loop.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from loguru import logger

from src.core.security.command_policy import CommandPolicy


class MediaVerifier:
    """Verifies media files using async ffprobe.

    Checks for video streams and minimum duration.
    All subprocess calls are async to avoid blocking the event loop.
    """

    MIN_DURATION_SECONDS: float = 60.0
    """Minimum acceptable duration for a valid media file (seconds)."""

    async def verify(self, file_path: Path) -> bool:
        """Verify a file is valid media using async ffprobe.

        Args:
            file_path: Path to the media file.

        Returns:
            True if the file has video content and sufficient duration.
        """
        try:
            probe = await self._probe(file_path)
            if probe is None:
                return False

            video_stream = next(
                (s for s in probe.get("streams", []) if s.get("codec_type") == "video"),
                None,
            )
            if not video_stream:
                logger.error(f"No video stream in {file_path}")
                return False

            duration = float(probe.get("format", {}).get("duration", 0))
            if duration < self.MIN_DURATION_SECONDS:
                logger.warning(f"File too short ({duration:.0f}s): {file_path}")
                return False

            logger.info(f"Verified media: {file_path} ({duration:.0f}s)")
            return True
        except Exception as e:
            logger.error(f"Media verification failed for {file_path}: {e}")
            return False

    @staticmethod
    async def _probe(file_path: Path) -> dict | None:
        """Run async ffprobe and return parsed JSON output.

        Returns:
            Parsed ffprobe JSON dict, or None on failure.
        """
        cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", str(file_path),
        ]
        proc = await CommandPolicy().create_subprocess_exec(
            cmd, purpose="media_verifier.ffprobe",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            logger.error(f"ffprobe failed for {file_path}")
            return None
        return json.loads(stdout)
