"""Regression coverage for startup artwork repair and first-paint UI hydration."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_scheduler_exposes_scan_lifecycle_invalidation_hook() -> None:
    """The hourly scan must not crash after repository reconciliation."""
    source = _read("src/core/scheduler.py")

    assert "async def _invalidate_scanned_lifecycle_state" in source
    assert "await self._invalidate_scanned_lifecycle_state(result)" in source


def test_startup_scan_requests_metadata_repair_even_when_scan_is_recent() -> None:
    """Startup should repair missing cover art without requiring a manual refresh."""
    main_source = _read("main.py")
    scheduler_source = _read("src/core/scheduler.py")

    assert "request_library_scan(force=False, refresh_metadata=True, reason=\"startup\")" in main_source
    assert "self._start_metadata_refresh(None)" in scheduler_source
    assert "async def _metadata_refresh_targets(self, result: LibraryScanResult | None)" in scheduler_source


def test_booty_panel_rehydrates_and_resolves_local_posters() -> None:
    """The library panel should recover from first-paint races and show cached art."""
    app_source = _read("src/web/static/js/app.js")
    booty_source = _read("src/web/static/js/components/bootyPanel.js")

    assert "_hydrateInitialPanels" in app_source
    assert "window.bootyPanel.loadCatalog" in app_source
    assert "library_metadata_refresh_completed" in booty_source
    assert "_posterUrlFor" in booty_source
    assert "value.startsWith('/category-data/')" in booty_source


def test_voyage_logs_are_bounded_on_both_sides() -> None:
    """The browser log terminal should remain a tail, not an unbounded archive."""
    app_source = _read("src/web/static/js/app.js")
    system_router = _read("src/web/routers/system.py")

    assert "this._voyageLogLineLimit = 160" in app_source
    assert "container.childElementCount > this._voyageLogLineLimit" in app_source
    assert "max(1, min(requested, 500))" in system_router


def test_repetitive_lifecycle_logs_are_aggregated() -> None:
    """A large movie library should not emit one dormant/debug line per item."""
    scheduler_source = _read("src/core/scheduler.py")
    state_source = _read("src/core/state_coordinator.py")
    suggestion_source = _read("src/core/suggestion_compiler.py")

    assert "dormant_count += 1" in scheduler_source
    assert "Lifecycle ledger kept {dormant_count} category item(s) dormant" in scheduler_source
    assert "Initializing state for category item:" not in state_source
    assert "_log_missing_workflow_once" in suggestion_source
