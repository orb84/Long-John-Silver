#!/usr/bin/env python3
"""Round 170/171 regression tests for the slskd APP_DIR/download boundary.

APP_DIR is local app state; completed Soulseek payloads go to the user-selected
LJS download root.  Earlier child-folder/default-directory experiments are
legacy and must migrate back to the direct download root topology.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.models import Settings, SoulseekSettings
from src.integrations.slskd_config import build_slskd_share_plan, render_slskd_yaml
from src.integrations.slskd_manager import SlskdManager


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
        managed_directory_mode="slskd_default",
        managed_runtime_app_dir=str(root / "Mounted" / "Argh" / "downloads"),
        downloads_dir=str(root / "Mounted" / "Argh" / "downloads" / "downloads"),
        incomplete_dir=str(root / "Mounted" / "Argh" / "downloads" / "incomplete"),
    )
    return settings


def test_configure_migrates_bad_modes_to_direct_download_root_and_local_app_dir() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        manager = SlskdManager()

        ok = asyncio.run(manager.configure(settings))

        app_dir = Path(settings.soulseek.app_dir).resolve(strict=False)
        downloads = Path(settings.download_dir).resolve(strict=False)
        incomplete = (downloads / ".slskd-incomplete").resolve(strict=False)
        assert ok is True
        assert settings.soulseek.managed_directory_mode == "explicit"
        assert settings.soulseek.managed_runtime_app_dir == ""
        assert manager._runtime_app_dir(settings) == app_dir  # noqa: SLF001
        assert Path(settings.soulseek.downloads_dir) == downloads
        assert Path(settings.soulseek.incomplete_dir) == incomplete
        plan = build_slskd_share_plan(settings)
        assert Path(plan.app_dir) == app_dir
        assert Path(plan.downloads_dir) == downloads
        assert Path(plan.incomplete_dir) == incomplete
        args = manager._start_args(root / "slskd", manager._runtime_app_dir(settings), manager.config_path(settings), settings)  # noqa: SLF001
        assert args[args.index("--app-dir") + 1] == str(app_dir)
        assert args[args.index("--downloads") + 1] == str(downloads)
        assert args[args.index("--incomplete") + 1] == str(incomplete)
        yaml_text = render_slskd_yaml(settings, redact_secrets=False).replace("\\", "/")
        assert "/project/data/slskd/downloads" not in yaml_text
        assert "/downloads/downloads" not in yaml_text
        assert "/downloads/Soulseek" not in yaml_text
        assert str(downloads).replace("\\", "/") in yaml_text
        assert str(incomplete).replace("\\", "/") in yaml_text


def test_write_probe_failure_does_not_block_startup_configuration() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        manager = SlskdManager()
        original = SlskdManager._write_probe_directory
        SlskdManager._write_probe_directory = classmethod(lambda cls, folder: {"ok": False, "attempts": [{"stage": "open_write", "error": "EIO"}]})
        try:
            ok = asyncio.run(manager.configure(settings))
        finally:
            SlskdManager._write_probe_directory = original

        assert ok is True
        assert settings.soulseek.account_status == "checking"
        assert Path(settings.soulseek.downloads_dir) == Path(settings.download_dir).resolve(strict=False)
        assert Path(settings.soulseek.incomplete_dir) == (Path(settings.download_dir).resolve(strict=False) / ".slskd-incomplete")


def main() -> None:
    test_configure_migrates_bad_modes_to_direct_download_root_and_local_app_dir()
    test_write_probe_failure_does_not_block_startup_configuration()
    print("Round 170/171 slskd APP_DIR boundary tests passed")


if __name__ == "__main__":
    main()
