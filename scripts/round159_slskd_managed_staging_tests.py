#!/usr/bin/env python3
"""Round 159 regression compatibility tests after Round 163 reversal.

Round 159 introduced app-local slskd staging. Real testing proved that was the
wrong default because active downloads may need the user's large configured
``download_dir``. These tests now lock the corrected migration behavior: values
from that bad build are legacy and must resolve back to ``settings.download_dir``.
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
    settings = Settings(download_dir=str(root / "downloads"), library_root=str(root / "library"))
    settings.soulseek = SoulseekSettings(
        enabled=True,
        managed=True,
        app_dir=str(root / "data" / "slskd"),
        api_key="a" * 32,
        soulseek_username="captain",
        soulseek_password="secret",
        web_username="ljs",
        web_password="web-secret",
        jwt_key="j" * 32,
    )
    return settings


def test_blank_managed_download_paths_use_ljs_download_root_not_app_local_staging() -> None:
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


def test_previous_app_local_staging_paths_migrate_to_ljs_download_root() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        settings.soulseek.downloads_dir = str((Path(settings.soulseek.app_dir) / "downloads").resolve(strict=False))
        settings.soulseek.incomplete_dir = str((Path(settings.soulseek.app_dir) / "incomplete").resolve(strict=False))

        plan = build_slskd_share_plan(settings)

        assert Path(plan.downloads_dir) == Path(settings.download_dir).resolve(strict=False)
        assert Path(plan.incomplete_dir) == (Path(settings.download_dir) / ".slskd-incomplete").resolve(strict=False)


def test_explicit_distinct_soulseek_download_root_is_ignored_for_managed_slskd() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        explicit = root / "explicit-slskd-downloads"
        settings.soulseek.downloads_dir = str(explicit)
        settings.soulseek.incomplete_dir = ""

        plan = build_slskd_share_plan(settings)

        assert Path(plan.downloads_dir) == Path(settings.download_dir).resolve(strict=False)
        assert Path(plan.incomplete_dir) == (Path(settings.download_dir) / ".slskd-incomplete").resolve(strict=False)


def test_configure_persists_effective_ljs_download_root_paths() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        settings.soulseek.downloads_dir = str((Path(settings.soulseek.app_dir) / "downloads").resolve(strict=False))
        manager = SlskdManager()
        ok = asyncio.run(manager.configure(settings))

        assert ok is True
        assert Path(settings.soulseek.downloads_dir) == Path(settings.download_dir).resolve(strict=False)
        assert Path(settings.soulseek.incomplete_dir) == (Path(settings.download_dir) / ".slskd-incomplete").resolve(strict=False)
        assert manager.config_path(settings).exists()


def main() -> None:
    test_blank_managed_download_paths_use_ljs_download_root_not_app_local_staging()
    test_previous_app_local_staging_paths_migrate_to_ljs_download_root()
    test_explicit_distinct_soulseek_download_root_is_ignored_for_managed_slskd()
    test_configure_persists_effective_ljs_download_root_paths()
    print("Round 159 slskd managed staging compatibility tests passed")


if __name__ == "__main__":
    main()
