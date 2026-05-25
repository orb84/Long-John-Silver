"""
Robustness and Smart Media Action Integration Tests for LJS.
"""

import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta

from src.core.categories.tv import TvShowCategory
from src.core.categories.movie import MovieCategory
from src.ai.tools.library import EnquireAboutMediaTool
from src.core.models import Settings, TvShowItem, Intent, AgentPlan, PlanStep, DownloadStatus, DownloadItem
from src.ai.plan_coordinator import PlanCoordinator
from src.ai.tool_executor import ToolCallExecutor
from src.core.downloader import DownloadManager, DownloadDependencies
from src.core.torrent_resolver import TorrentUrlResolver
from src.utils.bencode import BencodeDecoder


@pytest.mark.asyncio
class TestEnquireAboutMedia:
    """Test category-agnostic enquire and SQLite 24h caching."""

    async def test_tv_show_enquire_fresh_cache(self) -> None:
        """TvShowCategory.enquire should use cached metadata if it is under 24 hours old."""
        settings = Settings()
        settings.tracked_items = [
            TvShowItem(key="Firefly", language="Italian", enabled=True)
        ]
        
        # Fresh cache (1 hour old)
        fresh_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        
        db = MagicMock()
        db.media = AsyncMock()
        db.media.get_downloaded_episodes.return_value = []
        
        cached_meta = MagicMock()
        cached_meta.enriched_at = fresh_time
        cached_meta.tmdb_id = 1434
        cached_meta.overview = "Fresh Sci-Fi overview"
        cached_meta.genres = ["Sci-Fi"]
        db.media.get_show_metadata.return_value = cached_meta
        
        category = TvShowCategory()
        
        # Mock client / network calls to ensure none are made!
        with patch("src.integrations.tmdb.TMDBClient") as mock_client_cls:
            res = await category.enquire("Firefly", settings, db)
            
            # Assertions
            assert res["item_name"] == "Firefly"
            assert res["configured_language"] == "Italian"
            assert res["overview"] == "Fresh Sci-Fi overview"
            assert not mock_client_cls.called, "Should NOT call TMDB if cache is fresh"

    async def test_tv_show_enquire_stale_cache(self) -> None:
        """TvShowCategory.enquire should refresh metadata from TMDB if cache is stale (>24 hours)."""
        settings = Settings(tmdb_api_key="fake_key")
        settings.tracked_items = [
            TvShowItem(key="Firefly", language="Italian", enabled=True)
        ]
        
        # Stale cache (25 hours old)
        stale_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        
        db = MagicMock()
        db.media = AsyncMock()
        db.media.get_downloaded_episodes.return_value = []
        
        cached_meta = MagicMock()
        cached_meta.enriched_at = stale_time
        cached_meta.tmdb_id = 1434
        db.media.get_show_metadata.return_value = cached_meta
        
        category = TvShowCategory()
        
        # Mock TMDB refresh flow
        refreshed_meta = MagicMock()
        refreshed_meta.tmdb_id = 1434
        refreshed_meta.overview = "Refreshed Sci-Fi overview"
        refreshed_meta.genres = ["Sci-Fi"]
        
        with patch("src.core.categories.metadata.enricher.TMDBMetadataEnricher.enrich_series", return_value=refreshed_meta):
            with patch("src.integrations.tmdb.TMDBClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.get_tv_details.return_value = {
                    "status": "Ended",
                    "number_of_seasons": 1,
                    "number_of_episodes": 14,
                    "seasons": [{"season_number": 1}]
                }
                mock_client.get_tv_season_details.return_value = {
                    "episodes": [{"episode_number": 1, "name": "Serenity", "air_date": "2002-09-20"}]
                }
                mock_client_cls.return_value = mock_client
                
                res = await category.enquire("Firefly", settings, db)
                
                # Assertions
                assert res["item_name"] == "Firefly"
                assert res["overview"] == "Refreshed Sci-Fi overview"
                assert db.media.upsert_show_metadata.called, "Should upsert refreshed metadata to cache"


