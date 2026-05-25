#!/usr/bin/env python3
"""Round 18 architecture/documentation audit.

Checks the OOP cleanup for chat download control, Python public docstrings,
frontend JSDoc coverage, and a small behavior smoke test for manage_downloads
without requiring optional runtime packages such as libtorrent or aiosqlite.
"""

from __future__ import annotations

import ast
import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from scripts.check_public_docs import PublicDocumentationAudit
from src.ai.tools.download_control import ManageDownloadsTool
from src.core.models import DownloadPriority, DownloadStatus, ToolExecutionContext

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class FakeDownload:
    """Minimal DownloadItem-compatible object for tool smoke tests."""

    id: str
    item_name: str
    torrent_title: str
    status: DownloadStatus
    priority: DownloadPriority
    season: int | None = None
    episode: int | None = None
    progress: float = 0.0
    created_at: datetime = datetime(2026, 1, 1)
    download_rate: float = 0.0
    upload_rate: float = 0.0
    num_peers: int = 0
    num_seeds: int = 0
    total_size: int = 0
    downloaded_bytes: int = 0
    category_id: str = "tv"
    item_id: str = "for-all-mankind"
    language: str = "Italian"
    reason: str = "manual"


class FakeDownloader:
    """Small public downloader test double for manage_downloads."""

    def __init__(self) -> None:
        """Create a fake queue with two episodic downloads."""
        self.items = [
            FakeDownload("d1", "For All Mankind", "S05E03 release", DownloadStatus.QUEUED, DownloadPriority.NORMAL, 5, 3),
            FakeDownload("d2", "For All Mankind", "S05E04 release", DownloadStatus.QUEUED, DownloadPriority.NORMAL, 5, 4),
        ]
        self.paused: list[str] = []

    async def get_active_downloads(self) -> list[FakeDownload]:
        """Return the fake active queue."""
        return self.items

    async def pause_download(self, download_id: str) -> FakeDownload | None:
        """Mark a fake item as paused and return the updated item."""
        self.paused.append(download_id)
        for item in self.items:
            if item.id == download_id:
                item.status = DownloadStatus.PAUSED
                return item
        return None

    async def resume_download(self, download_id: str) -> FakeDownload | None:
        """Mark a fake item as queued and return the updated item."""
        for item in self.items:
            if item.id == download_id:
                item.status = DownloadStatus.QUEUED
                return item
        return None

    async def cancel_download(self, download_id: str, cleanup_files: bool = True) -> None:
        """Remove a fake item from the queue."""
        self.items = [item for item in self.items if item.id != download_id]

    async def set_priority(self, download_id: str, priority: DownloadPriority) -> FakeDownload | None:
        """Set priority on a fake queue item."""
        for item in self.items:
            if item.id == download_id:
                item.priority = priority
                return item
        return None

    async def update_download(self, item: FakeDownload) -> None:
        """Persist a fake item in place."""
        return None


async def exercise_manage_downloads() -> None:
    """Smoke-test dry-run, confirmation, and mutation behavior."""
    downloader = FakeDownloader()
    tool = ManageDownloadsTool(downloader)
    context = ToolExecutionContext(session_id="audit", user_id="audit")
    dry = await tool.execute({
        "action": "pause",
        "filters": {"name": "For All", "season": 5},
        "selection": "next_episode",
        "dry_run": True,
    }, context)
    assert dry["status"] == "dry_run" and dry["matched"][0]["episode"] == 3
    confirm = await tool.execute({"action": "cancel", "filters": {"id": "d1"}}, context)
    assert confirm["status"] == "confirmation_required"
    result = await tool.execute({"action": "pause", "filters": {"id": "d1"}}, context)
    assert result["status"] == "ok" and downloader.paused == ["d1"]


class Round18Audit:
    """Coordinate static and behavior checks for the Round 18 cleanup."""

    def run(self) -> None:
        """Run all Round 18 checks and raise AssertionError on regressions."""
        assert PublicDocumentationAudit().run() == 0
        self.assert_download_tools_are_decomposed()
        asyncio.run(exercise_manage_downloads())
        print("round18 architecture/documentation audit passed")

    def assert_download_tools_are_decomposed(self) -> None:
        """Ensure the download control layer stays split into focused classes."""
        downloads = ROOT / "src/ai/tools/downloads.py"
        control = ROOT / "src/ai/tools/download_control.py"
        assert sum(1 for _ in downloads.open(encoding="utf-8")) < 800, "downloads.py should remain below the file-size limit"
        tree = ast.parse(control.read_text(encoding="utf-8"), filename=str(control))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                span = (node.end_lineno or node.lineno) - node.lineno + 1
                assert span <= 200, f"{node.name} should stay focused; found {span} lines"
        assert "DownloadFilterResolver" in control.read_text(encoding="utf-8")
        assert "DownloadControlActionService" in control.read_text(encoding="utf-8")


if __name__ == "__main__":
    Round18Audit().run()
