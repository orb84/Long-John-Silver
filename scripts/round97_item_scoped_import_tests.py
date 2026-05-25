#!/usr/bin/env python3
"""Round 97 regression checks for non-blocking import and item-scoped scans."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    handler = read("src/core/download_handler.py")
    scheduler = read("src/core/scheduler.py")
    scanner = read("src/utils/library_scanner.py")
    tv = read("src/core/categories/tv.py")
    main_py = read("main.py")
    agents = read("AGENTS.md")
    architecture = read("architecture.md")

    require("async def _link_completed_file_to_library" in handler, "ready import must be async")
    require("await asyncio.to_thread(\n                self._materialize_library_file_sync" in handler, "hardlink/copy must be off event loop")
    require("await asyncio.to_thread(\n                    self._move_completed_file_to_library" in handler, "completion move must be off event loop")
    require("set_library_reconciler" in handler and "reconcile_library_item_from_path" in handler, "handler must call item reconciler")
    require("begin_managed_library_mutation" in handler and "end_managed_library_mutation" in handler, "handler must mark managed mutations")
    require("Ready callback recovered from an unsafe category-template target" in handler, "recovered fallback should log as recovery")
    require("Ready callback rejected category target; retrying fallback" not in handler, "old scary warning should be removed")

    require("async def reconcile_library_item_from_path" in scheduler, "scheduler must expose item reconciliation")
    require("_scanner.item_scan" in scheduler, "item reconciliation must use item scan")
    require("_reconcile_removed_library_entries(result)" not in scheduler.split("async def reconcile_library_item_from_path", 1)[1].split("async def cleanup_category_boundary_leaks", 1)[0], "item reconciliation must not run full-scan removal cleanup")
    require("self.request_library_scan(force=True, refresh_metadata=False, reason=\"filesystem_watch\")" in scheduler, "watcher full scan must be background queued")
    require("_managed_library_mutation_count > 0" in scheduler, "watcher must defer during managed imports")

    require("async def item_scan" in scanner, "scanner must support item_scan")
    require("category.scan_item" in scanner, "scanner must delegate item scans to categories")
    require("async def scan_item" in tv and "_scan_show_dir" in tv, "TV must scan one show folder")
    require("completion_handler.set_library_reconciler(scheduler)" in main_py, "composition root must wire handler to scheduler")

    require("Item-Scoped Library Mutation Rule" in agents, "AGENTS must document the rule")
    require("Round 97 — Item-Scoped Import Reconciliation" in architecture, "architecture must document the rule")

    print("Round 97 item-scoped import reconciliation checks passed.")


if __name__ == "__main__":
    main()
