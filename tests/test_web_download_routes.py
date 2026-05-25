"""
Tests for download API routes.

Verifies that all download endpoints (queue, single, pause, resume,
priority, file-priority, restart, cancel, list, upload) respond with
correct status codes and payload shapes. Uses mocked services.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.core.models import Settings, LLMConfig, ItemList, DownloadItem, DownloadPriority, DownloadStatus, DownloadFileInfo
from src.utils.auth import AuthService
from src.web.app import create_app


def _mock_settings(web_password_hash: str | None = None) -> MagicMock:
    """Create a mock SettingsManager with a given password hash."""
    settings = Settings(
        llm=LLMConfig(model="test", api_key="test"),
        tracked_items=ItemList(items=[]),
        download_dir="/tmp/test",
        web_password_hash=web_password_hash,
        setup_complete=True,
        trakt_client_id="",
    )
    mgr = MagicMock()
    mgr.settings = settings
    return mgr


def _make_app(settings_mgr, auth_service, downloader=None):
    """Create a bare-bones FastAPI app with mocked dependencies."""
    if downloader is None:
        downloader = AsyncMock()
    return create_app(
        settings_manager=settings_mgr,
        db=AsyncMock(),
        assistant=AsyncMock(),
        downloader=downloader,
        notifications=AsyncMock(),
        auth_service=auth_service,
        llm_manager=MagicMock(),
        scanner=AsyncMock(),
        conversation_manager=MagicMock(),
        behavior_tracker=MagicMock(),
        suggestion_compiler=AsyncMock(),
        recommender=MagicMock(),
        release_group_tracker=MagicMock(),
        comms_registry=MagicMock(),
        torrent_racer=MagicMock(),
        browser_runtime=MagicMock(),
        jackett_manager=MagicMock(),
        scheduler=AsyncMock(),
        supervisor=MagicMock(),
    )


def _make_download_item(
    item_id: str = "dl-001",
    item_name: str = "Test Show",
    status: DownloadStatus = DownloadStatus.DOWNLOADING,
    priority: DownloadPriority = DownloadPriority.NORMAL,
    progress: float = 0.5,
) -> DownloadItem:
    """Create a DownloadItem with minimal required fields."""
    return DownloadItem(
        id=item_id,
        item_name=item_name,
        magnet="magnet:?xt=urn:btih:test",
        status=status,
        priority=priority,
        progress=progress,
        created_at=datetime.now(),
    )


def _make_downloader_mock() -> MagicMock:
    """Create a mock DownloadManager with all methods as AsyncMock."""
    dl = MagicMock()
    dl.get_queued_downloads = AsyncMock(return_value=[])
    dl.get_download = AsyncMock(return_value=None)
    dl.pause_download = AsyncMock(return_value=None)
    dl.resume_download = AsyncMock(return_value=None)
    dl.set_priority = AsyncMock(return_value=None)
    dl.set_file_priority = AsyncMock(return_value=False)
    dl.restart_download = AsyncMock(return_value=None)
    dl.cancel_download = AsyncMock(return_value=None)
    dl.get_active_downloads = AsyncMock(return_value=[])
    dl.add_magnet = AsyncMock(return_value=_make_download_item())
    dl.get_file_progress = MagicMock(return_value=[])
    return dl


class TestDownloadQueue:
    """GET /api/downloads/queue — list queued downloads."""

    def test_empty_queue(self):
        """Should return an empty queue list."""
        auth = AuthService(secret_key="test-secret")
        settings_mgr = _mock_settings()
        app = _make_app(settings_mgr, auth)
        client = TestClient(app)

        resp = client.get("/api/downloads/queue")
        assert resp.status_code == 200
        data = resp.json()
        assert "queue" in data
        assert data["queue"] == []

    def test_queue_with_items(self):
        """Should return queued downloads with enriched file data."""
        auth = AuthService(secret_key="test-secret")
        settings_mgr = _mock_settings()
        dl = _make_downloader_mock()
        item = _make_download_item(item_id="dl-001", item_name="Test Show", status=DownloadStatus.QUEUED)
        dl.get_queued_downloads = AsyncMock(return_value=[item])
        app = _make_app(settings_mgr, auth, downloader=dl)
        client = TestClient(app)

        resp = client.get("/api/downloads/queue")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["queue"]) == 1
        assert data["queue"][0]["id"] == "dl-001"
        assert data["queue"][0]["item_name"] == "Test Show"


class TestDownloadSingle:
    """GET /api/downloads/{download_id} — single download detail."""

    def test_get_existing_download(self):
        """Should return the download when it exists."""
        auth = AuthService(secret_key="test-secret")
        settings_mgr = _mock_settings()
        dl = _make_downloader_mock()
        item = _make_download_item(item_id="dl-001")
        dl.get_download = AsyncMock(return_value=item)
        app = _make_app(settings_mgr, auth, downloader=dl)
        client = TestClient(app)

        resp = client.get("/api/downloads/dl-001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "dl-001"

    def test_get_missing_download(self):
        """Should return 404 when the download does not exist."""
        auth = AuthService(secret_key="test-secret")
        settings_mgr = _mock_settings()
        dl = _make_downloader_mock()
        dl.get_download = AsyncMock(return_value=None)
        app = _make_app(settings_mgr, auth, downloader=dl)
        client = TestClient(app)

        resp = client.get("/api/downloads/dl-missing")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()


class TestDownloadActions:
    """POST endpoints for download actions (pause, resume, priority, cancel, etc.).

    All action endpoints require auth, so we either use open access (no password)
    or provide a valid token.
    """

    @pytest.fixture
    def auth_client(self):
        """Return (client, downloader_mock) with auth configured."""
        auth = AuthService(secret_key="test-secret")
        hashed = auth.hash_password("secret")
        settings_mgr = _mock_settings(web_password_hash=hashed)
        dl = _make_downloader_mock()
        app = _make_app(settings_mgr, auth, downloader=dl)
        client = TestClient(app)
        token = auth.create_token("admin")
        client.headers = {"X-Auth-Token": token}
        return client, dl

    def test_pause_download(self, auth_client):
        """POST /api/downloads/{id}/pause should pause and emit event."""
        client, dl = auth_client
        item = _make_download_item(item_id="dl-001", status=DownloadStatus.PAUSED)
        dl.pause_download.return_value = item

        resp = client.post("/api/downloads/dl-001/pause")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "dl-001"
        dl.pause_download.assert_awaited_once_with(download_id="dl-001")

    def test_pause_missing_download(self, auth_client):
        """Should return 404 when pausing a non-existent download."""
        client, dl = auth_client
        dl.pause_download.return_value = None

        resp = client.post("/api/downloads/dl-missing/pause")
        assert resp.status_code == 404

    def test_resume_download(self, auth_client):
        """POST /api/downloads/{id}/resume should resume and emit event."""
        client, dl = auth_client
        item = _make_download_item(item_id="dl-001", status=DownloadStatus.DOWNLOADING)
        dl.resume_download.return_value = item

        resp = client.post("/api/downloads/dl-001/resume")
        assert resp.status_code == 200
        assert resp.json()["id"] == "dl-001"
        dl.resume_download.assert_awaited_once_with(download_id="dl-001")

    def test_resume_missing_download(self, auth_client):
        """Should return 404 when resuming a non-existent download."""
        client, dl = auth_client
        dl.resume_download.return_value = None

        resp = client.post("/api/downloads/dl-missing/resume")
        assert resp.status_code == 404

    def test_set_priority(self, auth_client):
        """POST /api/downloads/{id}/priority should update priority."""
        client, dl = auth_client
        item = _make_download_item(item_id="dl-001", priority=DownloadPriority.HIGH)
        dl.set_priority.return_value = item

        resp = client.post("/api/downloads/dl-001/priority", json={"priority": "high"})
        assert resp.status_code == 200
        assert resp.json()["id"] == "dl-001"
        dl.set_priority.assert_awaited_once()

    def test_set_priority_invalid(self, auth_client):
        """Should return 400 for invalid priority values."""
        client, dl = auth_client

        resp = client.post("/api/downloads/dl-001/priority", json={"priority": "invalid"})
        assert resp.status_code == 400

    def test_set_priority_missing_download(self, auth_client):
        """Should return 404 when setting priority on non-existent download."""
        client, dl = auth_client
        dl.set_priority.return_value = None

        resp = client.post("/api/downloads/dl-missing/priority", json={"priority": "high"})
        assert resp.status_code == 404

    def test_set_file_priority(self, auth_client):
        """POST /api/downloads/{id}/file-priority should set file priority."""
        client, dl = auth_client
        dl.set_file_priority.return_value = True

        resp = client.post(
            "/api/downloads/dl-001/file-priority",
            json={"file_index": 0, "priority": 7},
        )
        assert resp.status_code == 200
        assert resp.json()["file_index"] == 0
        assert resp.json()["priority"] == 7
        dl.set_file_priority.assert_awaited_once_with(download_id="dl-001", file_index=0, priority=7)

    def test_set_file_priority_invalid_index(self, auth_client):
        """Should return 400 when file_index is missing."""
        client, dl = auth_client

        resp = client.post(
            "/api/downloads/dl-001/file-priority",
            json={"priority": 7},
        )
        assert resp.status_code == 400

    def test_set_file_priority_bad_type(self, auth_client):
        """Should return 400 when file_index is not an integer."""
        client, dl = auth_client

        resp = client.post(
            "/api/downloads/dl-001/file-priority",
            json={"file_index": "zero", "priority": 7},
        )
        assert resp.status_code == 400

    def test_set_file_priority_out_of_range(self, auth_client):
        """Should return 400 when priority is out of 0-7 range."""
        client, dl = auth_client

        resp = client.post(
            "/api/downloads/dl-001/file-priority",
            json={"file_index": 0, "priority": 99},
        )
        assert resp.status_code == 400

    def test_restart_download(self, auth_client):
        """POST /api/downloads/{id}/restart should restart and emit event."""
        client, dl = auth_client
        item = _make_download_item(item_id="dl-001", status=DownloadStatus.QUEUED)
        dl.restart_download.return_value = item

        resp = client.post("/api/downloads/dl-001/restart")
        assert resp.status_code == 200
        assert resp.json()["id"] == "dl-001"
        dl.restart_download.assert_awaited_once_with(download_id="dl-001")

    def test_restart_missing_download(self, auth_client):
        """Should return 404 when restarting a non-existent download."""
        client, dl = auth_client
        dl.restart_download.return_value = None

        resp = client.post("/api/downloads/dl-missing/restart")
        assert resp.status_code == 404

    def test_cancel_download(self, auth_client):
        """POST /api/downloads/{id}/cancel should cancel and emit event."""
        client, dl = auth_client

        resp = client.post("/api/downloads/dl-001/cancel")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "cancelled"
        assert data["download_id"] == "dl-001"
        dl.cancel_download.assert_awaited_once_with(download_id="dl-001")

    def test_upload_torrent(self, auth_client):
        """POST /api/downloads/upload should add a magnet and return the id."""
        client, dl = auth_client
        item = _make_download_item(item_id="dl-uploaded")
        dl.add_magnet = AsyncMock(return_value=item)

        resp = client.post(
            "/api/downloads/upload",
            json={"magnet": "magnet:?xt=urn:btih:test", "item_name": "My Movie"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "added"
        assert data["download_id"] == "dl-uploaded"
        dl.add_magnet.assert_awaited_once()

    def test_upload_torrent_missing_magnet(self, auth_client):
        """Should return 400 when magnet link is missing."""
        client, dl = auth_client

        resp = client.post("/api/downloads/upload", json={"item_name": "No Magnet"})
        assert resp.status_code == 400
        assert "Missing magnet link" in resp.json()["error"]

    def test_action_requires_auth(self):
        """Should reject action endpoints with 401 when auth is configured and no token."""
        auth = AuthService(secret_key="test-secret")
        hashed = auth.hash_password("secret")
        settings_mgr = _mock_settings(web_password_hash=hashed)
        app = _make_app(settings_mgr, auth)
        client = TestClient(app)

        resp = client.post("/api/downloads/dl-001/pause")
        assert resp.status_code == 401


class TestAllDownloads:
    """GET /api/downloads — list all active downloads."""

    def test_empty_active(self):
        """Should return an empty active list."""
        auth = AuthService(secret_key="test-secret")
        settings_mgr = _mock_settings()
        app = _make_app(settings_mgr, auth)
        client = TestClient(app)

        resp = client.get("/api/downloads")
        assert resp.status_code == 200
        data = resp.json()
        assert "active" in data
        assert data["active"] == []

    def test_active_with_items(self):
        """Should return active downloads."""
        auth = AuthService(secret_key="test-secret")
        settings_mgr = _mock_settings()
        dl = _make_downloader_mock()
        item = _make_download_item(item_id="dl-001", status=DownloadStatus.DOWNLOADING, progress=0.5)
        dl.get_active_downloads = AsyncMock(return_value=[item])
        app = _make_app(settings_mgr, auth, downloader=dl)
        client = TestClient(app)

        resp = client.get("/api/downloads")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["active"]) == 1
        assert data["active"][0]["id"] == "dl-001"
