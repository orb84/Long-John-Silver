#!/usr/bin/env python3
"""Round 168 regression replacement: default APP_DIR mode is retired.

Round 168 tried to put slskd APP_DIR on settings.download_dir.  logs(63) proved
that is wrong because slskd write-tests APP_DIR itself.  These tests keep the
old script name but assert migration back to explicit managed directories.
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


def test_persisted_slskd_default_mode_is_migrated_to_direct_download_root() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        Path(settings.download_dir).mkdir(parents=True)
        manager = SlskdManager()

        ok = asyncio.run(manager.configure(settings))

        expected_downloads = Path(settings.download_dir).resolve(strict=False)
        expected_incomplete = (expected_downloads / ".slskd-incomplete").resolve(strict=False)
        assert ok is True
        assert settings.soulseek.managed_directory_mode == "explicit"
        assert settings.soulseek.managed_runtime_app_dir == ""
        assert Path(settings.soulseek.downloads_dir) == expected_downloads
        assert Path(settings.soulseek.incomplete_dir) == expected_incomplete
        assert manager._runtime_app_dir(settings) == Path(settings.soulseek.app_dir).resolve(strict=False)  # noqa: SLF001
        plan = build_slskd_share_plan(settings)
        assert Path(plan.app_dir) == Path(settings.soulseek.app_dir).resolve(strict=False)
        assert Path(plan.downloads_dir) == expected_downloads
        yaml_data = yaml.safe_load(render_slskd_yaml(settings, redact_secrets=False))
        assert yaml_data["directories"]["downloads"] == str(expected_downloads)
        args = manager._start_args(root / "slskd", manager._runtime_app_dir(settings), manager.config_path(settings), settings)  # noqa: SLF001
        assert args[args.index("--app-dir") + 1] == str(Path(settings.soulseek.app_dir).resolve(strict=False))
        assert args[args.index("--downloads") + 1] == str(expected_downloads)


def test_invalid_download_root_still_does_not_default_to_project_disk() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        bad_parent = root / "Mounted" / "Argh"
        bad_parent.parent.mkdir(parents=True)
        bad_parent.write_text("not a directory", encoding="utf-8")
        manager = SlskdManager()

        ok = asyncio.run(manager.configure(settings))

        assert ok is True
        assert settings.soulseek.account_status == "checking"
        yaml_text = render_slskd_yaml(settings, redact_secrets=False).replace("\\", "/")
        assert "project/data/slskd/downloads" not in yaml_text
        assert "/downloads/Soulseek" not in yaml_text
        assert str(Path(settings.download_dir)).replace("\\", "/") in yaml_text


def main() -> None:
    test_persisted_slskd_default_mode_is_migrated_to_direct_download_root()
    test_invalid_download_root_still_does_not_default_to_project_disk()
    print("Round 168/171 retired default-directory mode tests passed")


if __name__ == "__main__":
    main()
