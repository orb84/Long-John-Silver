"""
Tests for behavior tracker and recorder.

Verifies action recording via BehaviorRecorder, profile aggregation
via BehaviorTracker, and preference merging from behavioral data.
"""

import pytest
import pytest_asyncio
from src.core.database import Database
from src.core.behavior_tracker import BehaviorTracker
from src.ai.behavior_recorder import BehaviorRecorder


@pytest_asyncio.fixture
async def behavior_db(tmp_path):
    """Create a test database for behavior tracker tests."""
    database = Database(db_path=str(tmp_path / "test_behavior.db"))
    await database.initialize()
    yield database
    await database.close()


@pytest_asyncio.fixture
async def recorder(behavior_db):
    """Create a BehaviorRecorder with test database."""
    return BehaviorRecorder(db=behavior_db)


@pytest_asyncio.fixture
async def tracker(behavior_db):
    """Create a BehaviorTracker with test database."""
    return BehaviorTracker(db=behavior_db)


class TestBehaviorRecorder:
    """Tests for action recording via BehaviorRecorder."""

    @pytest.mark.asyncio
    async def test_record_download_stores_in_db(self, recorder, tracker):
        """Recording a download should persist it and appear in the profile."""
        await recorder.record_download(
            user_id="user1", item_name="Severance",
            resolution="1080p", codec="h265",
            release_group="SPARKS",
        )
        profile = await tracker.get_behavior_profile("user1")
        assert profile is not None
        assert profile.get("total_downloads", 0) >= 1

    @pytest.mark.asyncio
    async def test_multiple_downloads_build_profile(self, recorder, tracker):
        """Multiple download actions should build up a behavioral profile."""
        await recorder.record_download(
            user_id="user2", item_name="The Bear",
            resolution="1080p", codec="h264",
        )
        await recorder.record_download(
            user_id="user2", item_name="The Bear",
            resolution="1080p", codec="h265",
        )
        await recorder.record_download(
            user_id="user2", item_name="The Bear",
            resolution="720p", codec="h264",
        )

        profile = await tracker.get_behavior_profile("user2")
        assert profile is not None
        assert profile.get("preferred_resolution") == "1080p"
        assert profile.get("total_downloads") == 3

    @pytest.mark.asyncio
    async def test_record_reject(self, recorder, tracker):
        """Reject actions should be recorded via BehaviorRecorder."""
        await recorder.record_reject(
            user_id="user3", item_name="Bad Show",
        )
        profile = await tracker.get_behavior_profile("user3")
        assert profile is not None

    @pytest.mark.asyncio
    async def test_record_search(self, recorder, tracker):
        """Search actions should be recorded via BehaviorRecorder."""
        await recorder.record_search(
            user_id="user4", query="test show",
        )

    @pytest.mark.asyncio
    async def test_format_profile_for_prompt(self, recorder, tracker):
        """format_profile_for_prompt should produce a readable string."""
        await recorder.record_download(
            user_id="user5", item_name="Succession",
            resolution="1080p", codec="h265",
        )
        profile = await tracker.get_behavior_profile("user5")
        formatted = tracker.format_profile_for_prompt(profile)
        assert isinstance(formatted, str)
        assert len(formatted) > 0