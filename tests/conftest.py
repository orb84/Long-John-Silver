"""
Shared test fixtures for LJS.

Provides in-memory database, mock LLM provider, mock search providers,
and commonly-needed test objects. All tests should use these fixtures
rather than creating real service connections.
"""

import asyncio
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from src.core.models import (
    Settings, QualityProfile, TvShowItem, MovieItem, CategoryItem, ItemList, LLMConfig, SearchResult,
    DownloadItem, DownloadStatus, DownloadPriority,
    UpgradeCandidate,
)
from src.core.database import Database
from src.core.preferences import PreferenceManager
from src.core.smart_quality import SmartQualityInferrer
from src.search.base import SearchProvider


# --- In-Memory Database Fixture ---

@pytest_asyncio.fixture
async def db(tmp_path):
    """Create an in-memory database for testing."""
    database = Database(db_path=str(tmp_path / "test_ljs.db"))
    await database.initialize()
    yield database
    await database.close()


# --- Preference Manager Fixture ---

@pytest_asyncio.fixture
async def preference_manager(db):
    """Create a PreferenceManager with test database."""
    return PreferenceManager(db=db)


# --- Settings Fixture ---

@pytest.fixture
def settings():
    """Create a default Settings object for testing."""
    return Settings(
        llm=LLMConfig(
            model="test-model",
            api_key="test-key",
        ),
        tracked_items=ItemList(items=[
            TvShowItem(key="Test Show", language="English"),
            TvShowItem(key="Breaking Bad", language="English"),
        ]),
        download_dir="/tmp/ljs_test_downloads",
        library_root="/tmp/ljs_test_library",
        default_quality=QualityProfile(),
    )


# --- Mock Search Provider ---

class MockSearchProvider(SearchProvider):
    """A search provider that returns canned results for testing."""

    def __init__(self, results=None):
        self._results = results or []
        self._search_calls = []

    @property
    def name(self) -> str:
        return "mock"

    async def search(self, query: str) -> list[SearchResult]:
        self._search_calls.append(query)
        return self._results

    async def health_check(self) -> bool:
        return True


@pytest.fixture
def mock_provider():
    """Create a MockSearchProvider with sample results."""
    return MockSearchProvider(results=[
        SearchResult(
            title="Test.Show.S01E01.1080p.h264-GROUP",
            magnet="magnet:?xt=urn:btih:abc123",
            size="2.5 GB",
            seeders=50,
            source="mock",
        ),
        SearchResult(
            title="Test.Show.S01E01.720p.h264-OTHER",
            magnet="magnet:?xt=urn:btih:def456",
            size="1.2 GB",
            seeders=20,
            source="mock",
        ),
    ])


# --- Quality Scoring Fixtures ---

@pytest.fixture
def quality_profile():
    """Create a default QualityProfile for tests."""
    return QualityProfile(
        preferred_resolution="1080p",
        preferred_codecs=["h264", "h265", "hevc"],
        max_file_size_mb=5000,
        size_limit_mode="smart",
    )


# --- Upgrade Candidate Fixture ---

@pytest.fixture
def upgrade_candidate():
    """Create a sample UpgradeCandidate for tests."""
    return UpgradeCandidate(
        item_name="Test Show",
        current_resolution="720p",
        current_codecs=["h264"],
        best_upgrade_resolution="1080p",
        best_upgrade_codecs=["h265"],
        best_upgrade_title="Test.Show.S01.1080p.HEVC-GROUP",
        best_upgrade_magnet="magnet:?xt=urn:btih:upgrade123",
        quality_improvement="720p -> 1080p, codec: h264 -> h265",
    )


# --- Download Item Fixtures ---

@pytest.fixture
def download_item():
    """Create a sample DownloadItem for tests."""
    return DownloadItem(
        id="abc123",
        item_name="Test Show",
        magnet="magnet:?xt=urn:btih:abc123",
        status=DownloadStatus.DOWNLOADING,
        priority=DownloadPriority.NORMAL,
    )
