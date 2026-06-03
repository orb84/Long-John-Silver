#!/usr/bin/env python3
"""Round 214 coordinator usage and architecture audit tests."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text()


def assert_contains(path: str, needle: str) -> None:
    text = read(path)
    assert needle in text, f"{path} does not contain expected text: {needle!r}"


def test_architecture_docs_document_category_item_coordinator() -> None:
    text = read("architecture.md")
    assert "## Category Item Mutation Coordinator" in text
    assert "CategoryItemCoordinator" in text
    assert "settings.tracked_items" in text
    assert "enrich_metadata=False" in text


def test_ui_and_action_paths_delegate_to_coordinator() -> None:
    handler = read("src/web/action_handlers/category_items.py")
    assert "CategoryItemCoordinator(" in handler
    assert "await self._coordinator.add_or_update_item" in handler
    assert "await self._coordinator.update_item" in handler
    assert "await self._coordinator.remove_item" in handler
    assert "settings.tracked_items.append" not in handler
    assert "settings.tracked_items.items =" not in handler

    router = read("src/web/routers/category_items.py")
    assert '"category_item_add"' in router
    assert '"category_item_update"' in router
    assert '"category_item_remove"' in router


def test_library_discovery_uses_coordinator_without_provider_storms() -> None:
    scheduler = read("src/core/scheduler.py")
    assert "def category_item_coordinator" in scheduler
    assert "coordinator.add_or_update_item" in scheduler
    assert 'source="library_scan"' in scheduler
    assert "enrich_metadata=False" in scheduler
    assert "sync_all_category_watch_policies(reason=\"library_auto_discover\")" in scheduler


def test_direct_tracked_item_mutations_are_confined_to_allowed_files() -> None:
    offenders: list[str] = []
    allowed = {
        "src/core/category_item_coordinator.py",
        "src/core/state_coordinator.py",
    }
    for path in (ROOT / "src").rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text()
        if "tracked_items.append" in text or "tracked_items.items =" in text:
            if rel not in allowed:
                offenders.append(rel)
    assert not offenders, "direct tracked_items mutations outside coordinator/repair paths: " + ", ".join(offenders)


def test_selected_candidate_preference_uses_coordinator() -> None:
    text = read("src/ai/tools/queue_download_support.py")
    assert "category_item_coordinator" in text
    assert "coordinator_factory().update_item" in text
    assert "upsert_category_item(category_id, item_id" not in text


if __name__ == "__main__":
    tests = [
        test_architecture_docs_document_category_item_coordinator,
        test_ui_and_action_paths_delegate_to_coordinator,
        test_library_discovery_uses_coordinator_without_provider_storms,
        test_direct_tracked_item_mutations_are_confined_to_allowed_files,
        test_selected_candidate_preference_uses_coordinator,
    ]
    for test in tests:
        test()
    print("round214 coordinator usage audit tests: PASS")