@pytest.mark.asyncio
class TestLanguagePreferencePrimacy:
    """Test programmatic language preference primacy and overrides."""

    async def test_enforce_language_preference(self) -> None:
        """PlanCoordinator should enforce the tracked show's language if no explicit override is in the prompt."""
        settings = Settings()
        settings.tracked_items = [
            TvShowItem(key="Firefly", language="Italian", enabled=True)
        ]
        
        tool_executor = MagicMock()
        tool_executor._tool_registry.get_definitions.return_value = {}
        
        llm_client = MagicMock()
        
        coordinator = PlanCoordinator(tool_executor, llm_client, settings=settings)
        
        # Mock planner response
        agent_plan = AgentPlan(
            intent=Intent.DOWNLOAD,
            user_goal="Download Firefly S01E01",
            constraints={"language": "English"},
            steps=[
                PlanStep(
                    id="step1",
                    tool_name="search_media_torrents",
                    arguments={"name": "Firefly", "season": 1, "episode": 1, "language": "English"},
                    success_condition="Torrent list received"
                )
            ]
        )
        
        with patch("src.ai.reasoning.ReasoningPlanner.generate_plan", return_value=agent_plan):
            res_plan, _, _ = await coordinator.prepare_plan(
                user_prompt="Download Firefly S01E01",
                intent=Intent.DOWNLOAD,
                system_prompt_content="Base system prompt",
                allowed_tool_names=set()
            )
            
            assert res_plan is not None
            assert res_plan.constraints["language"] == "Italian"
            assert res_plan.steps[0].arguments["language"] == "Italian"

    async def test_explicit_override_prevails(self) -> None:
        """PlanCoordinator should allow the user to explicitly override the tracked show's preference if specified in the prompt."""
        settings = Settings()
        settings.tracked_items = [
            TvShowItem(key="Firefly", language="Italian", enabled=True)
        ]
        
        tool_executor = MagicMock()
        tool_executor._tool_registry.get_definitions.return_value = {}
        
        llm_client = MagicMock()
        
        coordinator = PlanCoordinator(tool_executor, llm_client, settings=settings)
        
        agent_plan = AgentPlan(
            intent=Intent.DOWNLOAD,
            user_goal="Download Firefly S01E01 in French",
            constraints={"language": "French"},
            steps=[
                PlanStep(
                    id="step1",
                    tool_name="search_media_torrents",
                    arguments={"name": "Firefly", "season": 1, "episode": 1, "language": "French"},
                    success_condition="Torrent list received"
                )
            ]
        )
        
        with patch("src.ai.reasoning.ReasoningPlanner.generate_plan", return_value=agent_plan):
            res_plan, _, _ = await coordinator.prepare_plan(
                user_prompt="Download Firefly S01E01 in French",
                intent=Intent.DOWNLOAD,
                system_prompt_content="Base system prompt",
                allowed_tool_names=set()
            )
            
            assert res_plan is not None
            # Enforce "French" constraint because it was explicitly requested in the prompt
            assert res_plan.constraints["language"] == "French"
            assert res_plan.steps[0].arguments["language"] == "French"


@pytest.mark.asyncio
class TestDownloadRetry:
    """Test that cancelled/failed downloads can be re-queued."""

    async def test_retry_on_cancelled_status(self) -> None:
        """Downloader.add_magnet should allow cancelled or failed downloads to proceed (bypassing duplicate checks)."""
        db = MagicMock()
        db.downloads = AsyncMock()
        
        # Existing download is in CANCELLED state
        cancelled_item = DownloadItem(
            id="test_id",
            item_name="Firefly",
            magnet="magnet:?xt=urn:btih:1234",
            status=DownloadStatus.CANCELLED,
            season=1,
            episode=1
        )
        db.downloads.get_download.return_value = cancelled_item
        
        queue = MagicMock()
        queue.active_count = MagicMock(return_value=0)
        
        deps = DownloadDependencies(
            download_dir="fake_dir",
            db=db,
            supervisor=MagicMock(),
            engine=AsyncMock(),
            queue=queue,
            bandwidth=MagicMock(),
            settings_manager=MagicMock(),
            torrent_resolver=MagicMock()
        )
        
        downloader = DownloadManager(deps)
        
        # We patch get_add_lock to return a fake lock
        fake_lock = AsyncMock()
        downloader._start_coordinator.get_add_lock = MagicMock(return_value=fake_lock)
        
        # Mock session to ensure we don't try to add to actual libtorrent
        downloader._session = MagicMock()
        
        res = await downloader.add_magnet(
            item_name="Firefly",
            magnet_link="magnet:?xt=urn:btih:1234",
            season=1,
            episode=1
        )
        
        # Assert that it queued/started a new item instead of returning the cancelled one as a blocked duplicate
        assert res.status in (DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING)
        assert db.downloads.upsert_download.called


