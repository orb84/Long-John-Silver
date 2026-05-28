#!/usr/bin/env python3
"""Round 169/170 regression tests for managed slskd environment isolation.

Managed slskd scrubs inherited SLSKD_* variables, then explicitly sets current
APP_DIR, downloads, and incomplete paths.  APP_DIR remains local app state.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.models import Settings, SoulseekSettings
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
    )
    return settings


def test_managed_mode_scrubs_stale_environment_and_sets_explicit_current_paths() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        # Simulate legacy persisted bad mode; the helpers must ignore it.
        settings.soulseek.managed_directory_mode = "slskd_default"
        settings.soulseek.managed_runtime_app_dir = settings.download_dir
        settings.soulseek.downloads_dir = str(Path(settings.download_dir) / "downloads")
        settings.soulseek.incomplete_dir = str(Path(settings.download_dir) / "incomplete")
        manager = SlskdManager()
        expected_downloads = Path(settings.download_dir).resolve(strict=False)
        expected_incomplete = (expected_downloads / ".slskd-incomplete").resolve(strict=False)
        expected_app_dir = Path(settings.soulseek.app_dir).resolve(strict=False)

        with patch.dict(
            os.environ,
            {
                "SLSKD_DOWNLOADS_DIR": "/wrong/downloads",
                "SLSKD_INCOMPLETE_DIR": "/wrong/incomplete",
                "SLSKD_CONFIG": "/tmp/old-slskd.yml",
                "SLSKD_APP_DIR": "/tmp/old-app",
                "APP_DIR": "/tmp/old-app",
                "PATH": os.environ.get("PATH", ""),
            },
            clear=False,
        ):
            env = manager._start_environment(settings, manager._runtime_app_dir(settings), manager.config_path(settings))  # noqa: SLF001
            args = manager._start_args(root / "slskd", manager._runtime_app_dir(settings), manager.config_path(settings), settings)  # noqa: SLF001

        assert env["APP_DIR"] == str(expected_app_dir)
        assert env["SLSKD_APP_DIR"] == str(expected_app_dir)
        assert env["SLSKD_CONFIG"] == str(manager.config_path(settings))
        assert env["SLSKD_DOWNLOADS_DIR"] == str(expected_downloads)
        assert env["SLSKD_INCOMPLETE_DIR"] == str(expected_incomplete)
        assert "/wrong/downloads" not in env.values()
        assert args[args.index("--app-dir") + 1] == str(expected_app_dir)
        assert args[args.index("--downloads") + 1] == str(expected_downloads)
        assert args[args.index("--incomplete") + 1] == str(expected_incomplete)


def test_fresh_start_log_rotates_stale_output_and_records_current_paths() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        manager = SlskdManager()
        app_dir = manager._runtime_app_dir(settings)  # noqa: SLF001
        config_path = manager.config_path(settings)
        env = manager._start_environment(settings, app_dir, config_path)  # noqa: SLF001
        args = manager._start_args(root / "slskd", app_dir, config_path, settings)  # noqa: SLF001
        log_path = manager.log_path(settings)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("old stale invalid configuration downloads=/wrong\n", encoding="utf-8")

        manager._prepare_fresh_start_log(settings, app_dir=app_dir, config_path=config_path, args=args, env=env)  # noqa: SLF001

        current = log_path.read_text(encoding="utf-8")
        previous = log_path.with_name(log_path.stem + ".previous" + log_path.suffix).read_text(encoding="utf-8")
        assert "old stale invalid configuration" in previous
        assert "old stale invalid configuration" not in current
        assert "LJS managed slskd launch" in current
        assert f"app_dir={app_dir}" in current
        assert "SLSKD_DOWNLOADS_DIR" in current
        assert str(Path(settings.download_dir).resolve(strict=False)) in current
        assert ".slskd-incomplete" in current
        assert "/Soulseek" not in current.replace("\\", "/")


def test_managed_runtime_cwd_remains_stable_ljs_app_dir() -> None:
    source = Path("src/integrations/slskd_manager.py").read_text(encoding="utf-8")
    assert "cwd=str(self._resolve_path(settings.soulseek.app_dir))" in source
    assert "cwd=str(app_dir)" not in source


def main() -> None:
    test_managed_mode_scrubs_stale_environment_and_sets_explicit_current_paths()
    test_fresh_start_log_rotates_stale_output_and_records_current_paths()
    test_managed_runtime_cwd_remains_stable_ljs_app_dir()
    print("Round 169/171 slskd environment scrub tests passed")


if __name__ == "__main__":
    main()
