"""
Download router for LJS.

Provides REST API endpoints for managing downloads: queue listing,
pause/resume, priority changes, file-level priority, restart, cancel,
and manual magnet upload.

All mutation endpoints now delegate to ActionGateway rather than
calling the downloader directly. This ensures every UI action is
audited and follows the same pipeline as chat tool calls.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from src.core.models import ActionCommand, ActionSource
from src.core.models import DownloadPriority
from src.web.dependencies import WebDependencies, verify_auth
from src.web.view_models.download_view_model import DownloadViewModelBuilder


class DownloadsRouter:
    """Class-based router for download management endpoints."""

    def __init__(self, deps: WebDependencies) -> None:
        self._deps = deps
        self._view_model_builder = DownloadViewModelBuilder(deps.downloader)

    def get_router(self) -> APIRouter:
        """Build and return an APIRouter with download-related endpoints."""
        router = APIRouter()
        router.add_api_route("/api/downloads/queue", self._get_download_queue, methods=["GET"])
        router.add_api_route("/api/downloads/recent", self._get_recent_downloads, methods=["GET"])
        router.add_api_route("/api/downloads/{download_id}", self._get_download, methods=["GET"])
        router.add_api_route("/api/downloads/{download_id}/pause", self._pause_download, methods=["POST"])
        router.add_api_route("/api/downloads/{download_id}/resume", self._resume_download, methods=["POST"])
        router.add_api_route("/api/downloads/{download_id}/priority", self._set_download_priority, methods=["POST"])
        router.add_api_route("/api/downloads/{download_id}/file-priority", self._set_file_priority, methods=["POST"])
        router.add_api_route("/api/downloads/{download_id}/restart", self._restart_download, methods=["POST"])
        router.add_api_route("/api/downloads/{download_id}/cancel", self._cancel_download, methods=["POST"])
        router.add_api_route("/api/downloads", self._get_all_downloads, methods=["GET"])
        router.add_api_route("/api/downloads/upload", self._upload_torrent, methods=["POST"])
        return router

    async def _execute_action(self, name: str, arguments: dict) -> dict:
        """Execute an action through the gateway and return the data dict.

        Raises HTTPException on failure with an appropriate status code.
        """
        result = await self._deps.action_gateway.execute(ActionCommand(
            name=name,
            arguments=arguments,
            source=ActionSource.UI,
        ))
        if not result.ok:
            code = 404 if 'not found' in (result.error or '').lower() else 400
            raise HTTPException(status_code=code, detail=result.error or 'Action failed')
        return result.data

    async def _get_download_queue(self):
        """Return the current download queue ordered by priority."""
        queued = await self._deps.downloader.get_queued_downloads()
        return {"queue": [self._view_model_builder.build(d) for d in queued]}

    async def _get_download(self, download_id: str):
        """Return details for a single download."""
        item = await self._deps.downloader.get_download(download_id)
        if not item:
            raise HTTPException(status_code=404, detail="Download not found")
        return self._view_model_builder.build(item)

    async def _pause_download(self, download_id: str, _auth: bool = Depends(verify_auth)):
        """Pause an active or queued download.

        Delegates to ActionGateway for unified audit and event emission.
        """
        data = await self._execute_action('pause_download', {'download_id': download_id})
        if not data:
            raise HTTPException(status_code=404, detail='Download not found or cannot be paused')
        self._deps.event_bus.emit_dl_event('paused', download_id, {'download': data})
        return data

    async def _resume_download(self, download_id: str, _auth: bool = Depends(verify_auth)):
        """Resume a paused download."""
        data = await self._execute_action('resume_download', {'download_id': download_id})
        if not data:
            raise HTTPException(status_code=404, detail='Download not found or cannot be resumed')
        self._deps.event_bus.emit_dl_event('resumed', download_id, {'download': data})
        return data

    async def _set_download_priority(self, download_id: str, request: Request, _auth: bool = Depends(verify_auth)):
        """Change the priority of a queued or paused download.

        Expects JSON body: {"priority": "high"|"normal"|"low"}
        """
        from src.core.models import DownloadPriority as DP
        body = await request.json()
        priority_str = body.get("priority", "normal").lower()
        try:
            priority = DP(priority_str)
        except ValueError:
            raise HTTPException(status_code=400, detail='Invalid priority. Use: high, normal, low')
        data = await self._execute_action('download_set_priority', {
            'download_id': download_id, 'priority': priority,
        })
        if not data:
            raise HTTPException(status_code=404, detail='Download not found or priority cannot be changed')
        self._deps.event_bus.emit_dl_event('priority_changed', download_id, {'download': data})
        return data

    async def _set_file_priority(self, download_id: str, request: Request, _auth: bool = Depends(verify_auth)):
        """Change the download priority of a single file within a multi-file torrent.

        Expects JSON body: {"file_index": int, "priority": int}
        Priority values: 0=ignore, 1=lowest, 4=normal, 7=maximum.
        """
        body = await request.json()
        file_index = body.get('file_index')
        priority = body.get('priority')

        if file_index is None or priority is None:
            raise HTTPException(status_code=400, detail='file_index and priority are required')
        if not isinstance(file_index, int) or not isinstance(priority, int):
            raise HTTPException(status_code=400, detail='file_index and priority must be integers')
        if priority < 0 or priority > 7:
            raise HTTPException(status_code=400, detail='priority must be 0-7')

        data = await self._execute_action('set_file_priority', {
            'download_id': download_id, 'file_index': file_index, 'priority': priority,
        })
        if not data.get('value'):
            raise HTTPException(status_code=404, detail='Download not found')
        self._deps.event_bus.emit_dl_event('file_priority_changed', download_id, {
            'file_index': file_index, 'priority': priority,
        })
        return {'status': 'ok', 'file_index': file_index, 'priority': priority}

    async def _restart_download(self, download_id: str, _auth: bool = Depends(verify_auth)):
        """Re-queue a failed or cancelled download for a fresh attempt."""
        data = await self._execute_action('restart_download', {'download_id': download_id})
        if not data:
            raise HTTPException(status_code=404, detail='Download not found or cannot be restarted')
        self._deps.event_bus.emit_dl_event('restarted', download_id, {'download': data})
        return data

    async def _cancel_download(self, download_id: str, _auth: bool = Depends(verify_auth)):
        """Cancel an active download and optionally clean up partial files."""
        await self._execute_action('cancel_download', {'download_id': download_id})
        self._deps.event_bus.emit_dl_event('cancelled', download_id)
        return {'status': 'cancelled', 'download_id': download_id}

    async def _get_all_downloads(self):
        """Return active downloads (used by the show detail modal)."""
        active = await self._deps.downloader.get_active_downloads()
        return {"active": [self._view_model_builder.build(d) for d in active]}

    async def _get_recent_downloads(self):
        """Return recently completed or failed downloads."""
        recent = await self._deps.downloader.get_recent_downloads(10)
        return {"downloads": [self._view_model_builder.build(r) for r in recent]}

    async def _upload_torrent(self, request: Request, _auth: bool = Depends(verify_auth)):
        """Add a download via manual magnet link."""
        body = await request.json()
        magnet = body.get("magnet")
        item_name = body.get("item_name", "Manual Upload")

        if not magnet:
            return JSONResponse(status_code=400, content={"error": "Missing magnet link"})

        return await self._execute_action('download_upload', {
            'magnet': magnet, 'item_name': item_name,
        })
