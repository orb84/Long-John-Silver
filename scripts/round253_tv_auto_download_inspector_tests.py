#!/usr/bin/env python3
"""Round 253 regressions for per-show TV new-episode automation.

TV release automation is item-owned and opt-in by default: every TV inspector
exposes a simple checkbox that writes the tracked-item ``auto_download`` field
through the category item coordinator.  The scheduler then rebuilds
category-owned release watches from that item policy; generic code must not
invent TV semantics.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import types
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("aiosqlite", types.SimpleNamespace(Connection=object, Row=dict, Cursor=object))

from src.core.categories.tv import TvShowCategory
from src.core.domain_models.media import ItemList
from src.core.models import TvShowItem
from src.web.action_handlers.category_items import CategoryItemActionHandler

TV_CATEGORY_ID = "tv"


class Check:
    """Small assertion collector for script-style regression checks."""

    def __init__(self) -> None:
        self.failures: list[str] = []

    def ok(self, condition: bool, message: str) -> None:
        """Record a failure when ``condition`` is false."""
        if not condition:
            self.failures.append(message)

    def finish(self) -> None:
        """Exit non-zero when any check failed."""
        if self.failures:
            print("Round 253 TV auto-download inspector failures:")
            for failure in self.failures:
                print(f" - {failure}")
            raise SystemExit(1)
        print("round253_tv_auto_download_inspector_tests: OK")


def run(coro: Any) -> Any:
    """Run an async test scenario."""
    return asyncio.run(coro)


class FakeSettings:
    """Settings double with global automation deliberately disabled."""

    def __init__(self) -> None:
        self.language = "English"
        self.auto_download = False
        self.tracked_items = ItemList(items=[TvShowItem(key="Star City", auto_download=True)])


class FakeSettingsManager:
    """Settings manager double that records saves."""

    def __init__(self) -> None:
        self.settings = FakeSettings()
        self.saved = 0

    def save(self, _settings: Any) -> None:
        """Record a settings save call."""
        self.saved += 1


class FakeRegistry:
    """Category registry double for TV-only action handler tests."""

    def __init__(self) -> None:
        self.tv = TvShowCategory()

    def get(self, category_id: str) -> Any:
        """Return the TV category for ``tv``."""
        return self.tv if category_id == TV_CATEGORY_ID else None


class FakeScheduler:
    """Scheduler double that records watch-policy resync requests."""

    def __init__(self) -> None:
        self.synced: list[dict[str, Any]] = []
        self.invalidated: list[dict[str, Any]] = []

    async def sync_category_watch_policy(
        self,
        category_id: str,
        item_id: str,
        item: Any = None,
        reason: str = "",
    ) -> None:
        """Record a category watch-policy sync."""
        self.synced.append({"category_id": category_id, "item_id": item_id, "item": item, "reason": reason})

    async def invalidate_item_lifecycle(self, category_id: str, item_id: str, reason: str = "") -> None:
        """Record lifecycle invalidation."""
        self.invalidated.append({"category_id": category_id, "item_id": item_id, "reason": reason})


class FakeContext:
    """Minimal context used by TV release-watch requirement checks."""

    def __init__(self) -> None:
        self.settings = types.SimpleNamespace(language="English", auto_download=False)


def test_tv_auto_download_defaults(check: Check) -> None:
    """Verify TV normalizes legacy/null automation to the safe opt-in default."""
    category = TvShowCategory()
    implicit = TvShowItem(key="Default Show")
    legacy_null = TvShowItem(key="Legacy Show", auto_download=None)
    created = category.create_item("Created Show")
    explicit_on = category.create_item("On Show", auto_download=True)
    check.ok(implicit.auto_download is False, "TV items should default new-episode auto-download to False")
    check.ok(legacy_null.auto_download is False, "legacy null TV automation should normalize to False")
    check.ok(created.auto_download is False, "TV create_item should default new-episode auto-download to False")
    check.ok(explicit_on.auto_download is True, "explicitly enabled TV automation must stay True")


def test_release_watch_requirements_default_off(check: Check) -> None:
    """Verify release-watch automation stays notify-only until the item opts in."""
    category = TvShowCategory()
    context = FakeContext()
    default_item = types.SimpleNamespace(language="English", auto_download=None)
    enabled_item = types.SimpleNamespace(language="English", auto_download=True)
    requirements = category._release_watch_requirements(default_item, context)
    enabled = category._release_watch_requirements(enabled_item, context)
    check.ok(requirements.get("auto_download") is False, "TV release watches should default to notify-only")
    check.ok(enabled.get("auto_download") is True, "TV release watches must respect the per-show checkbox on state")


def test_inspector_update_resyncs_watch_policy(check: Check) -> None:
    """Verify the UI PATCH path updates the item and resynchronizes watches."""
    async def scenario() -> tuple[FakeSettingsManager, FakeScheduler, dict[str, Any]]:
        settings_manager = FakeSettingsManager()
        scheduler = FakeScheduler()
        handler = CategoryItemActionHandler(settings_manager, FakeRegistry(), scheduler=scheduler)
        result = await handler.update(TV_CATEGORY_ID, "Star City", auto_download=False)
        return settings_manager, scheduler, result

    settings_manager, scheduler, result = run(scenario())
    item = settings_manager.settings.tracked_items[0]
    check.ok(result.get("status") == "ok", "category_item_update should succeed for TV auto_download")
    check.ok(item.auto_download is False, "category_item_update should persist the per-show checkbox value")
    check.ok(settings_manager.saved == 1, "category_item_update should save settings")
    check.ok(bool(scheduler.synced), "category_item_update should resync release watches immediately")
    check.ok(scheduler.synced[-1]["reason"] == "update", "watch sync should identify the item update reason")


def test_inspector_frontend_contains_checkbox(check: Check) -> None:
    """Verify the TV inspector renders and saves the new checkbox."""
    js = (ROOT / "src/web/static/js/components/categoryItemDetailModal.js").read_text(encoding="utf-8")
    check.ok("Automatically download new episodes" in js, "inspector should label the per-show automation checkbox")
    check.ok("_automationSection(item)" in js, "inspector should render the automation section")
    check.ok("CategoryApiClient.updateItem" in js, "checkbox should persist through the category item update API")
    check.ok("auto_download: Boolean(enabled)" in js, "checkbox should send the auto_download field")
    check.ok("item.auto_download === true" in js, "inspector should display missing/null auto_download as disabled")


if __name__ == "__main__":
    check = Check()
    test_tv_auto_download_defaults(check)
    test_release_watch_requirements_default_off(check)
    test_inspector_update_resyncs_watch_policy(check)
    test_inspector_frontend_contains_checkbox(check)
    check.finish()
