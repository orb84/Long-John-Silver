"""
Tests for smart movie size limit calculations in LJS.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from src.core.smart_quality import SmartQualityInferrer
from src.core.search_pipeline import SearchPipeline
from src.core.models import MovieItem, QualityProfile
from src.core.categories.movie import MovieCategory


class TestMovieSmartSize:
    """Test suite for smart movie size limits."""

    @pytest.mark.asyncio
    async def test_get_average_library_movie_size_from_scan(self) -> None:
        """Verify smart quality infers movie size from scan results."""
        inferrer = SmartQualityInferrer()
        
        # Mock Scan Result with 2 movies having different sizes
        mock_scan_res = MagicMock()
        item1 = MagicMock()
        item1.category_id = "movie"
        item1.avg_file_size_mb = 1000.0
        
        item2 = MagicMock()
        item2.category_id = "movie"
        item2.avg_file_size_mb = 2000.0

        # An unrelated TV item to ensure filtering
        item3 = MagicMock()
        item3.category_id = "tv"
        item3.avg_file_size_mb = 500.0

        mock_scan_res.items = [item1, item2, item3]

        avg = await inferrer.get_average_library_item_size_mb(category_id="movie", scan_result=mock_scan_res)
        assert avg == 1500.0

    @pytest.mark.asyncio
    async def test_search_pipeline_applies_smart_limit_from_scan(self) -> None:
        """Verify SearchPipeline resolves and overrides profile size using 1.3x library average."""
        aggregator = AsyncMock()
        downloader = MagicMock()
        db = MagicMock()
        librarian = MagicMock()
        category_registry = MagicMock()
        torrent_selection = MagicMock()
        settings_manager = MagicMock()

        # Set default/preferred language and mock settings
        settings_manager.settings.language = "en"
        
        # Build category registry mocks
        category_registry.get.return_value = MovieCategory()

        # Create SearchPipeline
        pipeline = SearchPipeline(
            aggregator, downloader, db, librarian, category_registry,
            torrent_selection=torrent_selection, settings_manager=settings_manager
        )

        # Mock scheduler to return last scan result
        mock_scheduler = MagicMock()
        mock_scan = MagicMock()
        item = MagicMock()
        item.category_id = "movie"
        item.avg_file_size_mb = 2000.0  # 2GB average movie size in library
        mock_scan.items = [item]
        mock_scheduler.get_last_scan_result.return_value = mock_scan

        pipeline.set_scheduler(mock_scheduler)

        # Create Movie item with no max_file_size_mb set
        movie_item = MovieItem(
            key="Gladiator",
            language="en",
            quality=QualityProfile(
                preferred_resolution="1080p",
                max_file_size_mb=0  # Trigger smart cap
            )
        )

        # Mock aggregator search return
        aggregator.search.return_value = []

        await pipeline.run_search(movie_item, mode="fast")

        # The aggregator search should have been called with the 1.3x smart limit override
        # 1.3 * 2000 MB = 2600 MB
        args, kwargs = aggregator.search.call_args
        quality_profile = kwargs.get("quality_profile")
        assert quality_profile is not None
        assert quality_profile.max_file_size_mb == 2600
