#!/usr/bin/env python3
"""Round 153 Soulseek import monitor and managed download-root tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.models import Settings, SoulseekSettings
from src.integrations.slskd_config import build_slskd_share_plan
from src.integrations.slskd_import_monitor import SlskdImportMonitor
from src.integrations.slskd_transfer_view import SlskdTransferReadModel


def test_managed_slskd_uses_ljs_download_root_by_default() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(download_dir=str(Path(tmp) / "Downloads"), library_root=str(Path(tmp) / "Library"))
        settings.soulseek = SoulseekSettings(enabled=True, managed=True, app_dir=str(Path(tmp) / "slskd-app"), downloads_dir="./downloads/soulseek", incomplete_dir="./downloads/soulseek-incomplete")
        plan = build_slskd_share_plan(settings)
        assert Path(plan.downloads_dir) == (Path(tmp) / "Downloads").resolve()
        assert Path(plan.incomplete_dir) == (Path(tmp) / "Downloads" / ".slskd-incomplete").resolve()


class _SystemPrefs:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get_preference(self, key: str, default: str = "") -> str:
        return self.values.get(key, default)

    async def set_preference(self, key: str, value: str) -> None:
        self.values[key] = value


class _DB:
    def __init__(self) -> None:
        self.system = _SystemPrefs()


class _SM:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings


class _Category:
    category_id = "music"


class _Registry:
    def get(self, category_id: str):
        return _Category() if category_id == "music" else None


class _CompletionHandler:
    def __init__(self, library_root: Path, download_root: Path) -> None:
        self.library_root = library_root
        self.download_root = download_root
        self.linked: list[tuple[Path, Path]] = []
        self.cleaned: list[Path] = []

    async def _link_completed_file_to_library(self, source, item, category, settings, *, file_info=None, source_name=None, episode_title=None):
        target = self.library_root / item.item_name / Path(str(source_name or source.name)).name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        self.linked.append((Path(source), target))
        return target

    def _safe_unlink(self, path: Path) -> bool:
        path = Path(path)
        try:
            path.relative_to(self.download_root)
        except Exception:
            return False
        path.unlink()
        self.cleaned.append(path)
        return True

    def _cleanup_empty_download_parents(self, sources, item=None):
        removed = 0
        for source in sources:
            parent = Path(source).parent
            while parent != self.download_root and str(parent).startswith(str(self.download_root)):
                try:
                    parent.rmdir()
                    removed += 1
                except OSError:
                    break
                parent = parent.parent
        return removed


async def test_completed_slskd_transfer_imports_and_cleans_source_async() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        generic_downloads = tmp_path / "Downloads"
        slskd_downloads = generic_downloads
        library = tmp_path / "Library"
        remote = "Persiana Jones/Puerto Hurraco/01 - Track.flac"
        source = slskd_downloads / remote
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"audio")
        settings = Settings(download_dir=str(generic_downloads), library_root=str(library))
        settings.soulseek = SoulseekSettings(
            enabled=True,
            managed=True,
            api_key="abc",
            account_status="ready",
            app_dir=str(tmp_path / "slskd-app"),
            downloads_dir=str(slskd_downloads),
            incomplete_dir=str(generic_downloads / ".slskd-incomplete"),
        )
        db = _DB()
        handler = _CompletionHandler(library, slskd_downloads)
        monitor = SlskdImportMonitor(settings_manager=_SM(settings), database=db, category_registry=_Registry(), completion_handler=handler)

        original = SlskdTransferReadModel.active_download_rows

        async def fake_rows(self, *, include_completed=True):
            return [{
                "id": "slskd:test",
                "source": "slskd",
                "backend": "soulseek",
                "status": "complete",
                "category_id": "music",
                "item_name": "Puerto Hurraco",
                "slskd_username": "peer",
                "files": [{
                    "file_path": remote,
                    "filename": remote,
                    "size": 5,
                    "downloaded_bytes": 5,
                    "status": "complete",
                }],
            }]

        SlskdTransferReadModel.active_download_rows = fake_rows
        try:
            counters = await monitor.run_once()
            counters2 = await monitor.run_once()
        finally:
            SlskdTransferReadModel.active_download_rows = original

        assert counters["imported"] == 1, counters
        assert counters2["imported"] == 0, counters2
        assert handler.linked
        assert (library / "Puerto Hurraco" / "01 - Track.flac").exists()
        assert not source.exists(), "Soulseek staging file should be cleaned after library import"


def main() -> None:
    test_managed_slskd_uses_ljs_download_root_by_default()
    asyncio.run(test_completed_slskd_transfer_imports_and_cleans_source_async())
    print("Round 153 Soulseek import monitor tests passed")


if __name__ == "__main__":
    main()
