#!/usr/bin/env python3
"""Round 186 regression checks for queue controls, smaller replacement search, and log spam guards."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ai.tool_policy import AgentToolPolicy
from src.core.categories.registry import CategoryRegistry
from src.core.models import Intent, SearchResult
from src.integrations.slskd_import_monitor import SlskdImportMonitor
from src.search.rss_monitor import RSSMonitor


def test_tv_category_keeps_download_control_tools() -> None:
    registry = CategoryRegistry.with_defaults()
    tv = registry.get("tv")
    tools = AgentToolPolicy().allowed_tool_names(Intent.DOWNLOAD, category=tv)
    assert "manage_downloads" in tools, "TV category YAML must not hide existing-download control"
    assert "set_download_priority" in tools, "TV category YAML must not hide priority changes"
    assert "list_downloads" in tools, "agent must inspect queue state before changing priorities"
    assert "search_media_torrents" in tools, "category-aware search must remain available"


def test_tv_episode_query_ladder_does_not_downgrade_first() -> None:
    tv = CategoryRegistry.with_defaults().get("tv")
    item = tv.create_item("Star City", language="Italian")
    queries = tv.build_alternative_search_queries(item, "S01E01", "Italian")
    joined = "\n".join(queries[:6]).lower()
    assert "720p" not in joined, f"smaller replacements should not start from downgraded 720p searches: {queries[:6]}"
    assert any("1080p" in q.lower() for q in queries[:6]), queries[:6]
    assert any("x265" in q.lower() or "hevc" in q.lower() for q in queries[:8]), queries[:8]


async def test_tv_ranking_prefers_smaller_same_resolution_candidate() -> None:
    tv = CategoryRegistry.with_defaults().get("tv")
    item = tv.create_item("Star City", language="Italian")
    item.quality = SimpleNamespace(preferred_resolution="1080p", max_file_size_mb=None)
    context = SimpleNamespace(
        settings=SimpleNamespace(default_quality=SimpleNamespace(preferred_resolution="1080p")),
        search_constraints={
            "size_mode": "smaller",
            "smaller_than_current": True,
            "preserve_resolution": True,
            "current_size_mb": 4700,
            "target_size_mb": 3450,
            "preferred_resolution": "1080p",
        },
        pipeline=None,
    )
    gb = 1024 ** 3
    results = [
        SearchResult(title="Star City S01E01 Gli Occhi ITA ENG 1080p ATVP WEB-DL DD5.1 H264-MeM GP", magnet="magnet:?xt=old", size_bytes=int(4.7 * gb), seeders=90, source="Jackett"),
        SearchResult(title="Star City S01E01 Gli Occhi ITA ENG 1080p ATVP WEB-DL H265-TheSmallerKing", magnet="magnet:?xt=small1080", size_bytes=int(3.45 * gb), seeders=35, source="Jackett"),
        SearchResult(title="Star City S01E01 Gli Occhi ITA ENG 720p ATVP WEB-DL H264-Small", magnet="magnet:?xt=small720", size_bytes=int(2.1 * gb), seeders=55, source="Jackett"),
    ]
    ranked = await tv.rank_agent_search_results(results, item=item, language="Italian", season=1, episode=1, context=context)
    assert ranked, "expected ranked candidates"
    assert ranked[0].magnet == "magnet:?xt=small1080", [r.title for r in ranked]
    assert all(r.magnet != "magnet:?xt=old" for r in ranked), "same/current-size candidate should be filtered when smaller_than_current is true"


def test_spam_guards_present() -> None:
    assert hasattr(SlskdImportMonitor, "_log_not_ready_if_needed")
    assert hasattr(RSSMonitor, "_log_fetch_failure")


def main() -> None:
    test_tv_category_keeps_download_control_tools()
    test_tv_episode_query_ladder_does_not_downgrade_first()
    asyncio.run(test_tv_ranking_prefers_smaller_same_resolution_candidate())
    test_spam_guards_present()
    print("round186 log spam / priority / smaller-search tests passed")


if __name__ == "__main__":
    main()
