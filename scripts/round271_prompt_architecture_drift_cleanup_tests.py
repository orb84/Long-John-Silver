#!/usr/bin/env python3
"""Round 271 regression checks for prompt drift, TV background safety, and leftover cleanup."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

class _AioSqliteStub(types.ModuleType):
    def __getattr__(self, _name: str) -> object:
        return object

sys.modules.setdefault("aiosqlite", _AioSqliteStub("aiosqlite"))

from src.ai.download_tool_recovery import DownloadToolRecovery
from src.core.categories.registry import CategoryRegistry
from src.core.categories.tv import TvShowCategory
from src.core.downloader import DownloadManager
from src.core.models import TvShowItem


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class _PipelineSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[object, str | None, object]] = []

    async def run_discovery(self, item: object, episode_label: str | None = None, force: object = None, **_: object) -> bool:
        self.calls.append((item, episode_label, force))
        return True


class _MediaStub:
    async def list_category_units(self, *_: object, **__: object) -> list[dict[str, object]]:
        return []


async def _run_tv_update(category: TvShowCategory, item: TvShowItem, pipeline: _PipelineSpy) -> None:
    now = datetime.now(timezone.utc).isoformat()
    item.last_upgrade_scan_at = now
    settings = SimpleNamespace(category_settings={"tv": {}})
    context = SimpleNamespace(settings=settings, db=SimpleNamespace(media=_MediaStub()), pipeline=pipeline, metadata_clients={})
    await category.update(item, context)


def test_tv_update_does_not_invent_next_episode_when_auto_off() -> None:
    category = TvShowCategory()
    pipeline = _PipelineSpy()
    item = TvShowItem(key="Yellowstone", auto_download=False, last_season=5, last_episode=14, last_checked_at=None)
    asyncio.run(_run_tv_update(category, item, pipeline))
    require(pipeline.calls == [], "TV background update must not search when per-show auto-download is off")


def test_tv_update_uses_watch_plan_not_progress_plus_one() -> None:
    category = TvShowCategory()
    pipeline = _PipelineSpy()
    item = TvShowItem(key="Yellowstone", auto_download=True, last_season=5, last_episode=14, last_checked_at=None)

    async def fake_watch_plan(_item: object, _context: object) -> object:
        return SimpleNamespace(release_watches=[SimpleNamespace(unit_key="S06E01")])

    category.build_watch_plan = fake_watch_plan  # type: ignore[method-assign]
    asyncio.run(_run_tv_update(category, item, pipeline))
    require([call[1] for call in pipeline.calls] == ["S06E01"], "TV update should use category watch-plan unit, not S05E15")


def test_downloader_tracked_item_iteration_handles_itemlist_and_plain_list() -> None:
    tracked = SimpleNamespace(key="Rooster", display_name="Rooster", item_type="tv", auto_download=False)
    require(DownloadManager._iter_tracked_items(SimpleNamespace(tracked_items=[tracked])) == [tracked], "plain tracked_items list should work")
    require(DownloadManager._iter_tracked_items(SimpleNamespace(tracked_items=SimpleNamespace(items=[tracked]))) == [tracked], "ItemList-style tracked_items.items should work")


def test_recovery_fallback_is_category_neutral() -> None:
    args = DownloadToolRecovery.build_search_media_torrents_args(
        user_prompt="Can you please grab me Widows Bay in italian ? Full first season",
        active_category_id="tv",
    )
    require(args is not None, "fallback should still produce a first search")
    require(args.get("category_id") == "tv", "fallback should preserve active category")
    for forbidden_key in ("language", "language_is_explicit", "season", "episode", "search_scope"):
        require(forbidden_key not in args, f"generic fallback must not synthesize {forbidden_key}")


def test_prompt_files_are_present_for_concrete_download_categories() -> None:
    registry = CategoryRegistry.with_defaults()
    expected = {
        "tv": "TV release-name skill",
        "movie": "Movie release-name skill",
        "music": "Music release-name skill",
        "ebooks": "Ebook release-name skill",
        "audiobooks": "Audiobook release-name skill",
        "general": "General Files is a narrow catch-all category",
    }
    for category_id, marker in expected.items():
        category = registry.get(category_id)
        require(category is not None, f"{category_id} should be registered")
        prompt = category.load_prompt_file()
        require(marker in prompt, f"{category_id} should load category-owned prompt skill file")


def test_noop_prompt_overrides_and_legacy_tv_progress_search_are_gone() -> None:
    tv_source = (ROOT / "src/core/categories/tv.py").read_text(encoding="utf-8")
    movie_source = (ROOT / "src/core/categories/movie.py").read_text(encoding="utf-8")
    workflow_source = (ROOT / "src/core/categories/tv_workflows.py").read_text(encoding="utf-8")
    require("def build_prompt_guidance" not in tv_source, "TV should rely on base prompt-file injection, not a no-op override")
    require("def build_prompt_guidance" not in movie_source, "Movie should rely on base prompt-file injection, not a no-op override")
    require("label = f\"S{int(s):02d}E{int(e) + 1:02d}\"" not in tv_source, "TV update must not derive unattended episode search from progress + 1")
    require('progress.get("last_episode") or 0) + 1' not in workflow_source, "TV workflow must not derive next download from progress + 1")


def main() -> None:
    test_tv_update_does_not_invent_next_episode_when_auto_off()
    test_tv_update_uses_watch_plan_not_progress_plus_one()
    test_downloader_tracked_item_iteration_handles_itemlist_and_plain_list()
    test_recovery_fallback_is_category_neutral()
    test_prompt_files_are_present_for_concrete_download_categories()
    test_noop_prompt_overrides_and_legacy_tv_progress_search_are_gone()
    print("round271_prompt_architecture_drift_cleanup_tests: OK")


if __name__ == "__main__":
    main()
