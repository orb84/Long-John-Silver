#!/usr/bin/env python3
"""Round 130 Soulseek/slskd source-boundary regression tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ai.tools.downloads import DownloadToolProvider
from src.ai.tools.soulseek import SearchSoulseekTool
from src.core.models import Settings, SoulseekSettings, SoulseekShareMode, ToolExecutionContext
from src.integrations.slskd_client import SlskdClient
from src.integrations.slskd_config import build_slskd_share_plan, render_slskd_yaml


class DummySettingsManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings


def test_settings_defaults_and_serialization() -> None:
    settings = Settings()
    assert settings.soulseek.enabled is False
    assert settings.soulseek.share_mode == SoulseekShareMode.FULL_LIBRARY
    dumped = settings.model_dump(mode="json")
    assert dumped["soulseek"]["share_mode"] == "full_library"
    assert "music" in dumped["soulseek"]["search_enabled_categories"]


def test_share_plan_full_library_and_exclusions() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "Library"
        settings = Settings(library_root=str(root))
        settings.soulseek = SoulseekSettings(enabled=True, api_key="abc", excluded_share_directories=[str(root / "Private")])
        plan = build_slskd_share_plan(settings)
        assert plan.enabled is True
        assert plan.share_mode == "full_library"
        assert any(entry.startswith("[LJS Library]") and str(root.resolve()) in entry for entry in plan.shared_directories)
        assert str((root / "Private").resolve()) in plan.excluded_directories
        assert any(entry.startswith("!") for entry in plan.slskd_share_entries)


def test_share_plan_root_is_rejected() -> None:
    settings = Settings(library_root="/")
    settings.soulseek = SoulseekSettings(enabled=True, api_key="abc")
    plan = build_slskd_share_plan(settings)
    assert not plan.shared_directories
    assert any("filesystem root" in warning for warning in plan.warnings)


def test_custom_shares_and_redacted_yaml() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        share = Path(tmp) / "Music"
        settings = Settings(library_root=str(Path(tmp) / "Library"))
        settings.soulseek = SoulseekSettings(
            enabled=True,
            api_key="secret-api",
            soulseek_username="captain",
            soulseek_password="secret-password",
            share_mode="custom",
            share_directories=[str(share)],
        )
        plan = build_slskd_share_plan(settings)
        assert any(str(share.resolve()) in entry for entry in plan.shared_directories)
        yaml_text = render_slskd_yaml(settings, redact_secrets=True)
        assert "captain" in yaml_text
        assert "secret-password" not in yaml_text
        assert "secret-api" not in yaml_text
        assert "********" in yaml_text


def test_tool_registration_and_unconfigured_fallback() -> None:
    tool_names = {tool.name for tool in DownloadToolProvider().get_tools()}
    assert "search_soulseek" in tool_names
    assert "enqueue_soulseek_download" in tool_names
    assert "get_soulseek_share_plan" in tool_names

    settings = Settings()
    tool = SearchSoulseekTool(settings_manager=DummySettingsManager(settings))
    result = asyncio.run(tool.execute({"query": "Blur discography"}, ToolExecutionContext(session_id="test")))
    assert result["ok"] is False
    assert result["recoverable"] is True
    assert result["error_code"] == "SLSKD_NOT_CONFIGURED"
    assert any("torrent" in item.lower() for item in result["next_actions"])


def test_slskd_payload_normalization() -> None:
    payload = {
        "responses": [
            {
                "username": "user1",
                "hasFreeUploadSlot": True,
                "queueLength": 0,
                "uploadSpeed": 12345,
                "files": [
                    {"filename": "Blur/Discography/01 Song.flac", "size": 123456, "extension": "flac", "bitRate": 1000},
                    {"filename": "Blur/Discography/02 Song.mp3", "size": 456789, "extension": "mp3", "bitRate": 320},
                ],
            }
        ]
    }
    candidates = SlskdClient.normalize_search_payload(payload)
    assert len(candidates) == 2
    assert candidates[0].username == "user1"
    assert candidates[0].filename.endswith("01 Song.flac")
    assert candidates[0].has_free_upload_slot is True
    assert candidates[0].queue_length == 0
    assert candidates[0].as_public_dict(1)["source"] == "slskd"


def main() -> None:
    test_settings_defaults_and_serialization()
    test_share_plan_full_library_and_exclusions()
    test_share_plan_root_is_rejected()
    test_custom_shares_and_redacted_yaml()
    test_tool_registration_and_unconfigured_fallback()
    test_slskd_payload_normalization()
    print("Round 130 Soulseek/slskd source-boundary tests passed")


if __name__ == "__main__":
    main()
