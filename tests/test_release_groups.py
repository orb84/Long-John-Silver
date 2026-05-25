"""
Tests for release group reputation tracker.

Verifies reputation scoring, caching, and blacklist integration.
"""

import pytest
import pytest_asyncio
from src.core.database import Database
from src.core.release_groups import ReleaseGroupTracker
from src.utils.blacklist import BlacklistManager


@pytest_asyncio.fixture
async def release_db(tmp_path):
    """Create a test database for release group tests."""
    database = Database(db_path=str(tmp_path / "test_release_groups.db"))
    await database.initialize()
    yield database
    await database.close()


@pytest_asyncio.fixture
async def blacklist_manager(release_db):
    """Create a BlacklistManager for release group tests."""
    bm = BlacklistManager(release_db)
    await bm.initialize()
    return bm


@pytest_asyncio.fixture
async def tracker(release_db, blacklist_manager):
    """Create a ReleaseGroupTracker with test database."""
    return ReleaseGroupTracker(db=release_db, blacklist_manager=blacklist_manager)


class TestReleaseGroupTracker:
    """Tests for release group reputation tracking."""

    @pytest.mark.asyncio
    async def test_record_success_updates_reputation(self, tracker):
        """Recording a successful download should improve reputation."""
        await tracker.record_outcome("Show.S01E01.1080p.h264-SPARKS", success=True)
        boost = await tracker.get_reputation_boost("Show.S02E03-SPARKS")
        # A successful group should have positive or neutral reputation
        assert boost >= 0

    @pytest.mark.asyncio
    async def test_record_failure_decreases_reputation(self, tracker):
        """Recording a failed download should decrease reputation."""
        await tracker.record_outcome("Movie.720p-FAKEGROUP", success=False)
        boost = await tracker.get_reputation_boost("Another.Movie-FAKEGROUP")
        # A failed group should have negative or zero reputation
        assert boost <= 0

    @pytest.mark.asyncio
    async def test_blacklisted_group_gets_penalty(self, tracker, blacklist_manager):
        """A blacklisted release group should get a strong negative penalty."""
        await blacklist_manager.add("YTS")
        boost = await tracker.get_reputation_boost("Movie.1080p-YTS")
        # Blacklisted groups get -0.3 penalty
        assert boost <= -0.2

    @pytest.mark.asyncio
    async def test_unknown_group_has_zero_reputation(self, tracker):
        """A group with no recorded outcomes should have neutral reputation."""
        boost = await tracker.get_reputation_boost("Show.S01E01-UNKNOWN_NEW_GROUP")
        assert boost == 0.0

    @pytest.mark.asyncio
    async def test_multiple_outcomes_build_reputation(self, tracker):
        """Multiple outcomes should build up reputation over time."""
        for _ in range(3):
            await tracker.record_outcome("Show.S01-GOODGROUP", success=True)
        await tracker.record_outcome("Show.S01-GOODGROUP", success=False)

        boost = await tracker.get_reputation_boost("Show.S02-GOODGROUP")
        assert boost > 0

    @pytest.mark.asyncio
    async def test_avg_quality_is_written(self, release_db, blacklist_manager):
        """update_release_group should write avg_quality column."""
        # Record outcome with a quality score
        await release_db.downloads.update_release_group("TESTGROUP", success=True, quality_score=0.8)
        row = await release_db.downloads.get_release_group("TESTGROUP")
        assert row is not None
        assert row["avg_quality"] == 0.8

    @pytest.mark.asyncio
    async def test_avg_quality_rolling_average(self, release_db, blacklist_manager):
        """avg_quality should use rolling average across multiple recordings."""
        await release_db.downloads.update_release_group("ROLLGROUP", success=True, quality_score=0.5)
        await release_db.downloads.update_release_group("ROLLGROUP", success=True, quality_score=0.7)
        await release_db.downloads.update_release_group("ROLLGROUP", success=True, quality_score=0.9)
        row = await release_db.downloads.get_release_group("ROLLGROUP")
        # Rolling average: 0.5 -> 0.6 -> 0.7 expected
        assert row is not None
        assert abs(row["avg_quality"] - 0.7) < 0.01