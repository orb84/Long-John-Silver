#!/usr/bin/env python3
"""Round 165 regression tests for managed slskd path launch behavior.

Round 164/165 added useful path audit logs.  Round 171 restores the actual
working topology: APP_DIR is local app state and completed Soulseek files use
the configured LJS download root directly.  Probe failures are diagnostics; they
must not invent child folders or project-local staging.
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
from src.integrations.slskd_config import build_slskd_share_plan
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
    )
    return settings


def test_exact_root_probe_failure_keeps_direct_download_root_not_project_disk() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        manager = SlskdManager()
        original = SlskdManager._write_probe_directory

        def fake_probe(cls, folder: Path) -> dict[str, object]:  # noqa: ANN001 - monkeypatch signature for classmethod
            text = str(folder).replace("\\", "/")
            if text.endswith("/Soulseek") or text.endswith("/Soulseek-Incomplete"):
                return {"ok": True, "filename": "probe.tmp", "bytes": 21, "attempts": []}
            return {"ok": False, "attempts": [{"stage": "open_write", "error": "OSError(5, 'Input/output error')"}]}

        SlskdManager._write_probe_directory = classmethod(fake_probe)
        try:
            ok = asyncio.run(manager.configure(settings))
        finally:
            SlskdManager._write_probe_directory = original

        plan = build_slskd_share_plan(settings)
        expected_downloads = Path(settings.download_dir).resolve(strict=False)
        expected_incomplete = expected_downloads / ".slskd-incomplete"
        assert ok is True, "probe failure must not move Soulseek payloads to project disk or invented children"
        assert Path(plan.downloads_dir) == expected_downloads
        assert Path(plan.incomplete_dir) == expected_incomplete
        assert Path(settings.soulseek.downloads_dir) == expected_downloads
        yaml_text = manager.config_path(settings).read_text(encoding="utf-8").replace("\\", "/")
        assert "project/data/slskd/downloads" not in yaml_text
        assert "/Mounted/Argh/downloads/Soulseek" not in yaml_text
        assert str(expected_downloads).replace("\\", "/") in yaml_text


def test_uncreatable_download_directory_is_logged_but_not_replaced_by_project_disk() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        blocker = root / "Mounted"
        blocker.write_text("not a directory", encoding="utf-8")
        settings = _settings(root)
        manager = SlskdManager()

        ok = asyncio.run(manager.configure(settings))

        assert ok is True
        assert settings.soulseek.account_status == "checking"
        assert manager.config_path(settings).exists(), "corrected config should still be visible for diagnostics"
        yaml_text = manager.config_path(settings).read_text(encoding="utf-8").replace("\\", "/")
        assert "project/data/slskd/downloads" not in yaml_text
        assert "/downloads/Soulseek" not in yaml_text


def test_managed_slskd_launch_args_force_user_download_root() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        settings.soulseek.downloads_dir = str(Path(settings.soulseek.app_dir) / "downloads")
        plan = build_slskd_share_plan(settings)
        manager = SlskdManager()
        exe = root / "slskd"
        exe.write_text("", encoding="utf-8")

        args = manager._start_args(exe, Path(settings.soulseek.app_dir), manager.config_path(settings), settings)  # noqa: SLF001

        assert "--downloads" in args
        assert args[args.index("--downloads") + 1] == plan.downloads_dir
        assert args[args.index("--downloads") + 1] == str(Path(settings.download_dir).resolve(strict=False))
        assert "--incomplete" in args
        assert args[args.index("--incomplete") + 1] == plan.incomplete_dir
        assert "project/data/slskd/downloads" not in "/".join(args).replace("\\", "/")


def test_path_diagnostics_include_mount_and_capacity_fields_when_available() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)
        diagnostics = SlskdManager._path_diagnostics(path)  # noqa: SLF001
        assert "exists=True" in diagnostics
        assert "mode=" in diagnostics
        assert "free_bytes=" in diagnostics
        assert "total_bytes=" in diagnostics


def test_download_ui_uses_stable_soulseek_expansion_keys() -> None:
    text = (ROOT / "src" / "web" / "static" / "js" / "components" / "downloadManagerUI.js").read_text(encoding="utf-8")
    assert "_downloadExpansionKeys(dl)" in text
    assert "slskd:${user}:${folder || item}" in text
    assert "slskd-item:${user}:${item}" in text
    assert "this._isFilesPanelExpanded(dl)" in text
    assert "this._setFilesPanelExpanded(dl, nextExpanded)" in text


def main() -> None:
    test_exact_root_probe_failure_keeps_direct_download_root_not_project_disk()
    test_uncreatable_download_directory_is_logged_but_not_replaced_by_project_disk()
    test_managed_slskd_launch_args_force_user_download_root()
    test_path_diagnostics_include_mount_and_capacity_fields_when_available()
    test_download_ui_uses_stable_soulseek_expansion_keys()
    print("Round 165/171 slskd preflight and CLI path tests passed")


if __name__ == "__main__":
    main()
