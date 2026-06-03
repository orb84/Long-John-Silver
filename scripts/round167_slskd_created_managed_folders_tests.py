#!/usr/bin/env python3
"""Round 167/171 regression tests for managed slskd folder handling.

Managed slskd uses the configured LJS download root directly for completed
payloads and creates only the incomplete child folder under that same root.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import yaml
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.models import Settings, SoulseekSettings
from src.integrations.slskd_config import build_slskd_share_plan
from src.integrations.slskd_manager import SlskdManager


def _settings(root: Path) -> Settings:
    settings = Settings(download_dir=str(root / "download-volume" / "downloads"), library_root=str(root / "download-volume" / "Media"))
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
    )
    return settings


def test_managed_config_uses_download_root_and_incomplete_child_before_writing_yaml() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        downloads = Path(settings.download_dir).resolve(strict=False)
        incomplete = downloads / ".slskd-incomplete"
        assert not downloads.exists()
        assert not incomplete.exists()

        manager = SlskdManager()
        ok = asyncio.run(manager.configure(settings))

        assert ok is True
        assert downloads.is_dir()
        assert incomplete.is_dir()
        assert Path(settings.soulseek.downloads_dir) == downloads
        assert Path(settings.soulseek.incomplete_dir) == incomplete
        yaml_text = manager.config_path(settings).read_text(encoding="utf-8")
        yaml_data = yaml.safe_load(yaml_text)
        assert Path(yaml_data["directories"]["downloads"]) == downloads
        assert Path(yaml_data["directories"]["incomplete"]) == incomplete


def test_stale_child_or_default_values_are_migrated_to_direct_root() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        settings.soulseek.downloads_dir = str(Path(settings.download_dir) / "Soulseek")
        settings.soulseek.incomplete_dir = str(Path(settings.download_dir) / "Soulseek-Incomplete")

        plan = build_slskd_share_plan(settings)
        manager = SlskdManager()
        ok = asyncio.run(manager.configure(settings))

        expected_downloads = Path(settings.download_dir).resolve(strict=False)
        expected_incomplete = expected_downloads / ".slskd-incomplete"
        assert Path(plan.downloads_dir) == expected_downloads
        assert Path(plan.incomplete_dir) == expected_incomplete
        assert ok is True
        assert Path(settings.soulseek.downloads_dir) == expected_downloads
        assert Path(settings.soulseek.incomplete_dir) == expected_incomplete


def test_start_args_and_environment_point_at_direct_root() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        manager = SlskdManager()
        assert asyncio.run(manager.configure(settings)) is True
        exe = root / "slskd"
        exe.write_text("", encoding="utf-8")

        args = manager._start_args(exe, Path(settings.soulseek.app_dir), manager.config_path(settings), settings)  # noqa: SLF001
        env = manager._start_environment(settings, Path(settings.soulseek.app_dir), manager.config_path(settings))  # noqa: SLF001

        assert args[args.index("--downloads") + 1] == settings.soulseek.downloads_dir
        assert args[args.index("--incomplete") + 1] == settings.soulseek.incomplete_dir
        assert env["SLSKD_DOWNLOADS_DIR"] == settings.soulseek.downloads_dir
        assert env["SLSKD_INCOMPLETE_DIR"] == settings.soulseek.incomplete_dir
        joined = "/".join(args + [env["SLSKD_DOWNLOADS_DIR"], env["SLSKD_INCOMPLETE_DIR"]]).replace("\\", "/")
        assert "/project/data/slskd/downloads" not in joined
        assert "/download-volume/downloads/Soulseek" not in joined
        assert "/download-volume/downloads" in joined


def main() -> None:
    test_managed_config_uses_download_root_and_incomplete_child_before_writing_yaml()
    test_stale_child_or_default_values_are_migrated_to_direct_root()
    test_start_args_and_environment_point_at_direct_root()
    print("Round 167/171 managed slskd folder tests passed")


if __name__ == "__main__":
    main()
