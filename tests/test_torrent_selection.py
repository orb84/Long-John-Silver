"""
Tests for TorrentSelectionService — deterministic pre-filtering
and LLM-based torrent candidate selection.
"""

import pytest
import json
from unittest.mock import AsyncMock, MagicMock

from src.ai.torrent_selection import TorrentSelectionService, REJECTED_RELEASE_TYPES, MAX_LLM_CANDIDATES
from src.core.models import SearchResult


def _make_result(title: str, seeders: int = 10, magnet: str = "magnet:?xt=urn:btih:abc", size: str = "1.5 GB") -> SearchResult:
    """Helper to build a SearchResult quickly."""
    return SearchResult(title=title, seeders=seeders, magnet=magnet, size=size)


class TestDeterministicPreFilter:
    """Tests for TorrentSelectionService.deterministic_pre_filter()."""

    def setup_method(self):
        self.service = TorrentSelectionService()

    def test_rejects_cam_releases_before_llm(self):
        """CAM, TS, and HDCAM are hard-rejected before LLM evaluation."""
        results = [
            _make_result("Movie.2024.CAM.x264", seeders=500),
            _make_result("Movie.2024.TS.x264", seeders=300),
            _make_result("Movie.2024.HDCAM.x264", seeders=200),
            _make_result("Movie.2024.WEB-DL.1080p", seeders=100),
        ]
        filtered = self.service.deterministic_pre_filter(results)
        assert len(filtered) == 1
        assert filtered[0].title == "Movie.2024.WEB-DL.1080p"

    def test_rejects_camrip_and_tsrip_before_llm(self):
        """CAMRIP and TSRIP are hard-rejected before LLM evaluation."""
        results = [
            _make_result("Show.S02E01.CAMRIP.x264", seeders=100),
            _make_result("Show.S02E01.TSRIP.x264", seeders=50),
            _make_result("Show.S02E01.1080p.WEB-DL", seeders=80),
        ]
        filtered = self.service.deterministic_pre_filter(results)
        assert len(filtered) == 1
        assert filtered[0].title == "Show.S02E01.1080p.WEB-DL"

    def test_removes_no_magnet_results_when_required(self):
        """Results without magnet links should be removed by default."""
        results = [
            _make_result("Show.S01.WEB-DL", magnet="magnet:?xt=urn:btih:abc"),
            _make_result("Show.S01.HDTV", magnet=None),
        ]
        filtered = self.service.deterministic_pre_filter(results, require_magnet=True)
        assert len(filtered) == 1
        assert filtered[0].magnet is not None

    def test_keeps_no_magnet_when_not_required(self):
        """Results without magnets should pass when require_magnet=False."""
        results = [
            _make_result("Show.S01.WEB-DL", magnet="magnet:?xt=urn:btih:abc"),
            _make_result("Show.S01.HDTV", magnet=None),
        ]
        filtered = self.service.deterministic_pre_filter(results, require_magnet=False)
        assert len(filtered) == 2

    def test_sorts_by_seeders_descending(self):
        """Results should be sorted by seeder count, highest first."""
        results = [
            _make_result("Low.Seeds.1080p.WEB-DL", seeders=5),
            _make_result("High.Seeds.1080p.WEB-DL", seeders=500),
            _make_result("Mid.Seeds.1080p.WEB-DL", seeders=50),
        ]
        filtered = self.service.deterministic_pre_filter(results)
        assert filtered[0].seeders == 500
        assert filtered[1].seeders == 50
        assert filtered[2].seeders == 5

    def test_caps_at_max_candidates(self):
        """Only the top MAX_LLM_CANDIDATES should be kept after filtering."""
        results = [_make_result(f"Result.{i}.1080p.WEB-DL", seeders=100 - i) for i in range(20)]
        filtered = self.service.deterministic_pre_filter(results)
        assert len(filtered) == MAX_LLM_CANDIDATES

    def test_empty_input_returns_empty(self):
        """Empty input list should return empty list."""
        assert self.service.deterministic_pre_filter([]) == []

    def test_all_cam_rejected(self):
        """All theater recordings are rejected by the deterministic pre-filter."""
        results = [
            _make_result("Movie.2024.CAM.x264"),
            _make_result("Movie.2024.TS.x264"),
        ]
        filtered = self.service.deterministic_pre_filter(results)
        assert len(filtered) == 0


