"""Tests for the blacklist manager."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from src.utils.blacklist import BlacklistManager
from src.core.models import BlacklistEntry, SearchResult


class TestBlacklistManager:
    def test_filter_results(self):
        """Blacklisted titles should be removed from results."""
        import re
        manager = BlacklistManager.__new__(BlacklistManager)
        manager._cache = [
            BlacklistEntry(pattern=".*CAM.*", reason="Low quality"),
            BlacklistEntry(pattern=r"\bTS\b.*\bversion\b", reason="Telesync"),
        ]
        manager._compiled = [re.compile(e.pattern, re.IGNORECASE) for e in manager._cache]

        results = [
            SearchResult(title="Movie.2024.1080p.BluRay", magnet="magnet:1", size="2GB", source="test"),
            SearchResult(title="Movie.2024.CAM.1080p", magnet="magnet:2", size="1GB", source="test"),
            SearchResult(title="Show.S01E01.TS version", magnet="magnet:3", size="500MB", source="test"),
        ]

        filtered = manager.filter_results(results)
        assert len(filtered) == 1
        assert "CAM" not in filtered[0].title

    def test_is_blacklisted(self):
        import re
        manager = BlacklistManager.__new__(BlacklistManager)
        manager._cache = [BlacklistEntry(pattern=".*YTS.*", reason="YIFY group")]
        manager._compiled = [re.compile(".*YTS.*", re.IGNORECASE)]

        assert manager.is_blacklisted("Movie.YTS.1080p") is not None
        assert manager.is_blacklisted("Movie.1080p.BluRay") is None