"""Round 134 managed slskd runtime tests."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.models import Settings, SoulseekSettings
from src.integrations.slskd_config import build_slskd_config_dict, render_slskd_yaml
from src.integrations.slskd_manager import SlskdManager


def test_slskd_asset_selection_prefers_platform_zip() -> None:
    assets = [
        {"name": "slskd-0.25.1-linux-x64.zip", "browser_download_url": "https://example/linux-x64.zip"},
        {"name": "slskd-0.25.1-linux-arm64.zip", "browser_download_url": "https://example/linux-arm64.zip"},
        {"name": "slskd-0.25.1-linux-x64.sha256", "browser_download_url": "https://example/checksum"},
        {"name": "source.zip", "browser_download_url": "https://example/source.zip"},
    ]
    original = SlskdManager._platform_asset_terms
    SlskdManager._platform_asset_terms = staticmethod(lambda: (["linux"], ["x64", "amd64"]))
    try:
        urls = SlskdManager._select_asset_urls(assets)
    finally:
        SlskdManager._platform_asset_terms = original
    assert urls == ["https://example/linux-x64.zip"], urls


async def test_configure_generates_local_secrets_and_yaml_async() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = Settings(library_root=str(root / "Library"))
        settings.soulseek = SoulseekSettings(
            enabled=True,
            soulseek_username="captain",
            soulseek_password="secret",
            app_dir=str(root / "slskd"),
            downloads_dir=str(root / "slskd-downloads"),
            incomplete_dir=str(root / "slskd-incomplete"),
        )
        manager = SlskdManager()
        ok = await manager.configure(settings)
        assert ok is True
        assert settings.soulseek.api_key and len(settings.soulseek.api_key) >= 32
        assert settings.soulseek.host == "http://127.0.0.1:5030"
        assert (root / "slskd" / "slskd.yml").exists()
        cfg = build_slskd_config_dict(settings, redact_secrets=False)
        assert cfg["web"]["ip_address"] == "127.0.0.1"
        assert cfg["web"]["https"]["disabled"] is True
        assert cfg["web"]["authentication"]["api_keys"]["ljs"]["cidr"] == "127.0.0.1/32,::1/128"
        redacted = render_slskd_yaml(settings, redact_secrets=True)
        assert "secret" not in redacted
        assert settings.soulseek.api_key not in redacted
        assert "********" in redacted


def test_settings_handler_has_managed_start_stop_hooks() -> None:
    source = (ROOT / "src/web/action_handlers/settings.py").read_text(encoding="utf-8")
    assert "self._slskd.start(settings" in source
    assert "await self._slskd.stop()" in source
    assert "needs_credentials" in source


def test_main_wires_slskd_lifecycle() -> None:
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "SlskdManager" in source
    assert "slskd_manager.start(settings" in source
    assert "slskd_manager.stop()" in source
    assert "slskd_manager=slskd_manager" in source

def main() -> None:
    test_slskd_asset_selection_prefers_platform_zip()
    asyncio.run(test_configure_generates_local_secrets_and_yaml_async())
    test_settings_handler_has_managed_start_stop_hooks()
    test_main_wires_slskd_lifecycle()
    print("Round 134 managed slskd runtime tests passed")


if __name__ == "__main__":
    main()
