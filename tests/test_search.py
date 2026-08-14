"""Tests for the search aggregator, BTDigg parser, and provider timeout/retry."""

import asyncio
import pytest
from src.search.aggregator import SearchAggregator
from src.core.models import SearchResult, QualityProfile
from src.utils.blacklist import BlacklistManager
from src.core.models import BlacklistEntry
import re


class MockProvider:
    """A mock search provider that returns predefined results."""

    def __init__(self, results=None):
        self._results = results or []

    @property
    def name(self):
        return "mock"

    async def search(self, query):
        return self._results

    async def health_check(self):
        return True


class SlowProvider:
    """A mock provider that simulates slow responses."""

    def __init__(self, delay: float, results=None):
        self._delay = delay
        self._results = results or []
        self.search_calls = 0

    @property
    def name(self):
        return "slow"

    async def search(self, query):
        self.search_calls += 1
        await asyncio.sleep(self._delay)
        return self._results

    async def health_check(self):
        return True


class FailingProvider:
    """A mock provider that always raises an exception on first call."""

    def __init__(self, results=None):
        self._results = results or []
        self.call_count = 0

    @property
    def name(self):
        return "failing"

    async def search(self, query):
        self.call_count += 1
        if self.call_count == 1:
            raise ConnectionError("Provider unavailable")
        return self._results

    async def health_check(self):
        return True


class TestSearchAggregator:
    def test_deduplicate(self):
        """Duplicate results (by magnet) should be removed."""
        import re
        blacklist = BlacklistManager.__new__(BlacklistManager)
        blacklist._cache = []
        blacklist._compiled = []

        results = [
            SearchResult(title="Show S01E01 1080p", magnet="magnet:1", size="1GB", source="a"),
            SearchResult(title="Show S01E01 1080p", magnet="magnet:1", size="1GB", source="b"),
            SearchResult(title="Show S01E01 720p", magnet="magnet:2", size="500MB", source="a"),
        ]

        agg = SearchAggregator(providers=[], blacklist=blacklist)
        deduped = agg._deduplicate(results)
        assert len(deduped) == 2

    def test_blacklist_filter(self):
        """Blacklisted results should be filtered out."""
        import re
        blacklist = BlacklistManager.__new__(BlacklistManager)
        blacklist._cache = [BlacklistEntry(pattern=".*CAM.*", reason="Low quality")]
        blacklist._compiled = [re.compile(".*CAM.*", re.IGNORECASE)]

        results = [
            SearchResult(title="Show S01E01 1080p", magnet="magnet:1", size="1GB", source="test"),
            SearchResult(title="Show S01E01 CAM", magnet="magnet:2", size="500MB", source="test"),
        ]

        filtered = blacklist.filter_results(results)
        assert len(filtered) == 1
        assert "CAM" not in filtered[0].title

    @pytest.mark.asyncio
    async def test_provider_timeout(self):
        """Slow providers should be timed out and results still returned."""

        class _NoopBlacklist:
            def filter_results(self, results):
                return results

        fast_provider = MockProvider(results=[
            SearchResult(title="Fast Result", magnet="magnet:fast", size="1GB", source="fast"),
        ])
        slow_provider = SlowProvider(delay=10.0, results=[
            SearchResult(title="Slow Result", magnet="magnet:slow", size="2GB", source="slow"),
        ])

        agg = SearchAggregator(
            providers=[fast_provider, slow_provider],
            blacklist=_NoopBlacklist(),
            provider_timeout=1,  # 1 second timeout
            provider_retries=0,
        )
        results = await agg.search("test query")
        # Fast provider result should be present; slow provider should timeout
        assert any(r.title == "Fast Result" for r in results)

    @pytest.mark.asyncio
    async def test_provider_retry(self):
        """Failing providers should retry before giving up."""

        class _NoopBlacklist:
            def filter_results(self, results):
                return results

        failing = FailingProvider(results=[
            SearchResult(title="Recovered", magnet="magnet:recovered", size="1GB", source="failing"),
        ])
        agg = SearchAggregator(
            providers=[failing],
            blacklist=_NoopBlacklist(),
            provider_timeout=5,
            provider_retries=1,
        )
        results = await agg.search("test query")
        # FailingProvider succeeds on 2nd call (call_count=2)
        assert failing.call_count == 2
        assert any(r.title == "Recovered" for r in results)


class TestBTDiggParser:
    def test_parse_results_empty(self):
        """Empty HTML should return an empty list."""
        from src.search.btdigg import BTDiggSearch
        search = BTDiggSearch()
        html = "<html><body></body></html>"
        results = search._parse_results(html)
        assert len(results) == 0


@pytest.mark.asyncio
async def test_scheduler_untracked_search_dynamic_tvshowitem():
    """Untracked TV requests should be materialized by the TV category."""
    from unittest.mock import MagicMock

    from src.core.categories.registry import CategoryRegistry
    from src.core.models import Settings
    from src.core.scheduler_services import SchedulerServiceContext, SchedulerTorrentSearchService

    settings_manager = MagicMock()
    settings = Settings()
    settings.tracked_items = []
    settings_manager.settings = settings
    context = SchedulerServiceContext(
        settings_manager=settings_manager,
        db=MagicMock(),
        downloader=MagicMock(),
        pipeline=MagicMock(),
        aggregator=MagicMock(),
        categories=CategoryRegistry.with_defaults(),
    )
    service = SchedulerTorrentSearchService(context)

    media = await service._media_for_request("Firefly", "Firefly", "tv", "English")

    assert media.key == "Firefly"
    assert media.language == "English"
    assert media.item_type == "tv"

