#!/usr/bin/env python3
"""Round 187 checks for global control tools, bitrate-aware TV selection, and Soulseek backoff."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ai.tool_policy import AgentToolPolicy
from src.ai.tools.scheduling import _quality_choice_policy
from src.core.categories.registry import CategoryRegistry
from src.core.models import Intent, QualityProfile, SearchResult
from src.integrations.slskd_import_monitor import SlskdImportMonitor


GLOBAL_TOOLS = {"list_downloads", "manage_downloads", "set_download_priority", "get_storage_status", "inspect_torrent_candidate"}


def test_global_download_control_tools_are_always_visible() -> None:
    policy = AgentToolPolicy()
    for intent in (Intent.CHAT, Intent.SEARCH, Intent.DOWNLOAD, Intent.CONFIG):
        tools = policy.allowed_tool_names(intent)
        missing = GLOBAL_TOOLS - tools
        assert not missing, f"global queue/storage tools hidden for {intent}: {missing}"

    # Category YAML may narrow the domain/search surface, but it must not hide
    # app-level queue/storage/inspection controls.
    tv = CategoryRegistry.with_defaults().get("tv")
    for intent in (Intent.SEARCH, Intent.DOWNLOAD):
        tools = policy.allowed_tool_names(intent, category=tv)
        missing = GLOBAL_TOOLS - tools
        assert not missing, f"category narrowing hid global tools for {intent}: {missing}"


async def test_tv_ranking_uses_saved_bitrate_without_resolution_downgrade() -> None:
    tv = CategoryRegistry.with_defaults().get("tv")
    item = tv.create_item("Star City", language="Italian")
    item.quality = QualityProfile(preferred_resolution="1080p", preferred_bitrate_kbps=9000, max_bitrate_kbps=12000)
    context = SimpleNamespace(
        settings=SimpleNamespace(default_quality=QualityProfile(preferred_resolution="1080p")),
        search_constraints={
            "smaller_than_current": True,
            "preserve_resolution": True,
            "current_size_mb": 4700,
            "preferred_resolution": "1080p",
        },
        pipeline=None,
    )
    gb = 1024 ** 3
    results = [
        SearchResult(title="Star City S01E01 ITA ENG 1080p WEB-DL H264-TooLarge", magnet="magnet:?xt=large1080", size_bytes=int(4.7 * gb), seeders=80, source="Jackett"),
        SearchResult(title="Star City S01E01 ITA ENG 1080p WEB-DL H265-GoodBitrate", magnet="magnet:?xt=good1080", size_bytes=int(3.45 * gb), seeders=35, source="Jackett"),
        SearchResult(title="Star City S01E01 ITA ENG 720p WEB-DL H264-LowerResolution", magnet="magnet:?xt=small720", size_bytes=int(1.6 * gb), seeders=120, source="Jackett"),
    ]
    ranked = await tv.rank_agent_search_results(results, item=item, language="Italian", season=1, episode=1, context=context)
    assert ranked, "expected ranked candidates"
    assert ranked[0].magnet == "magnet:?xt=good1080", [r.title for r in ranked]
    assert ranked[0].magnet != "magnet:?xt=small720", "smaller must not silently mean 720p downgrade"


def test_new_show_quality_choice_blocks_silent_autopick() -> None:
    candidates = [
        {"candidate_id": "a", "title": "Show S01E01 ITA 1080p H264", "resolution": "1080p", "estimated_bitrate_kbps": 12200, "size": "4.7 GB", "seeders": 80, "auto_queue_allowed": True},
        {"candidate_id": "b", "title": "Show S01E01 ITA 1080p H265", "resolution": "1080p", "estimated_bitrate_kbps": 8950, "size": "3.45 GB", "seeders": 40, "auto_queue_allowed": True},
    ]
    policy = _quality_choice_policy(candidates, constraints={})
    assert policy.get("requires_user_choice") is True, policy
    assert policy.get("candidate_ids") == ["b", "a"] or set(policy.get("candidate_ids") or []) == {"a", "b"}
    supplied = _quality_choice_policy(candidates, constraints={"preferred_bitrate_kbps": 9000})
    assert supplied.get("requires_user_choice") is False, supplied


def test_soulseek_import_monitor_backs_off_when_not_ready() -> None:
    monitor = SlskdImportMonitor(
        settings_manager=SimpleNamespace(settings=SimpleNamespace(soulseek=SimpleNamespace(enabled=True, api_configured=True, managed=True, account_status="error"))),
        database=None,
        category_registry=None,
        completion_handler=None,
        interval_seconds=60,
    )
    # The async loop consumes this return marker to sleep longer instead of
    # repeating the same impossible managed-slskd probe every minute.
    counters = asyncio.run(monitor.run_once())
    assert counters.get("not_ready") == 1, counters


def main() -> None:
    test_global_download_control_tools_are_always_visible()
    asyncio.run(test_tv_ranking_uses_saved_bitrate_without_resolution_downgrade())
    test_new_show_quality_choice_blocks_silent_autopick()
    test_soulseek_import_monitor_backs_off_when_not_ready()
    print("round187 global tools / bitrate / soulseek diagnostics tests passed")


if __name__ == "__main__":
    main()
