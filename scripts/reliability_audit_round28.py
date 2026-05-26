#!/usr/bin/env python3
"""Round 28 audit for completed-download library import reliability.

The audit avoids optional database drivers. It verifies the bug class reported
by the user: completed torrents must not remain stranded in the download folder
when ready/completion callbacks are missed or settings are needed for category
path planning.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.download_handler import DownloadCompletionHandler
from src.core.models import DownloadFileInfo, DownloadItem, DownloadStatus, Settings


class FakeDownloader:
    """In-memory downloader facade for callback/audit execution."""

    def __init__(self, item: DownloadItem) -> None:
        self.item = item
        self.updates = 0

    async def get_download(self, download_id: str) -> DownloadItem | None:
        """Return the stored item when the ID matches."""
        return self.item if self.item.id == download_id else None

    async def get_recent_downloads(self, limit: int = 200) -> list[DownloadItem]:
        """Return the stored item as a recent download row."""
        return [self.item]

    async def update_download(self, item: DownloadItem) -> None:
        """Persist the updated item in memory."""
        self.item = item
        self.updates += 1


class FakeNotifications:
    """Notification sink that records no external side effects."""

    async def send_download_complete(self, item_name: str, season: int | None, episode: int | None) -> None:
        """Accept a completion notification."""
        return None


class SettingsAwareCategory:
    """Category that requires a non-None settings object for path planning."""

    category_id = "tv"

    def __init__(self, root: Path) -> None:
        self.root = root

    def get_root_path(self, settings: Settings) -> str:
        """Return root only after touching settings.category_settings."""
        settings.category_settings
        return str(self.root)

    def compute_target_path(self, source_name: str, item_name: str, season: int, episode: int, **kwargs) -> Path:
        """Compute a target path while requiring live settings injection."""
        kwargs["settings"].category_settings
        return Path(kwargs["library_root"]) / item_name / f"Season {season:02d}" / source_name


class FakeRegistry:
    """Single-category registry used by the audit."""

    def __init__(self, category: SettingsAwareCategory) -> None:
        self.category = category

    def get(self, category_id: str) -> SettingsAwareCategory | None:
        """Return the category by ID."""
        return self.category if category_id == self.category.category_id else None


class MovingLibrarian:
    """Category organizer fake that moves files into a library root."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.moves: list[tuple[Path, Path]] = []

    def organize_file(self, source: Path, item_name: str = "", season: int | None = None, episode: int | None = None, **kwargs) -> Path:
        """Move a source into a deterministic test library path."""
        dest_dir = self.root / item_name / f"Season {int(season or 1):02d}"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / source.name
        source.rename(dest)
        self.moves.append((source, dest))
        return dest


class Round28CompletionImportAudit:
    """Run completed-download import regression checks."""

    def run(self) -> int:
        """Run all checks and return a shell-friendly exit code."""
        failures: list[str] = []
        try:
            asyncio.run(self._check_ready_callback_uses_live_settings())
        except Exception as exc:  # pragma: no cover - shell audit reporting
            failures.append(f"ready callback settings/import check failed: {exc}")
        try:
            asyncio.run(self._check_completion_moves_all_multifile_payloads())
        except Exception as exc:  # pragma: no cover - shell audit reporting
            failures.append(f"completion fallback multifile check failed: {exc}")
        failures.extend(self._check_static_startup_reconcile_wiring())
        if failures:
            print("Round 28 completion/import audit failed:")
            print("\n".join(f"- {failure}" for failure in failures))
            return 1
        print("Round 28 completion/import audit passed.")
        return 0

    async def _check_ready_callback_uses_live_settings(self) -> None:
        """Ensure ready-time path planning does not receive settings=None."""
        with tempfile.TemporaryDirectory() as tmp_text:
            tmp = Path(tmp_text)
            downloads = tmp / "downloads"
            library = tmp / "library"
            downloads.mkdir()
            source = downloads / "Show.S01E01.mkv"
            source.write_bytes(b"episode")
            item = DownloadItem(
                id="dl1",
                item_name="Show",
                magnet="magnet:?xt=urn:btih:test",
                status=DownloadStatus.SEEDING,
                progress=1.0,
                category_id="tv",
                save_path=str(downloads),
                files=[DownloadFileInfo(file_index=0, file_path=source.name, size=7, status="complete", season=1, episode=1)],
            )
            downloader = FakeDownloader(item)
            handler = DownloadCompletionHandler(
                downloader=downloader,
                librarian=MovingLibrarian(library),
                notifications=FakeNotifications(),
                category_registry=FakeRegistry(SettingsAwareCategory(library)),
                settings=None,
                settings_manager=SimpleNamespace(settings=Settings()),
                download_dir=downloads,
            )
            await handler.on_download_ready("dl1")
            organized = downloader.item.files[0].organized_path
            assert organized, "ready callback did not record organized_path"
            assert Path(organized).exists(), "ready callback target does not exist"

    async def _check_completion_moves_all_multifile_payloads(self) -> None:
        """Ensure missed ready callbacks are repaired for every completed file."""
        with tempfile.TemporaryDirectory() as tmp_text:
            tmp = Path(tmp_text)
            downloads = tmp / "downloads"
            library = tmp / "library"
            downloads.mkdir()
            for name, payload in (("Show.S01E01.mkv", b"ep1"), ("Show.S01E02.mkv", b"ep2")):
                (downloads / name).write_bytes(payload)
            item = DownloadItem(
                id="dl2",
                item_name="Show",
                magnet="magnet:?xt=urn:btih:test2",
                status=DownloadStatus.COMPLETE,
                progress=1.0,
                category_id="tv",
                save_path=str(downloads),
                files=[
                    DownloadFileInfo(file_index=0, file_path="Show.S01E01.mkv", size=3, status="complete", season=1, episode=1),
                    DownloadFileInfo(file_index=1, file_path="Show.S01E02.mkv", size=3, status="complete", season=1, episode=2),
                ],
            )
            librarian = MovingLibrarian(library)
            handler = DownloadCompletionHandler(
                downloader=FakeDownloader(item),
                librarian=librarian,
                notifications=FakeNotifications(),
                category_registry=FakeRegistry(SettingsAwareCategory(library)),
                settings=Settings(),
                download_dir=downloads,
            )
            repaired = await handler.reconcile_completed_imports()
            assert repaired == 1, f"expected one repaired row, got {repaired}"
            assert len(librarian.moves) == 2, "not every file in the completed torrent was moved"

    def _check_static_startup_reconcile_wiring(self) -> list[str]:
        """Ensure main startup calls the repair pass after recovery."""
        text = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")
        if "await completion_handler.reconcile_completed_imports()" not in text:
            return ["main.py does not run completed-import reconciliation after download recovery"]
        handler = (PROJECT_ROOT / "src/core/download_handler.py").read_text(encoding="utf-8")
        failures = []
        runtime_header = handler.split("if TYPE_CHECKING:", 1)[0]
        if "from src.core.downloader import DownloadManager" in runtime_header:
            failures.append("download_handler imports DownloadManager at runtime, pulling optional DB deps into import-only tests")
        if "relative_to(root.resolve())" not in handler:
            failures.append("path containment helper does not use Path.relative_to")
        return failures


if __name__ == "__main__":
    sys.exit(Round28CompletionImportAudit().run())
