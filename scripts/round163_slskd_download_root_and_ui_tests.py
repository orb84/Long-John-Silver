#!/usr/bin/env python3
"""Round 163 regression tests for slskd download roots and UI state.

This round deliberately reverses the rejected app-local slskd staging default:
managed slskd must use the user-selected LJS download_dir so users can place
active downloads on the disk/mount with enough free space.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.models import Settings, SoulseekSettings
from src.integrations.slskd_config import build_slskd_share_plan, render_slskd_yaml
from src.integrations.slskd_manager import SlskdManager


def _settings(root: Path) -> Settings:
    settings = Settings(download_dir=str(root / "Mounted" / "Downloads"), library_root=str(root / "Mounted" / "Media"))
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


def test_managed_slskd_always_uses_user_download_root_by_default() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        plan = build_slskd_share_plan(settings)

        assert Path(plan.downloads_dir) == Path(settings.download_dir).resolve(strict=False)
        assert Path(plan.incomplete_dir) == (Path(settings.download_dir) / ".slskd-incomplete").resolve(strict=False)
        assert Path(plan.downloads_dir) != (Path(settings.soulseek.app_dir) / "downloads").resolve(strict=False)
        yaml_text = render_slskd_yaml(settings, redact_secrets=False)
        assert f"downloads: {plan.downloads_dir}" in yaml_text
        assert f"incomplete: {plan.incomplete_dir}" in yaml_text


def test_rejected_app_local_paths_are_migrated_back_to_download_root() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        settings.soulseek.downloads_dir = str(Path(settings.soulseek.app_dir) / "downloads")
        settings.soulseek.incomplete_dir = str(Path(settings.soulseek.app_dir) / "incomplete")

        plan = build_slskd_share_plan(settings)

        assert Path(plan.downloads_dir) == Path(settings.download_dir).resolve(strict=False)
        assert Path(plan.incomplete_dir) == (Path(settings.download_dir) / ".slskd-incomplete").resolve(strict=False)


def test_distinct_soulseek_download_override_is_ignored_in_managed_mode() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        settings.soulseek.downloads_dir = str(root / "wrong-disk" / "soulseek")
        settings.soulseek.incomplete_dir = str(root / "wrong-disk" / "partial")

        plan = build_slskd_share_plan(settings)

        assert Path(plan.downloads_dir) == Path(settings.download_dir).resolve(strict=False)
        assert Path(plan.incomplete_dir) == (Path(settings.download_dir) / ".slskd-incomplete").resolve(strict=False)


def test_configure_persists_download_root_not_project_local_staging() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        settings.soulseek.downloads_dir = str(Path(settings.soulseek.app_dir) / "downloads")
        settings.soulseek.incomplete_dir = str(Path(settings.soulseek.app_dir) / "incomplete")
        manager = SlskdManager()
        ok = asyncio.run(manager.configure(settings))

        assert ok is True
        assert Path(settings.soulseek.downloads_dir) == Path(settings.download_dir).resolve(strict=False)
        assert Path(settings.soulseek.incomplete_dir) == (Path(settings.download_dir) / ".slskd-incomplete").resolve(strict=False)
        assert manager.config_path(settings).exists()


def test_download_manager_preserves_expanded_file_panels_across_poll_render() -> None:
    text = (ROOT / "src" / "web" / "static" / "js" / "components" / "downloadManagerUI.js").read_text(encoding="utf-8")
    assert "this._expandedFilePanels = new Set();" in text
    assert "_downloadExpansionKeys(dl)" in text
    assert "_isFilesPanelExpanded(dl)" in text
    assert "this._toggleFilesPanel(dl, card)" in text
    assert "this._setFilesPanelExpanded(dl, nextExpanded)" in text


def test_library_list_rows_override_poster_card_aspect_ratio() -> None:
    css = (ROOT / "src" / "web" / "static" / "css" / "style.css").read_text(encoding="utf-8")
    assert re.search(r"\.media-card\.media-list-row\s*\{[^}]*aspect-ratio:\s*auto", css, re.S)
    assert re.search(r"\.media-card\.media-list-row\s*\{[^}]*height:\s*auto", css, re.S)
    assert re.search(r"\.media-grid\.category-view-list\s*\{[^}]*flex-direction:\s*column", css, re.S)


def main() -> None:
    test_managed_slskd_always_uses_user_download_root_by_default()
    test_rejected_app_local_paths_are_migrated_back_to_download_root()
    test_distinct_soulseek_download_override_is_ignored_in_managed_mode()
    test_configure_persists_download_root_not_project_local_staging()
    test_download_manager_preserves_expanded_file_panels_across_poll_render()
    test_library_list_rows_override_poster_card_aspect_ratio()
    print("Round 163 slskd download-root and UI tests passed")


if __name__ == "__main__":
    main()
