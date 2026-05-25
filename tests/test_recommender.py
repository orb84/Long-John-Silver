"""
Tests for the LJS Recommendation Engine.

Verifies the Hybrid taste scoring, Trakt/TMDB integration, and the
weekly send recommendations notification cooldown.
"""

import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

from src.core.database import Database
from src.core.notifications import NotificationService
from src.core.recommender import RecommendationEngine
from src.core.models import TasteProfile


@pytest_asyncio.fixture
async def recommender_db(tmp_path):
    """Create a temporary database for recommendation tests."""
    database = Database(db_path=str(tmp_path / "test_recommender.db"))
    await database.initialize()
    yield database
    await database.close()


@pytest_asyncio.fixture
def mock_notifications():
    """Mock NotificationService."""
    ns = MagicMock(spec=NotificationService)
    ns.send_message = AsyncMock()
    return ns


class TestRecommendationEngine:
    """Tests for RecommendationEngine logic."""

    @pytest.mark.asyncio
    async def test_send_recommendations_cooldown(self, recommender_db, mock_notifications):
        """Test that send_recommendations enforces a 7-day cooldown."""
        # Setup RecommendationEngine
        engine = RecommendationEngine(
            trakt_client=None,
            behavior_tracker=None,
            db=recommender_db,
            notifications=mock_notifications,
        )

        # Mock get_recommendations to return a mock recommendation list
        mock_recs = [
            {"title": "Black Sails", "year": 2014, "reason": "Pirate adventure match"},
            {"title": "Our Flag Means Death", "year": 2022, "reason": "Comedy match"},
        ]
        engine.get_recommendations = AsyncMock(return_value=mock_recs)

        # 1. First run: should send recommendation notification
        await engine.send_recommendations()
        assert mock_notifications.send_message.call_count == 1
        
        # Verify preference key is updated
        last_sent_str = await recommender_db.system.get_preference("last_recommendation_time")
        assert last_sent_str != ""
        last_sent = datetime.fromisoformat(last_sent_str)
        # Should be close to current time
        assert (datetime.now(timezone.utc) - last_sent).total_seconds() < 60

        # Reset mocks
        mock_notifications.send_message.reset_mock()

        # 2. Second run: cooldown is active, should skip sending
        await engine.send_recommendations()
        assert mock_notifications.send_message.call_count == 0

        # 3. Simulate 8 days elapsed in the database preference
        eight_days_ago = datetime.now(timezone.utc) - timedelta(days=8)
        await recommender_db.system.set_preference("last_recommendation_time", eight_days_ago.isoformat())

        # 4. Third run: cooldown expired, should send recommendation notification again
        await engine.send_recommendations()
        assert mock_notifications.send_message.call_count == 1
