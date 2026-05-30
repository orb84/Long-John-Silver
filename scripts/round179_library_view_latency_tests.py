"""Round 179 regression tests for the library/Booty overview latency fix.

These checks are intentionally lightweight/static because the regression was a
frontend/API contract problem: the list endpoint was doing detail-grade work and
startup hydration was issuing overlapping catalog loads.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_category_items_list_endpoint_is_lightweight() -> None:
    source = read("src/web/routers/category_items.py")
    enrich_start = source.index("def _enrich_list_item")
    enrich_end = source.index("async def _add_item", enrich_start)
    enrich_body = source[enrich_start:enrich_end]

    require("CanonicalLibraryObjectBuilder" not in source, "list router must not import/build canonical detail objects")
    require("maybe_cache_detail_artwork" not in enrich_body, "list endpoint must not cache/fetch artwork during tab paint")
    require("_category_list_overview" in source, "list endpoint should use one cheap per-category overview map")
    require("list_category_unit_counts" in source, "list endpoint should use aggregate unit counts, not per-item unit scans")


def test_booty_panel_loads_progressively_and_dedupes_requests() -> None:
    source = read("src/web/static/js/components/bootyPanel.js")
    require("_catalogLoadPromise" in source, "BootyPanel should dedupe overlapping catalog loads")
    require("Promise.allSettled(jobs)" in source, "BootyPanel should load category item endpoints concurrently")
    require("Loading tracked items" in source, "BootyPanel should paint category sections while item requests are still pending")
    require("loadCatalog(options = {})" in source, "BootyPanel loadCatalog should support safe force/non-force reloads")


def test_global_hydration_no_longer_duplicates_booty_load() -> None:
    source = read("src/web/static/js/app.js")
    require("jobs.push(window.bootyPanel.loadCatalog" not in source, "global hydration must not start a second Booty catalog load")
    view_source = read("src/web/static/js/components/viewManager.js")
    require("targetId === 'booty'" in view_source and "window.bootyPanel.loadCatalog()" in view_source, "opening Booty should refresh/reuse the catalog load")


if __name__ == "__main__":
    test_category_items_list_endpoint_is_lightweight()
    test_booty_panel_loads_progressively_and_dedupes_requests()
    test_global_hydration_no_longer_duplicates_booty_load()
    print("round179 library view latency tests passed")
