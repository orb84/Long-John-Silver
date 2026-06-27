#!/usr/bin/env python3
"""Round 266 regression checks for TV consent drift and queue visibility."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.category_item_coordinator import CategoryItemCoordinator
from src.core.categories.tv import TvShowCategory
from src.core.categories.tv_workflows import TvWorkflowMixin


class _FakeSettingsManager:
    def __init__(self) -> None:
        self.settings = SimpleNamespace(tracked_items=[])
        self.saved = 0

    def save(self, settings) -> None:
        self.saved += 1


class _FakeMediaRepo:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.upserted: list[tuple[str, str, dict]] = []

    async def get_category_item(self, category_id: str, item_id: str):
        return dict(self.payload)

    async def upsert_category_item(self, category_id: str, item_id: str, payload: dict):
        self.upserted.append((category_id, item_id, dict(payload)))


class _FakeLifecycle:
    def __init__(self) -> None:
        self.invalidated: list[tuple[str, str, str]] = []

    async def invalidate_item(self, category_id: str, item_id: str, *, reason: str):
        self.invalidated.append((category_id, item_id, reason))


def test_tv_strict_automation_values() -> None:
    category = TvShowCategory()
    for value in (False, None, "false", "true", 1, 0):
        item = SimpleNamespace(key="The Wire", auto_download=value)
        assert category.release_watch_auto_download_allowed(item, {"auto_download": True}, None) is False
        assert category.release_watch_search_allowed(item, {"auto_download": True}, None) is False
        assert category.background_discovery_allowed(item, None) is False
        assert category._release_watch_requirements(item, SimpleNamespace(settings=SimpleNamespace(language="Italian")))["auto_download"] is False
    item = SimpleNamespace(key="Star City", auto_download=True)
    assert category.background_discovery_allowed(item, None) is True


def test_tv_visible_off_reconciles_settings_true() -> None:
    category = TvShowCategory()
    item = SimpleNamespace(key="Yellowstone", auto_download=True)
    changed = category.reconcile_settings_item_with_persisted_state(item, {"auto_download": False}, None)
    assert changed is True
    assert item.auto_download is False


def test_category_item_coordinator_coerces_false_string() -> None:
    coordinator = object.__new__(CategoryItemCoordinator)
    item = SimpleNamespace(key="The Wire", item_type="tv", auto_download=True)
    coordinator._apply_item_updates(item, {"auto_download": "false"})
    assert item.auto_download is False
    coordinator._apply_item_updates(item, {"auto_download": "true"})
    assert item.auto_download is True


def test_scheduled_check_no_progress_plus_one_for_background() -> None:
    source = (ROOT / "src/core/categories/tv_workflows.py").read_text()
    scheduled_block = source[source.index('if workflow_name in {"download_next_missing_episode", "download_next_missing_unit", "scheduled_check"}') : source.index('if workflow_name in {"search_download_candidates", "search_upgrade"}')]
    assert 'workflow_name == "scheduled_check"' in scheduled_block
    assert 'background_discovery_allowed' in scheduled_block
    assert 'build_watch_plan' in scheduled_block
    assert 'last_episode") or 0) + 1' not in scheduled_block, "TV workflows must not invent next episode from local progress"
    assert 'no_category_watch_unit' in scheduled_block, "manual next-missing flow should fail closed when category watch plan has no unit"


def test_scheduler_calls_reconcile_before_unattended_work() -> None:
    source = (ROOT / "src/core/scheduler.py").read_text()
    assert 'reconcile_settings_item_with_persisted_state' in source
    assert 'reason="scheduled_check"' in source
    assert 'reason="watch_policy_sync"' in source
    assert 'reason="release_watch_retry"' in source


def test_queue_endpoint_returns_active_visible_rows() -> None:
    source = (ROOT / "src/web/routers/downloads.py").read_text()
    assert 'get_active_downloads()' in source
    assert 'visible_statuses = {"queued", "downloading", "paused", "stalled"}' in source
    assert 'queued_only' in source


def test_legacy_config_route_persists_repo_and_resyncs_watch_policy() -> None:
    source = (ROOT / "src/web/action_handlers/library.py").read_text()
    assert 'upsert_category_item' in source
    assert 'sync_category_watch_policy' in source
    assert '_coerce_config_value' in source


async def main() -> None:
    test_tv_strict_automation_values()
    test_tv_visible_off_reconciles_settings_true()
    test_category_item_coordinator_coerces_false_string()
    test_scheduled_check_no_progress_plus_one_for_background()
    test_scheduler_calls_reconcile_before_unattended_work()
    test_queue_endpoint_returns_active_visible_rows()
    test_legacy_config_route_persists_repo_and_resyncs_watch_policy()
    print("ROUND266_TV_CONSENT_VISIBILITY_HARD_STOP_TESTS_PASS")


if __name__ == "__main__":
    asyncio.run(main())
