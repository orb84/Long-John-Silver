#!/usr/bin/env python3
"""Round 136 regression checks for Soulseek source strategy and Compass structure."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.models import Settings


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_soulseek_defaults_cover_parallel_search() -> None:
    cfg = Settings().soulseek
    cats = set(cfg.search_enabled_categories)
    require({"music", "audiobooks", "ebooks", "tv", "movie", "general"}.issubset(cats), "Soulseek should be enabled for the main downloadable categories by default")
    require(cfg.parallel_search_enabled is True, "parallel Soulseek companion search should default on")
    require(cfg.download_preference == "torrent_first", "download preference should have a safe default")


def test_tool_policy_does_not_hide_soulseek_for_music() -> None:
    policy = read("src/ai/tool_policy.py")
    require('"search_soulseek"' in policy, "tool policy must preserve search_soulseek")
    require('"enqueue_soulseek_download"' in policy, "tool policy must preserve enqueue_soulseek_download")
    require('category YAML narrowing hid search_soulseek' in policy, "regression comment should document the log-driven failure")
    audio = read("config/category-definitions/audio.yaml")
    media = read("config/category-definitions/media.yaml")
    require("search_soulseek" in audio and "enqueue_soulseek_download" in audio, "audio categories should declare Soulseek tools")
    require("search_soulseek" in media and "enqueue_soulseek_download" in media, "media categories should declare Soulseek tools")


def test_search_media_torrents_returns_companion_contract() -> None:
    src = read("src/core/scheduler_services.py")
    require('response["companion_soulseek"]' in src, "search_media_torrents should attach parallel Soulseek results")
    require('download_preference' in src and 'soulseek_first' in src, "source strategy should expose download preference")
    tool = read("src/ai/tools/scheduling.py")
    require('evaluate_soulseek_candidates' in tool, "search tool should tell the LLM how to act on companion Soulseek results")


def test_slskd_installer_prefers_runnable_binary() -> None:
    src = read("src/integrations/slskd_manager.py")
    require('_binary_is_runnable' in src, "managed slskd install should smoke-test the extracted binary")
    require('"musl" in name' in src, "Linux musl assets should be de-prioritized after ENOENT startup logs")
    require('account_status = "error"' in src and 'slskd failed to start' in src, "start failures should update visible account status")


def test_compass_uses_nested_collapsible_sections() -> None:
    js = read("src/web/static/js/components/settingsPanel.js")
    css = read("src/web/static/css/style.css")
    require('_settingsSubsection' in js, "settings panel should have reusable nested accordions")
    require('category-root-details' in js and 'nested-settings-details' in js, "category settings should be nested/collapsible")
    require('pref-soulseek-download-preference' in js, "Compass should expose first download source preference")
    require('Round 136: Compass accordions' in css, "Compass redesign CSS should be present")


def main() -> None:
    test_soulseek_defaults_cover_parallel_search()
    test_tool_policy_does_not_hide_soulseek_for_music()
    test_search_media_torrents_returns_companion_contract()
    test_slskd_installer_prefers_runnable_binary()
    test_compass_uses_nested_collapsible_sections()
    print("Round 136 Soulseek parallel/settings tests passed")


if __name__ == "__main__":
    main()