class TestBuildQualityReference:
    """Tests for TorrentSelectionService.build_quality_reference()."""

    def setup_method(self):
        self.service = TorrentSelectionService()

    def test_returns_compact_guide_for_small_context(self):
        """Small context window gets compact quality guide."""
        ref = self.service.build_quality_reference([], context_limit=4096)
        assert "REMUX" in ref
        assert "CAM" in ref
        # Compact guide should be much shorter than the full guide
        assert len(ref) < 500

    def test_returns_full_guide_for_large_context(self):
        """Large context window gets full quality guide."""
        ref = self.service.build_quality_reference([], context_limit=16384)
        # Full guide is significantly longer
        assert len(ref) > len(self.service.build_quality_reference([], context_limit=4096))


class TestSelectBest:
    """Tests for TorrentSelectionService.select_best() — the full selection pipeline."""

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_results(self):
        """Empty results should return None."""
        service = TorrentSelectionService()
        result = await service.select_best("Show", "S01E01", [], "en")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_candidate_without_llm_client_when_only_cam(self):
        """Without LLM client, hard-rejected CAM candidates still return None."""
        service = TorrentSelectionService()
        results = [
            _make_result("Show.S01E01.CAM.x264", seeders=100),
            _make_result("Show.S01E01.TS.x264", seeders=50),
        ]
        result = await service.select_best("Show", "S01E01", results, "en")
        assert result is None

    @pytest.mark.asyncio
    async def test_fallback_when_no_llm_client(self):
        """Without an LLM client, should return the top pre-filtered candidate."""
        service = TorrentSelectionService(llm_client=None, circuit_breaker=None)
        results = [
            _make_result("Show.S01E01.1080p.WEB-DL", seeders=200),
            _make_result("Show.S01E01.720p.WEB-DL", seeders=50),
        ]
        result = await service.select_best("Show", "S01E01", results, "en")
        assert result is None

    @pytest.mark.asyncio
    async def test_llm_selection_with_valid_response(self):
        """LLM returns a valid index — service should return that candidate."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"index": 0}'

        mock_llm = AsyncMock()
        mock_llm.completion = AsyncMock(return_value=mock_response)

        mock_breaker = MagicMock()
        mock_breaker.call = AsyncMock(return_value=mock_response)

        service = TorrentSelectionService(
            llm_client=mock_llm,
            circuit_breaker=mock_breaker,
        )
        results = [
            _make_result("Show.S01E01.1080p.WEB-DL", seeders=200),
            _make_result("Show.S01E01.720p.WEB-DL", seeders=50),
        ]
        result = await service.select_best("Show", "S01E01", results, "en")
        assert result is not None
        assert result["title"] == results[0].title

    @pytest.mark.asyncio
    async def test_llm_selection_with_negative_one_fails_closed(self):
        """LLM returns index=-1 — service should fail closed."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"index": -1}'

        mock_llm = AsyncMock()
        mock_llm.completion = AsyncMock(return_value=mock_response)

        mock_breaker = MagicMock()
        mock_breaker.call = AsyncMock(return_value=mock_response)

        service = TorrentSelectionService(
            llm_client=mock_llm,
            circuit_breaker=mock_breaker,
        )
        results = [_make_result("Show.S01E01.1080p.WEB-DL", seeders=200)]
        result = await service.select_best("Show", "S01E01", results, "en")
        assert result is None

    @pytest.mark.asyncio
    async def test_llm_invalid_json_fails_closed(self):
        """LLM returns invalid JSON — service should fail closed."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = 'not json at all'

        mock_llm = AsyncMock()
        mock_llm.completion = AsyncMock(return_value=mock_response)

        mock_breaker = MagicMock()
        mock_breaker.call = AsyncMock(return_value=mock_response)

        service = TorrentSelectionService(
            llm_client=mock_llm,
            circuit_breaker=mock_breaker,
        )
        results = [_make_result("Show.S01E01.1080p.WEB-DL", seeders=200)]
        result = await service.select_best("Show", "S01E01", results, "en")
        assert result is None

    @pytest.mark.asyncio
    async def test_llm_out_of_range_index_fails_closed(self):
        """LLM returns an index outside the candidate range — should fail closed."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"index": 99}'

        mock_llm = AsyncMock()
        mock_llm.completion = AsyncMock(return_value=mock_response)

        mock_breaker = MagicMock()
        mock_breaker.call = AsyncMock(return_value=mock_response)

        service = TorrentSelectionService(
            llm_client=mock_llm,
            circuit_breaker=mock_breaker,
        )
        results = [_make_result("Show.S01E01.1080p.WEB-DL", seeders=200)]
        result = await service.select_best("Show", "S01E01", results, "en")
        assert result is None

    @pytest.mark.asyncio
    async def test_llm_exception_fails_closed(self):
        """LLM call raises an exception — service should fail closed."""
        mock_llm = AsyncMock()
        mock_llm.completion = AsyncMock(side_effect=RuntimeError("provider error"))

        mock_breaker = MagicMock()
        mock_breaker.call = AsyncMock(side_effect=RuntimeError("provider error"))

        service = TorrentSelectionService(
            llm_client=mock_llm,
            circuit_breaker=mock_breaker,
        )
        results = [_make_result("Show.S01E01.1080p.WEB-DL", seeders=200)]
        result = await service.select_best("Show", "S01E01", results, "en")
        assert result is None

    @pytest.mark.asyncio
    async def test_cam_candidates_not_sent_to_llm(self):
        """CAM results are filtered before the LLM can select them."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"index": 0}'

        mock_llm = AsyncMock()
        mock_llm.completion = AsyncMock(return_value=mock_response)

        mock_breaker = MagicMock()
        mock_breaker.call = AsyncMock(return_value=mock_response)

        service = TorrentSelectionService(
            llm_client=mock_llm,
            circuit_breaker=mock_breaker,
        )
        results = [
            _make_result("Show.S01E01.CAM.x264", seeders=9999),
            _make_result("Show.S01E01.TS.x264", seeders=500),
        ]
        result = await service.select_best("Show", "S01E01", results, "en")
        assert result is None
        mock_breaker.call.assert_not_called()

    @pytest.mark.asyncio
    async def test_select_best_with_quality_profile_resolution_constraints(self):
        """Verifies that select_best handles QualityProfile resolution constraints correctly.

        1. Checks that the prompt contains the strict rejection criteria if preferred_resolution is set.
        2. Checks that quality_ref includes strict resolution constraints.
        """
        from src.core.models import QualityProfile

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"index": 0}'

        mock_llm = AsyncMock()
        mock_llm.completion = AsyncMock(return_value=mock_response)

        mock_breaker = MagicMock()
        mock_breaker.call = AsyncMock(return_value=mock_response)

        service = TorrentSelectionService(
            llm_client=mock_llm,
            circuit_breaker=mock_breaker,
        )

        results = [
            _make_result("Show.S01E01.1080p.WEB-DL", seeders=200),
            _make_result("Show.S01E01.2160p.WEB-DL", seeders=150),
        ]
        
        # Test 1080p preference
        profile_1080p = QualityProfile(preferred_resolution="1080p")
        result = await service.select_best("Show", "S01E01", results, "en", quality_profile=profile_1080p)
        assert result is not None
        
        # Verify the prompt contained the rejection rule for higher resolutions
        call_args = mock_breaker.call.call_args[1]
        prompt = call_args["messages"][0]["content"]
        assert "Rejection criteria" in prompt
        assert "Have a resolution HIGHER than the preferred resolution of '1080p'" in prompt
        assert "DO NOT select resolutions higher than '1080p'" in prompt

    @pytest.mark.asyncio
    async def test_select_best_with_quality_profile_size_constraints(self):
        """Verifies that select_best programmatically filters out candidates exceeding size limits."""
        from src.core.models import QualityProfile

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"index": 0}'

        mock_llm = AsyncMock()
        mock_llm.completion = AsyncMock(return_value=mock_response)

        mock_breaker = MagicMock()
        mock_breaker.call = AsyncMock(return_value=mock_response)

        service = TorrentSelectionService(
            llm_client=mock_llm,
            circuit_breaker=mock_breaker,
        )

        results = [
            SearchResult(title="Show.S01E01.1080p.3.5GB.WEB-DL", seeders=200, magnet="magnet:?xt=abc", size="3.5 GB", size_bytes=3758096384),
            SearchResult(title="Show.S01E01.1080p.15GB.WEB-DL", seeders=150, magnet="magnet:?xt=def", size="15 GB", size_bytes=16106127360),
        ]

        profile_7gb = QualityProfile(max_file_size_mb=7168)
        result = await service.select_best("Show", "S01E01", results, "en", quality_profile=profile_7gb)

        assert result is not None
        assert result["title"] == "Show.S01E01.1080p.3.5GB.WEB-DL"

        if mock_breaker.call.called:
            call_args = mock_breaker.call.call_args[1]
            prompt = call_args["messages"][0]["content"]
            assert "15GB.WEB-DL" not in prompt
            assert "3.5GB.WEB-DL" in prompt

    @pytest.mark.asyncio
    async def test_select_best_with_unpopulated_size_bytes_regression(self):
        """Regression test for [ISSUE-056]: Verifies that SearchResult parses size strings to size_bytes
        and is filtered out programmatically inside select_best when it exceeds size limits,
        even if size_bytes is not explicitly passed to the constructor.
        """
        from src.core.models import QualityProfile

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"index": 0}'

        mock_llm = AsyncMock()
        mock_llm.completion = AsyncMock(return_value=mock_response)

        mock_breaker = MagicMock()
        mock_breaker.call = AsyncMock(return_value=mock_response)

        service = TorrentSelectionService(
            llm_client=mock_llm,
            circuit_breaker=mock_breaker,
        )

        # Build SearchResults WITHOUT passing size_bytes to simulate scraper output
        results = [
            SearchResult(title="Show.S01E01.1080p.3.5GB.WEB-DL", seeders=200, magnet="magnet:?xt=abc", size="3.5 GB"),
            SearchResult(title="Show.S01E01.1080p.15GB.WEB-DL", seeders=150, magnet="magnet:?xt=def", size="15 GB"),
        ]

        # Verify our validator worked on construction
        assert results[0].size_bytes is not None
        assert results[1].size_bytes is not None

        profile_7gb = QualityProfile(max_file_size_mb=7168)
        result = await service.select_best("Show", "S01E01", results, "en", quality_profile=profile_7gb)

        assert result is not None
        assert result["title"] == "Show.S01E01.1080p.3.5GB.WEB-DL"

    @pytest.mark.asyncio
    async def test_select_best_with_category_guidance(self):
        """Verifies that select_best retrieves and uses category-specific guidance
        from the CategoryRegistry when generating the LLM prompt.
        """
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"index": 0}'

        mock_llm = AsyncMock()
        mock_llm.completion = AsyncMock(return_value=mock_response)

        mock_breaker = MagicMock()
        mock_breaker.call = AsyncMock(return_value=mock_response)

        from src.core.categories.registry import CategoryRegistry
        cat_registry = CategoryRegistry.with_defaults()

        service = TorrentSelectionService(
            llm_client=mock_llm,
            circuit_breaker=mock_breaker,
            category_registry=cat_registry,
        )

        results = [
            SearchResult(title="Gladiator.1080p.3.5GB.WEB-DL", seeders=200, magnet="magnet:?xt=abc", size="3.5 GB"),
        ]

        result = await service.select_best(
            item_name="Gladiator",
            episodes="2000",
            results=results,
            preferred_language="en",
            media_category="movie",
            category_id="movie"
        )

        assert result is not None
        assert mock_breaker.call.called
        call_args = mock_breaker.call.call_args[1]
        prompt = call_args["messages"][0]["content"]
        
        # Verify the movie category selection guidance is injected
        movie_cat = cat_registry.get("movie")
        expected_guidance = movie_cat.build_torrent_selection_guidance()
        assert expected_guidance in prompt