#!/usr/bin/env python3
"""Round 164 regression tests for managed slskd path ownership and audit logging.

The failure this round was not only a bad path string.  A stale already-running
slskd process could keep using the rejected project-local downloads directory,
and the import monitor could still scan raw stale soulseek.downloads_dir values.
Managed mode must use settings.download_dir or fail loudly.
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


def test_managed_import_roots_ignore_stale_project_local_soulseek_dirs() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        settings.soulseek.downloads_dir = str(Path(settings.soulseek.app_dir) / "downloads")
        settings.soulseek.incomplete_dir = str(Path(settings.soulseek.app_dir) / "incomplete")
        monitor = SlskdImportMonitor(
            settings_manager=_SettingsManager(settings),
            database=_Database(),
            category_registry={},
            completion_handler=None,
        )

        completed = monitor._download_roots(settings)  # noqa: SLF001 - regression coverage for root planning.
        incomplete = monitor._incomplete_roots(settings)  # noqa: SLF001

        assert completed == [Path(settings.download_dir).resolve(strict=False)]
        assert incomplete == [(Path(settings.download_dir) / ".slskd-incomplete").resolve(strict=False)]
        assert all("project/data/slskd" not in str(path).replace("\\", "/") for path in completed + incomplete)


def test_managed_import_monitor_skips_when_slskd_is_not_ready() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        settings = _settings(Path(tmp))
        settings.soulseek.account_status = "error"
        settings.soulseek.account_status_message = "download mount failed write probe"
        monitor = SlskdImportMonitor(
            settings_manager=_SettingsManager(settings),
            database=_Database(),
            category_registry={},
            completion_handler=None,
        )

        counters = asyncio.run(monitor.run_once())

        assert counters == {"seen": 0, "complete": 0, "imported": 0, "missing": 0, "skipped": 0}


def test_configure_writes_corrected_config_before_download_root_probe_failure() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        blocker = root / "not-a-directory"
        blocker.write_text("blocks mkdir", encoding="utf-8")
        settings = _settings(root)
        settings.download_dir = str(blocker / "downloads")
        settings.soulseek.downloads_dir = str(Path(settings.soulseek.app_dir) / "downloads")
        settings.soulseek.incomplete_dir = str(Path(settings.soulseek.app_dir) / "incomplete")
        manager = SlskdManager()

        ok = asyncio.run(manager.configure(settings))

        assert ok is True
        config_path = manager.config_path(settings)
        assert config_path.exists(), "slskd.yml should be written with the configured download root even when LJS probes fail"
        yaml_text = config_path.read_text(encoding="utf-8")
        normalized = yaml_text.replace("\\", "/")
        assert str(blocker / "downloads") in yaml_text
        assert str(blocker / "downloads" / ".slskd-incomplete") in yaml_text
        assert "project/data/slskd/downloads" not in normalized
        assert "/downloads/Soulseek" not in normalized
        assert settings.soulseek.account_status == "checking"


def test_manager_refuses_blind_adoption_of_existing_slskd_processes() -> None:
    text = (ROOT / "src" / "integrations" / "slskd_manager.py").read_text(encoding="utf-8")
    assert "adopted already-running authenticated runtime" not in text
    assert "refusing blind adoption" in text
    assert "_stop_reachable_stale_instance" in text
    assert "LJS will not adopt it because it may still be using stale download paths" in text


def test_slskd_client_has_application_stop_for_stale_runtime_cleanup() -> None:
    text = (ROOT / "src" / "integrations" / "slskd_client.py").read_text(encoding="utf-8")
    assert "async def stop_application" in text
    assert "/api/v0/application/stop" in text
    assert "async def login_session" in text
    assert "_request_no_auth" in text
    assert "Authorization" in text


def main() -> None:
    test_managed_import_roots_ignore_stale_project_local_soulseek_dirs()
    test_managed_import_monitor_skips_when_slskd_is_not_ready()
    test_configure_writes_corrected_config_before_download_root_probe_failure()
    test_manager_refuses_blind_adoption_of_existing_slskd_processes()
    test_slskd_client_has_application_stop_for_stale_runtime_cleanup()
    print("Round 164 slskd path guard and audit tests passed")


if __name__ == "__main__":
    main()
