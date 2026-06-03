#!/usr/bin/env python3
"""Round 156 slskd completed-import path resolution regression tests."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.models import Settings, SoulseekSettings
from src.integrations.slskd_import_monitor import SlskdImportMonitor
from src.integrations.slskd_transfer_view import SlskdTransferReadModel


class _SettingsManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings


def _monitor(settings: Settings) -> SlskdImportMonitor:
    return SlskdImportMonitor(
        settings_manager=_SettingsManager(settings),
        database=None,
        category_registry=None,
        completion_handler=None,
    )


def test_remote_music_prefix_is_not_treated_as_required_local_path() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        downloads = base / "Downloads"
        slskd_downloads = downloads
        incomplete = downloads / ".slskd-incomplete"
        actual_folder = slskd_downloads / "slskd-created-folder" / "Persiana Jones - Puerto Hurraco"
        actual_folder.mkdir(parents=True)
        source = actual_folder / "01 - Test Track.mp3"
        payload = b"not real audio but good enough for path tests"
        source.write_bytes(payload)
        settings = Settings(download_dir=str(downloads), library_root=str(base / "Library"))
        settings.soulseek = SoulseekSettings(
            enabled=True,
            api_key="abc",
            downloads_dir=str(slskd_downloads),
            incomplete_dir=str(incomplete),
        )

        resolved = _monitor(settings)._resolve_completed_source({
            "filename": "Music/Persiana Jones/Puerto Hurraco/01 - Test Track.mp3",
            "size": len(payload),
            "username": "remote-user",
        }, settings)

        assert resolved == source.resolve(strict=False)
        assert not (slskd_downloads / "Music" / "Persiana Jones" / "Puerto Hurraco" / "01 - Test Track.mp3").exists()


def test_incomplete_folder_is_not_imported_even_when_file_exists() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        downloads = base / "Downloads"
        slskd_downloads = downloads
        incomplete = downloads / ".slskd-incomplete"
        incomplete.mkdir(parents=True)
        source = incomplete / "02 - Half Done.flac"
        source.write_bytes(b"partial")
        settings = Settings(download_dir=str(downloads), library_root=str(base / "Library"))
        settings.soulseek = SoulseekSettings(
            enabled=True,
            api_key="abc",
            downloads_dir=str(slskd_downloads),
            incomplete_dir=str(incomplete),
        )

        resolved = _monitor(settings)._resolve_completed_source({
            "filename": "Music/Persiana Jones/Puerto Hurraco/02 - Half Done.flac",
            "size": len(b"partial"),
            "username": "remote-user",
        }, settings)

        assert resolved is None


def test_transfer_view_preserves_path_as_local_hint_when_filename_exists() -> None:
    model = SlskdTransferReadModel(settings_manager=object(), database=None)
    payload = {
        "downloads": [
            {
                "username": "remote-user",
                "filename": "Music/Artist/Album/03 Song.flac",
                "path": "/tmp/Downloads/remote-user/Album/03 Song.flac",
                "state": "Completed, Succeeded",
                "size": 100,
                "bytesTransferred": 100,
            }
        ]
    }
    rows = model._rows_from_transfer_payload(payload, include_completed=True)
    assert rows and rows[0]["files"][0]["file_path"] == "Music/Artist/Album/03 Song.flac"
    assert rows[0]["files"][0]["local_path"] == "/tmp/Downloads/remote-user/Album/03 Song.flac"


def main() -> None:
    test_remote_music_prefix_is_not_treated_as_required_local_path()
    test_incomplete_folder_is_not_imported_even_when_file_exists()
    test_transfer_view_preserves_path_as_local_hint_when_filename_exists()
    print("Round 156 slskd import path resolution tests passed")


if __name__ == "__main__":
    main()
