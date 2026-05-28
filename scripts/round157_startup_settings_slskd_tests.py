#!/usr/bin/env python3
"""Round 157 startup/settings/slskd regression tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.config import SettingsManager
from src.core.models import Settings, SoulseekSettings
from src.integrations.slskd_config import build_slskd_share_plan
from src.integrations.slskd_manager import SlskdManager


def test_settings_save_uses_unique_tmp_and_survives_concurrent_writers() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings_path = root / "config" / "settings.local.yaml"
        template_path = root / "config" / "settings.template.yaml"
        template_path.parent.mkdir(parents=True, exist_ok=True)
        template_path.write_text("language: English\n", encoding="utf-8")
        manager = SettingsManager(
            yaml_path=str(settings_path),
            template_path=str(template_path),
            category_config_dir=str(root / "config" / "categories"),
            category_template_dir=str(root / "config" / "category-config-templates"),
            category_definition_dir=str(ROOT / "config" / "category-definitions"),
        )
        base = manager.settings

        def save_one(idx: int) -> None:
            payload = base.model_copy(deep=True)
            payload.web_port = 8100 + idx
            manager.save(payload)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(save_one, range(24)))

        assert settings_path.exists(), "settings file should survive concurrent saves"
        tmp_leftovers = list(settings_path.parent.glob("*.tmp"))
        assert not tmp_leftovers, f"settings temp files leaked: {tmp_leftovers}"
        reloaded = manager.reload()
        assert 8100 <= reloaded.web_port <= 8123


def test_managed_slskd_start_uses_documented_app_dir_and_download_env() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = Settings(download_dir=str(root / "downloads"), library_root=str(root / "library"))
        settings.soulseek = SoulseekSettings(
            enabled=True,
            managed=True,
            app_dir=str(root / "slskd-app"),
            api_key="a" * 32,
            soulseek_username="captain",
            soulseek_password="secret",
            web_username="ljs",
            web_password="web-secret",
            jwt_key="j" * 32,
        )
        manager = SlskdManager()
        app_dir = Path(settings.soulseek.app_dir)
        config_path = manager.config_path(settings)
        args = manager._start_args(Path("/tmp/slskd"), app_dir, config_path, settings)
        assert "--app-dir" in args, args
        assert str(app_dir) in args, args
        assert "--config" in args and str(config_path) in args, args
        assert "--downloads" in args and build_slskd_share_plan(settings).downloads_dir in args, args
        assert "--incomplete" in args and build_slskd_share_plan(settings).incomplete_dir in args, args

        env = manager._start_environment(settings, app_dir, config_path)
        plan = build_slskd_share_plan(settings)
        assert env["APP_DIR"] == str(app_dir)
        assert env["SLSKD_CONFIG"] == str(config_path)
        assert env["SLSKD_DOWNLOADS_DIR"] == plan.downloads_dir
        assert env["SLSKD_INCOMPLETE_DIR"] == plan.incomplete_dir
        assert env["SLSKD_SLSK_USERNAME"] == "captain"
        assert env["SLSKD_SLSK_PASSWORD"] == "secret"


def test_slskd_startup_exit_message_includes_log_tail() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = Settings()
        settings.soulseek.app_dir = str(root / "slskd")
        manager = SlskdManager()
        log_path = manager.log_path(settings)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("line one\nconfiguration error example\n", encoding="utf-8")
        message = manager._format_startup_exit_message(settings, 0)
        assert "rc=0" in message
        assert "configuration error example" in message


def main() -> None:
    test_settings_save_uses_unique_tmp_and_survives_concurrent_writers()
    test_managed_slskd_start_uses_documented_app_dir_and_download_env()
    test_slskd_startup_exit_message_includes_log_tail()
    print("Round 157 startup/settings/slskd tests passed")


if __name__ == "__main__":
    main()
