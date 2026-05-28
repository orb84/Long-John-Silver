#!/usr/bin/env python3
"""Round 172 regressions: Soulseek must not hammer external download storage."""

from __future__ import annotations

import asyncio
import errno
import tempfile
from pathlib import Path
from types import SimpleNamespace
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.domain_models.settings import Settings, SoulseekSettings
from src.integrations import slskd_import_monitor as monitor_mod
from src.integrations.slskd_import_monitor import SlskdImportMonitor
from src.integrations.slskd_manager import SlskdManager


class _SystemPrefs:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get_preference(self, key: str, default: str = "") -> str:
        return self.values.get(key, default)

    async def set_preference(self, key: str, value: str) -> None:
        self.values[key] = value


class _Database:
    def __init__(self) -> None:
        self.system = _SystemPrefs()


class _CategoryRegistry:
    def get(self, category_id: str):
        return SimpleNamespace(category_id=category_id)


class _SettingsManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings


class _FailingCompletionHandler:
    def __init__(self) -> None:
        self.calls = 0
        self.download_dir = ""

    async def _link_completed_file_to_library(self, *args, **kwargs):  # noqa: ANN002, ANN003
        self.calls += 1
        raise OSError(errno.EIO, "Input/output error")

    def _safe_unlink(self, source: Path) -> bool:
        return False

    def _cleanup_empty_download_parents(self, sources):  # noqa: ANN001
        return 0


class _Rows:
    def __init__(self, settings_manager, database):  # noqa: ANN001
        pass

    async def active_download_rows(self, *, include_completed: bool = True):
        return [
            {
                "id": "slskd:test",
                "backend": "soulseek",
                "source": "slskd",
                "status": "complete",
                "category_id": "music",
                "item_name": "Example Album",
                "slskd_username": "peer",
                "slskd_folder": r"music\\Albums\\E\\Example Album",
                "files": [
                    {"filename": r"music\\Albums\\E\\Example Album\\01.mp3", "size": 4, "downloaded_bytes": 4, "status": "complete"},
                    {"filename": r"music\\Albums\\E\\Example Album\\02.mp3", "size": 4, "downloaded_bytes": 4, "status": "complete"},
                    {"filename": r"music\\Albums\\E\\Example Album\\03.mp3", "size": 4, "downloaded_bytes": 4, "status": "complete"},
                ],
            }
        ]


def _settings(tmp: Path) -> Settings:
    settings = Settings()
    settings.download_dir = str(tmp / "downloads")
    settings.library_root = str(tmp / "Media")
    settings.soulseek = SoulseekSettings(
        enabled=True,
        managed=True,
        soulseek_username="user",
        soulseek_password="pass",
        app_dir=str(tmp / "project" / "data" / "slskd"),
        downloads_dir=str(tmp / "project" / "data" / "slskd" / "downloads"),
        incomplete_dir=str(tmp / "downloads" / "Soulseek-Incomplete"),
        api_key="api",
        web_username="ljs",
        web_password="web",
        jwt_key="jwt",
    )
    return settings


def test_managed_startup_does_not_write_probe_download_mount() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        settings = _settings(tmp)
        manager = SlskdManager()
        calls: list[Path] = []
        original = manager._write_probe_directory

        def record(folder: Path):
            calls.append(folder)
            return original(folder)

        manager._write_probe_directory = record  # type: ignore[method-assign]
        ok = asyncio.run(manager.configure(settings))
        assert ok, manager.last_error
        # Only local slskd APP_DIR is probed. The external downloads root and
        # .slskd-incomplete are created/logged but not synthetic-write/fsync tested.
        assert calls == [Path(settings.soulseek.app_dir)]


def test_resolver_uses_album_folder_candidate_without_deep_rglob() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        settings = _settings(tmp)
        album = Path(settings.download_dir) / "Example Album"
        album.mkdir(parents=True)
        source = album / "01.mp3"
        source.write_bytes(b"data")
        mon = SlskdImportMonitor(
            settings_manager=_SettingsManager(settings),
            database=_Database(),
            category_registry=_CategoryRegistry(),
            completion_handler=_FailingCompletionHandler(),
        )
        found = mon._resolve_completed_source(  # noqa: SLF001
            {
                "filename": r"music\\Albums\\E\\Example Album\\01.mp3",
                "folder": r"music\\Albums\\E\\Example Album",
                "item_name": "Example Album",
                "username": "peer",
                "size": 4,
            },
            settings,
        )
        assert found == source.resolve(strict=False)


def test_import_io_failure_opens_circuit_and_limits_attempts(monkeypatch=None) -> None:  # noqa: ANN001
    async def run() -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            settings = _settings(tmp)
            Path(settings.download_dir, "Example Album").mkdir(parents=True)
            for idx in range(1, 4):
                Path(settings.download_dir, "Example Album", f"0{idx}.mp3").write_bytes(b"data")
            settings.soulseek.account_status = "ready"
            handler = _FailingCompletionHandler()
            original_rows = monitor_mod.SlskdTransferReadModel
            monitor_mod.SlskdTransferReadModel = _Rows  # type: ignore[assignment]
            try:
                mon = SlskdImportMonitor(
                    settings_manager=_SettingsManager(settings),
                    database=_Database(),
                    category_registry=_CategoryRegistry(),
                    completion_handler=handler,
                )
                counters = await mon.run_once()
                assert handler.calls == 1, f"expected circuit to stop after one EIO, got {handler.calls}"
                assert counters["skipped"] >= 1
                counters2 = await mon.run_once()
                assert handler.calls == 1, "storage circuit should prevent immediate retry storm"
                assert counters2["imported"] == 0
            finally:
                monitor_mod.SlskdTransferReadModel = original_rows  # type: ignore[assignment]

    asyncio.run(run())


if __name__ == "__main__":
    test_managed_startup_does_not_write_probe_download_mount()
    test_resolver_uses_album_folder_candidate_without_deep_rglob()
    test_import_io_failure_opens_circuit_and_limits_attempts()
    print("Round 172 Soulseek I/O guard tests passed")
