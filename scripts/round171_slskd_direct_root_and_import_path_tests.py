#!/usr/bin/env python3
"""Round 171 regression tests for Soulseek path flow.

The working storage topology is simple: slskd APP_DIR is local app state,
completed Soulseek files go directly to settings.download_dir, incomplete files
live in settings.download_dir/.slskd-incomplete, and remote Soulseek paths are
identity hints only when importing into the library.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.download_handler import DownloadCompletionHandler
from src.core.models import Settings, SoulseekSettings
from src.integrations.slskd_config import build_slskd_share_plan, render_slskd_yaml
from src.integrations.slskd_import_monitor import SlskdImportMonitor
from src.integrations.slskd_manager import SlskdManager


class _SettingsManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings


class _SystemPrefs:
    async def get_preference(self, key: str, default: str = "") -> str:
        return default

    async def set_preference(self, key: str, value: str) -> None:
        self.value = value


class _Database:
    def __init__(self) -> None:
        self.system = _SystemPrefs()


class _CompletionHandler:
    def __init__(self, target: Path) -> None:
        self.target = target
        self.calls: list[tuple[Path, str | None]] = []

    async def _link_completed_file_to_library(self, source: Path, item, category, settings, *, file_info=None, source_name=None):
        self.calls.append((source, source_name))
        return self.target

    def _safe_unlink(self, source: Path) -> bool:
        return True

    def _cleanup_empty_download_parents(self, sources: list[Path]) -> None:
        self.cleaned = sources


def _settings(root: Path) -> Settings:
    settings = Settings(
        download_dir=str(root / "Mounted" / "Argh" / "downloads"),
        library_root=str(root / "Mounted" / "Argh" / "Media"),
    )
    settings.soulseek = SoulseekSettings(
        enabled=True,
        managed=True,
        app_dir=str(root / "project" / "data" / "slskd"),
        api_key="a" * 32,
        soulseek_username="captain",
        soulseek_password="secret",
        web_username="ljs",
        web_password="web-secret",
        jwt_key="j" * 32,
        account_status="ready",
        downloads_dir=str(root / "project" / "data" / "slskd" / "downloads"),
        incomplete_dir=str(root / "Mounted" / "Argh" / "downloads" / "Soulseek-Incomplete"),
        managed_directory_mode="slskd_default",
        managed_runtime_app_dir=str(root / "Mounted" / "Argh" / "downloads"),
    )
    return settings


def test_managed_slskd_uses_direct_download_root_and_local_app_dir() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        manager = SlskdManager()
        assert asyncio.run(manager.configure(settings)) is True

        app_dir = Path(settings.soulseek.app_dir).resolve(strict=False)
        download_root = Path(settings.download_dir).resolve(strict=False)
        incomplete = download_root / ".slskd-incomplete"
        plan = build_slskd_share_plan(settings)
        args = manager._start_args(root / "slskd", manager._runtime_app_dir(settings), manager.config_path(settings), settings)  # noqa: SLF001
        env = manager._start_environment(settings, manager._runtime_app_dir(settings), manager.config_path(settings))  # noqa: SLF001
        yaml_text = render_slskd_yaml(settings, redact_secrets=False).replace("\\", "/")

        assert Path(plan.app_dir) == app_dir
        assert Path(plan.downloads_dir) == download_root
        assert Path(plan.incomplete_dir) == incomplete
        assert Path(settings.soulseek.downloads_dir) == download_root
        assert Path(settings.soulseek.incomplete_dir) == incomplete
        assert args[args.index("--app-dir") + 1] == str(app_dir)
        assert args[args.index("--downloads") + 1] == str(download_root)
        assert args[args.index("--incomplete") + 1] == str(incomplete)
        assert env["SLSKD_DOWNLOADS_DIR"] == str(download_root)
        assert env["SLSKD_INCOMPLETE_DIR"] == str(incomplete)
        assert "/project/data/slskd/downloads" not in yaml_text
        assert "/downloads/Soulseek" not in yaml_text
        assert "/downloads/downloads" not in yaml_text


def test_soulseek_remote_windows_path_resolves_to_completed_basename_in_download_root() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        settings.soulseek.downloads_dir = settings.download_dir
        settings.soulseek.incomplete_dir = str(Path(settings.download_dir) / ".slskd-incomplete")
        download_root = Path(settings.download_dir)
        download_root.mkdir(parents=True, exist_ok=True)
        completed = download_root / "03, Spacco Tutto.mp3"
        completed.write_bytes(b"audio")

        monitor = SlskdImportMonitor(
            settings_manager=_SettingsManager(settings),
            database=_Database(),
            category_registry={},
            completion_handler=_CompletionHandler(root / "library" / "03, Spacco Tutto.mp3"),
        )
        source = monitor._resolve_completed_source(  # noqa: SLF001
            {
                "file_path": r"music\Albums\P\Persiana Jones - 1999 - Puerto Hurraco\03, Spacco Tutto.mp3",
                "filename": r"music\Albums\P\Persiana Jones - 1999 - Puerto Hurraco\03, Spacco Tutto.mp3",
                "username": "baitattack",
                "size": 5,
            },
            settings,
        )
        assert source == completed.resolve(strict=False)
        assert DownloadCompletionHandler._clean_source_name(r"music\Albums\P\Persiana Jones - 1999 - Puerto Hurraco\03, Spacco Tutto.mp3") == "03, Spacco Tutto.mp3"


def main() -> None:
    test_managed_slskd_uses_direct_download_root_and_local_app_dir()
    test_soulseek_remote_windows_path_resolves_to_completed_basename_in_download_root()
    print("Round 171 slskd direct-root and import-path tests passed")


if __name__ == "__main__":
    main()
