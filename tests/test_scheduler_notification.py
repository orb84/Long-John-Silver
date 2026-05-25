"""
Regression tests for MediaScheduler stalled download notification formatting.

Verifies that the generated stalled download message contains proper newline
characters (\\n) and not escaped backslash-n strings (\\\\n).
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

from src.core.models import (
    Settings,
    DownloadItem,
    DownloadStatus,
    TvShowItem,
    SearchResult,
)
from src.core.config import SettingsManager
from src.core.database import Database
from src.core.downloader import DownloadManager
from src.search.aggregator import SearchAggregator
from src.core.librarian import Librarian
from src.core.notifications import NotificationService
from src.utils.library_scanner import LibraryScanner
from src.core.smart_quality import SmartQualityInferrer
from src.core.scheduler import SchedulerDependencies, MediaScheduler


@pytest.mark.asyncio
async def test_stalled_download_notification_formatting():
    """Verify that stalled download messages use proper unescaped newlines."""
    # 1. Setup mocked settings and dependencies
    settings = Settings()
    settings.stall_alternative_hours = 1.0
    settings.stall_cancel_hours = 24.0
    settings.tracked_items = [
        TvShowItem(key="Project Hail Mary", language="English", enabled=True)
    ]

    settings_manager = MagicMock(spec=SettingsManager)
    settings_manager.settings = settings

    db = MagicMock(spec=Database)
    db.media = AsyncMock()
    db.media.get_category_item_paused.return_value = False

    # Mock stalled download item created 2 hours ago (elapsed > stall_alternative_hours)
    stalled_dl = DownloadItem(
        id="dl_123",
        item_name="Project Hail Mary",
        magnet="magnet:?xt=urn:btih:stalled123",
        status=DownloadStatus.DOWNLOADING,
        season=1,
        episode=1,
        downloaded_bytes=0,
        created_at=datetime.now(timezone.utc) - timedelta(hours=2.0),
        stalled_notified=False,
    )

    downloader = MagicMock(spec=DownloadManager)
    downloader.get_active_downloads = AsyncMock(return_value=[stalled_dl])
    downloader.update_download = AsyncMock()

    aggregator = MagicMock(spec=SearchAggregator)
    librarian = MagicMock(spec=Librarian)

    notifications = MagicMock(spec=NotificationService)
    notifications.send_message = AsyncMock()

    scanner = MagicMock(spec=LibraryScanner)
    quality_inferrer = MagicMock(spec=SmartQualityInferrer)

    deps = SchedulerDependencies(
        settings_manager=settings_manager,
        db=db,
        downloader=downloader,
        aggregator=aggregator,
        librarian=librarian,
        notifications=notifications,
        scanner=scanner,
        quality_inferrer=quality_inferrer,
    )

    scheduler = MediaScheduler(deps)

    # Mock SearchPipeline.run_search to return some mock torrent candidate options
    mock_candidates = [
        SearchResult(
            title="Project.Hail.Mary.S01E01.1080p.WEBDL-CYBER",
            magnet="magnet:?xt=urn:btih:cyber123",
            size="2.3 GB",
            seeders=25,
            provider="btdigg",
        ),
        SearchResult(
            title="Project.Hail.Mary.S01E01.720p.WEBDL-CYBER",
            magnet="magnet:?xt=urn:btih:cyber456",
            size="1.2 GB",
            seeders=5,
            provider="jackett",
        ),
    ]
    scheduler._pipeline.run_search = AsyncMock(return_value=mock_candidates)

    # 2. Execute target stalled checks job
    await scheduler._check_stalled_downloads_job()

    # 3. Assertions and verification
    notifications.send_message.assert_called_once()
    args, kwargs = notifications.send_message.call_args
    msg_body = args[0]
    title = kwargs.get("title")

    assert title == "Stalled Download Detected"
    
    # The message must contain real newline characters (\n)
    assert "\n" in msg_body
    
    # The message must NOT contain double-escaped literal '\\n' characters
    assert "\\n" not in msg_body

    # Verify formatting structure is intact
    assert "I found these alternative options. Reply with a number to try adding one of them:" in msg_body
    assert "Project.Hail.Mary.S01E01.1080p.WEBDL-CYBER (2.3 GB, 25 seeders)" in msg_body
    assert "Project.Hail.Mary.S01E01.720p.WEBDL-CYBER (1.2 GB, 5 seeders)" in msg_body
