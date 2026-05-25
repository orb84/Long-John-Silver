"""Tests for primary Jackett search with explicit fallback providers."""

import pytest

from src.core.models import SearchResult
from src.search.aggregator import SearchAggregator
from src.search.base import SearchProvider


class EmptyProvider(SearchProvider):
    """Primary provider returning no candidates."""

    @property
    def name(self) -> str:
        return "Jackett"

    async def search(self, query: str) -> list[SearchResult]:
        return []

    async def health_check(self) -> bool:
        return True


class FallbackProvider(SearchProvider):
    """Fallback provider returning one usable candidate."""

    @property
    def name(self) -> str:
        return "Fallback"

    async def search(self, query: str) -> list[SearchResult]:
        return [SearchResult(title="Example 1080p", magnet="magnet:?xt=urn:btih:def", size="1 GB", seeders=5)]

    async def health_check(self) -> bool:
        return True


class NoopBlacklist:
    """Minimal blacklist stub for aggregator tests."""

    def filter_results(self, results: list[SearchResult]) -> list[SearchResult]:
        return results


@pytest.mark.asyncio
async def test_aggregator_uses_fallback_when_primary_returns_empty() -> None:
    """Fallback providers are queried only after primary providers produce no usable result."""
    aggregator = SearchAggregator(
        providers=[EmptyProvider()],
        fallback_providers=[FallbackProvider()],
        blacklist=NoopBlacklist(),
    )

    results = await aggregator.search("Example", category="movie")

    assert len(results) == 1
    assert results[0].source == "Fallback"