@pytest.mark.asyncio
class TestRedirectInterception:
    """Test that UnsupportedProtocol redirects are correctly resolved to magnets."""

    async def test_intercept_unsupported_protocol_redirect(self) -> None:
        """resolve_to_magnet should intercept UnsupportedProtocol exceptions and return the redirect magnet URI."""
        resolver = TorrentUrlResolver(decoder=BencodeDecoder())
        
        # We mock httpx.AsyncClient.get to raise httpx.UnsupportedProtocol with a request target URL
        req = httpx.Request("GET", "magnet:?xt=urn:btih:abcdef123456")
        exc = httpx.UnsupportedProtocol("Unsupported", request=req)
        
        with patch("httpx.AsyncClient.get", side_effect=exc):
            res = await resolver.resolve_to_magnet("http://example.com/torrent-redirect")
            assert res == "magnet:?xt=urn:btih:abcdef123456"


@pytest.mark.asyncio
class TestGenericMediaEnquiryToolRegression:
    """Test the supported generic media status path."""

    async def test_enquire_about_media_fetches_missing_aired_episodes(self) -> None:
        """EnquireAboutMediaTool should delegate TV state to category enquiry semantics."""
        from src.core.models import ToolExecutionContext

        db = MagicMock()
        db.media = AsyncMock()
        db.media.get_downloaded_episodes.return_value = []
        db.media.get_item_progress.return_value = None
        db.media.get_category_item_paused.return_value = False

        db.downloads = AsyncMock()
        db.downloads.get_upgrade_candidates.return_value = []

        settings = Settings()
        settings.tracked_items = [
            TvShowItem(key="Firefly", language="Italian", enabled=True)
        ]

        settings_manager = MagicMock()
        settings_manager.settings = settings

        tool = EnquireAboutMediaTool(settings_manager=settings_manager, database=db)
        enquiry_mock = {
            "item_name": "Firefly",
            "missing_aired_episodes_count": 5,
            "missing_aired_episodes": [
                {"season": 1, "episode": 4, "title": "Shindig", "air_date": "2002-11-01"}
            ],
        }

        with patch("src.core.categories.tv.TvShowCategory.enquire", return_value=enquiry_mock):
            res = await tool.execute(
                {"item_name": "Firefly", "category_id": "tv"},
                ToolExecutionContext(),
            )

        assert res["item_name"] == "Firefly"
        assert res["missing_aired_episodes_count"] == 5
        assert len(res["missing_aired_episodes"]) == 1
        assert res["missing_aired_episodes"][0]["title"] == "Shindig"


class TestRetiredToolSurface:
    """Verify the retired TV-specific status tool is absent from active code."""

    def test_retired_tool_not_exported_by_downloads(self) -> None:
        """Download tools should only expose active download-domain tools."""
        import src.ai.tools.downloads as downloads

        retired_symbol = "Show" + "Episodes" + "Tool"
        assert not hasattr(downloads, retired_symbol)

    def test_retired_tool_not_in_download_provider(self) -> None:
        """DownloadToolProvider should expose generic media enquiry elsewhere."""
        from src.ai.tools.downloads import DownloadToolProvider

        retired_name = "show" + "_episodes"
        assert retired_name not in [tool.name for tool in DownloadToolProvider().get_tools()]

    def test_registry_skips_compatibility_only_objects(self) -> None:
        """ToolRegistry should not register compatibility-only objects accidentally."""
        from src.ai.tool_registry import ToolRegistry

        class CompatibilityOnlyTool:
            """Local stand-in for a retired compatibility-only tool."""

            name = "show" + "_episodes"
            compatibility_only = True

        registry = ToolRegistry()
        registry.register_tool(CompatibilityOnlyTool())
        assert CompatibilityOnlyTool.name not in registry.get_tool_names()
