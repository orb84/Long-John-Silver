#!/usr/bin/env python3
"""Round 158 slskd directory preflight regression tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
import stat
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.models import Settings, SoulseekSettings
import src.integrations.slskd_manager as slskd_manager_module
from src.integrations.slskd_manager import SlskdManager


def _settings(root: Path) -> Settings:
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
    return settings


def test_configure_keeps_direct_download_root_when_diagnostic_write_probe_fails() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        manager = SlskdManager()
        managed_downloads = Path(settings.download_dir).resolve(strict=False)
        incomplete = (managed_downloads / ".slskd-incomplete").resolve(strict=False)

        def fake_probe(folder: Path) -> dict[str, object]:
            if folder == managed_downloads:
                return {"ok": False, "attempts": [{"stage": "open_write", "error": "simulated EACCES"}]}
            return {"ok": True, "filename": "test", "bytes": 1}

        manager._write_probe_directory = fake_probe  # type: ignore[method-assign]
        ok = asyncio.run(manager.configure(settings))

        assert ok is True
        assert settings.soulseek.account_status == "checking"
        assert settings.soulseek.downloads_dir == str(managed_downloads)
        assert settings.soulseek.incomplete_dir == str(incomplete)
        assert manager.config_path(settings).exists(), "corrected slskd.yml should be written before launch"
        yaml_text = manager.config_path(settings).read_text(encoding="utf-8")
        assert str(managed_downloads) in yaml_text
        assert str(incomplete) in yaml_text
        assert "/downloads/Soulseek" not in yaml_text.replace("\\", "/")
        assert "slskd-app/downloads" not in yaml_text.replace("\\", "/")


def test_write_probe_detects_unwritable_directory_when_platform_enforces_modes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        folder = root / "no-write"
        folder.mkdir()
        original_mode = stat.S_IMODE(folder.stat().st_mode)
        try:
            folder.chmod(0o500)
            result = SlskdManager._write_probe_directory(folder)
            assert isinstance(result, dict)
            if not result.get("ok"):
                assert result.get("attempts"), result
        finally:
            folder.chmod(original_mode)


def test_is_installed_status_check_does_not_smoke_test_binary_on_event_loop() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        bin_dir = Path(tmp) / "bin"
        bin_dir.mkdir()
        exe = bin_dir / ("slskd.exe" if sys.platform.startswith("win") else "slskd")
        exe.write_text("not a real binary", encoding="utf-8")
        exe.chmod(0o755)

        old_dir = slskd_manager_module.SLSKD_BIN_DIR
        old_smoke = SlskdManager._binary_is_runnable
        try:
            slskd_manager_module.SLSKD_BIN_DIR = bin_dir

            def fail_if_called(path: Path) -> bool:
                raise AssertionError("is_installed must not run the blocking slskd smoke test")

            SlskdManager._binary_is_runnable = staticmethod(fail_if_called)  # type: ignore[method-assign]
            assert SlskdManager().is_installed is True
        finally:
            slskd_manager_module.SLSKD_BIN_DIR = old_dir
            SlskdManager._binary_is_runnable = old_smoke  # type: ignore[method-assign]


def main() -> None:
    test_configure_keeps_direct_download_root_when_diagnostic_write_probe_fails()
    test_write_probe_detects_unwritable_directory_when_platform_enforces_modes()
    test_is_installed_status_check_does_not_smoke_test_binary_on_event_loop()
    print("Round 158 slskd directory preflight tests passed")


if __name__ == "__main__":
    main()
