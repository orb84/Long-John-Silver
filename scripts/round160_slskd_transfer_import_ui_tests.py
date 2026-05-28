#!/usr/bin/env python3
"""Round 160 slskd transfer/import/UI regression tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.models import Settings, SoulseekSettings
from src.integrations.slskd_import_monitor import SlskdImportMonitor
from src.integrations.slskd_transfer_view import SlskdTransferReadModel
from src.web.action_handlers.downloads import DownloadsActionHandler


class _SM:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings


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


def _settings(root: Path) -> Settings:
    settings = Settings(download_dir=str(root / "generic-downloads"), library_root=str(root / "library"))
    settings.soulseek = SoulseekSettings(
        enabled=True,
        managed=True,
        api_key="a" * 32,
        app_dir=str(root / "data" / "slskd"),
        downloads_dir="",
        incomplete_dir="",
        account_status="ready",
    )
    return settings


def test_transfer_view_flattens_documented_slskd_directory_shape_with_speed_and_ids() -> None:
    model = SlskdTransferReadModel(settings_manager=object(), database=None)
    payload: dict[str, Any] = {
        "ok": True,
        "transfers": [
            {
                "username": "peer",
                "directories": [
                    {
                        "directory": "Music/Persiana Jones/Puerto Hurraco",
                        "files": [
                            {
                                "id": "file-1",
                                "filename": "01, Un Giorno Nuovo.mp3",
                                "size": 1000,
                                "bytesTransferred": 250,
                                "averageSpeed": 2048,
                                "state": "InProgress",
                                "percentComplete": 25,
                            }
                        ],
                    }
                ],
            }
        ],
    }
    rows = model._rows_from_transfer_payload(payload, include_completed=True)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["slskd_username"] == "peer"
    assert row["status"] == "downloading"
    assert row["download_rate"] == 2048
    assert row["files"][0]["slskd_id"] == "file-1"
    assert row["files"][0]["progress"] == 0.25
    assert row["files"][0]["file_path"].endswith("01, Un Giorno Nuovo.mp3")


def test_import_monitor_uses_direct_download_root_for_managed_slskd() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        monitor = SlskdImportMonitor(settings_manager=_SM(settings), database=None, category_registry=None, completion_handler=None)
        roots = monitor._download_roots(settings)
        assert Path(settings.download_dir).resolve(strict=False) in roots
        assert (Path(settings.download_dir) / ".slskd-incomplete").resolve(strict=False) not in roots


def test_import_monitor_skips_candidate_oserror_instead_of_crashing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        downloads = Path(settings.download_dir)
        downloads.mkdir(parents=True)
        monitor = SlskdImportMonitor(settings_manager=_SM(settings), database=None, category_registry=None, completion_handler=None)

        original_exists = Path.exists
        try:
            def flaky_exists(self: Path) -> bool:  # type: ignore[override]
                if self.name == "01, Un Giorno Nuovo.mp3":
                    raise OSError(5, "Input/output error", str(self))
                return original_exists(self)
            Path.exists = flaky_exists  # type: ignore[assignment]
            resolved = monitor._resolve_completed_source({
                "filename": "Music/Persiana Jones/Puerto Hurraco/01, Un Giorno Nuovo.mp3",
                "size": 123,
                "username": "peer",
            }, settings)
        finally:
            Path.exists = original_exists  # type: ignore[assignment]
        assert resolved is None


async def test_soulseek_cancel_action_removes_shadow_row_without_libtorrent() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        db = _DB()
        sm = _SM(settings)
        model = SlskdTransferReadModel(sm, db)
        public = await model.add_shadow_transfer(
            username="peer",
            filenames=["Music/Artist/Album/01 Track.mp3"],
            category_id="music",
            candidate={"folder": "Music/Artist/Album"},
        )
        assert public and public["id"].startswith("slskd:")
        handler = DownloadsActionHandler(downloader=object(), settings_manager=sm, database=db)  # type: ignore[arg-type]

        # No live slskd is running in this test, but cancel should still clear
        # the local shadow row instead of calling libtorrent and failing.
        result = await handler.cancel_download(public["id"])
        assert result["backend"] == "soulseek"
        assert result["download_id"] == public["id"]
        assert await model._shadow_rows(include_completed=True) == []


def test_frontend_no_longer_disables_soulseek_controls() -> None:
    text = (ROOT / "src/web/static/js/components/downloadManagerUI.js").read_text(encoding="utf-8")
    assert "use slskd/Soulseek controls for pause/cancel until LJS adds write controls" not in text
    assert "Cancel Soulseek transfer" in text


def main() -> None:
    test_transfer_view_flattens_documented_slskd_directory_shape_with_speed_and_ids()
    test_import_monitor_uses_direct_download_root_for_managed_slskd()
    test_import_monitor_skips_candidate_oserror_instead_of_crashing()
    asyncio.run(test_soulseek_cancel_action_removes_shadow_row_without_libtorrent())
    test_frontend_no_longer_disables_soulseek_controls()
    print("Round 160 slskd transfer/import/UI tests passed")


if __name__ == "__main__":
    main()
