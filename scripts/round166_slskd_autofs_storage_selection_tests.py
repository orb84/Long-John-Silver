#!/usr/bin/env python3
"""Round 166/171 regression tests for slskd storage selection on mounted download roots.

Managed slskd must never use project-local payload folders and must never put
APP_DIR on the user download mount.  APP_DIR is local app state; completed
Soulseek payloads go directly to settings.download_dir, with partial files in
settings.download_dir/.slskd-incomplete.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import tempfile
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.models import Settings, SoulseekSettings
from src.integrations.slskd_config import (
    build_slskd_share_plan,
    render_slskd_yaml,
)
from src.integrations.slskd_import_monitor import SlskdImportMonitor
from src.integrations.slskd_manager import SlskdManager


class _SettingsManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings


class _Database:
    pass


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
    )
    return settings


def _patch_probe(callback):
    original = SlskdManager._write_probe_directory
    SlskdManager._write_probe_directory = classmethod(lambda cls, folder: callback(Path(folder)))
    return original


def test_managed_start_uses_direct_download_root() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        manager = SlskdManager()

        original = _patch_probe(lambda folder: {"ok": True, "filename": "probe.tmp", "bytes": 21, "attempts": []})
        try:
            ok = asyncio.run(manager.configure(settings))
        finally:
            SlskdManager._write_probe_directory = original

        selected_downloads = Path(settings.download_dir).resolve(strict=False)
        selected_incomplete = (selected_downloads / ".slskd-incomplete").resolve(strict=False)
        assert ok is True
        assert settings.soulseek.managed_directory_mode == "explicit"
        assert settings.soulseek.managed_runtime_app_dir == ""
        assert Path(settings.soulseek.downloads_dir) == selected_downloads
        assert Path(settings.soulseek.incomplete_dir) == selected_incomplete
        yaml_data = yaml.safe_load(manager.config_path(settings).read_text(encoding="utf-8"))
        assert yaml_data["directories"]["downloads"] == str(selected_downloads)
        assert yaml_data["directories"]["incomplete"] == str(selected_incomplete)
        args = manager._start_args(root / "slskd", manager._runtime_app_dir(settings), manager.config_path(settings), settings)  # noqa: SLF001
        assert args[args.index("--app-dir") + 1] == str(Path(settings.soulseek.app_dir).resolve(strict=False))
        assert args[args.index("--downloads") + 1] == str(selected_downloads)
        assert args[args.index("--incomplete") + 1] == str(selected_incomplete)
        env = manager._start_environment(settings, manager._runtime_app_dir(settings), manager.config_path(settings))  # noqa: SLF001
        assert env["APP_DIR"] == str(Path(settings.soulseek.app_dir).resolve(strict=False))
        assert env["SLSKD_DOWNLOADS_DIR"] == str(selected_downloads)
        assert env["SLSKD_INCOMPLETE_DIR"] == str(selected_incomplete)


def test_diagnostic_probe_failure_does_not_rewrite_storage_topology() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        manager = SlskdManager()
        original = _patch_probe(lambda folder: {"ok": False, "attempts": [{"stage": "open_write", "error": "EIO"}]})
        try:
            ok = asyncio.run(manager.configure(settings))
        finally:
            SlskdManager._write_probe_directory = original

        expected_downloads = Path(settings.download_dir).resolve(strict=False)
        expected_incomplete = expected_downloads / ".slskd-incomplete"
        assert ok is True
        assert settings.soulseek.account_status == "checking"
        assert settings.soulseek.managed_directory_mode == "explicit"
        assert settings.soulseek.managed_runtime_app_dir == ""
        yaml_text = render_slskd_yaml(settings, redact_secrets=False).replace("\\", "/")
        assert "project/data/slskd/downloads" not in yaml_text
        assert "/downloads/Soulseek" not in yaml_text
        assert str(expected_downloads).replace("\\", "/") in yaml_text
        assert str(expected_incomplete).replace("\\", "/") in yaml_text

def test_legacy_persisted_paths_are_migrated_by_plan_and_import_monitor() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        # Round 168's bad persisted mode must be ignored/migrated.
        settings.soulseek.managed_directory_mode = "slskd_default"
        settings.soulseek.managed_runtime_app_dir = settings.download_dir
        settings.soulseek.downloads_dir = str(Path(settings.download_dir) / "downloads")
        settings.soulseek.incomplete_dir = str(Path(settings.download_dir) / "incomplete")

        expected_downloads = Path(settings.download_dir).resolve(strict=False)
        expected_incomplete = (expected_downloads / ".slskd-incomplete").resolve(strict=False)
        plan = build_slskd_share_plan(settings)
        monitor = SlskdImportMonitor(
            settings_manager=_SettingsManager(settings),
            database=_Database(),
            category_registry={},
            completion_handler=None,
        )

        assert Path(plan.app_dir) == Path(settings.soulseek.app_dir).resolve(strict=False)
        assert Path(plan.downloads_dir) == expected_downloads
        assert Path(plan.incomplete_dir) == expected_incomplete
        assert monitor._download_roots(settings) == [expected_downloads]  # noqa: SLF001
        assert monitor._incomplete_roots(settings) == [expected_incomplete]  # noqa: SLF001


def main() -> None:
    test_managed_start_uses_direct_download_root()
    test_diagnostic_probe_failure_does_not_rewrite_storage_topology()
    test_legacy_persisted_paths_are_migrated_by_plan_and_import_monitor()
    print("Round 166/171 slskd managed explicit storage tests passed")


if __name__ == "__main__":
    main()
