#!/usr/bin/env python3
"""Round 260 regressions for long-running frontend performance guardrails.

The browser dashboard must not keep burning CPU/GPU in the background through
ambient animations, hidden-view polling, full download-card rebuilds, or
collapsed file-row patching.  These checks are intentionally static because the
project frontend is plain browser JS without a Node bundler/test harness.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Check:
    """Small assertion collector for script-style regression checks."""

    def __init__(self) -> None:
        self.failures: list[str] = []

    def ok(self, condition: bool, message: str) -> None:
        """Record a failure when ``condition`` is false."""
        if not condition:
            self.failures.append(message)

    def contains(self, path: str, needle: str, message: str) -> None:
        """Assert that a source file contains a required text contract."""
        text = (ROOT / path).read_text(encoding="utf-8")
        self.ok(needle in text, message)

    def finish(self) -> None:
        """Exit non-zero when any check failed."""
        if self.failures:
            print("Round 260 frontend performance guardrail failures:")
            for failure in self.failures:
                print(f" - {failure}")
            raise SystemExit(1)
        print("round260_frontend_performance_guardrails_tests: OK")


def test_shared_performance_coordinator(check: Check) -> None:
    """Verify the shared tab-visibility/performance service is present and loaded."""
    check.contains(
        "src/web/static/js/core/performanceCoordinator.js",
        "class FrontendPerformanceCoordinator",
        "frontend performance coordinator class should exist",
    )
    check.contains(
        "src/web/static/js/core/performanceCoordinator.js",
        "registerAdaptiveInterval(callback, options = {})",
        "coordinator should expose adaptive intervals for visible/background tabs",
    )
    check.contains(
        "src/web/static/js/core/performanceCoordinator.js",
        "document.addEventListener('visibilitychange'",
        "coordinator should listen for browser visibility changes",
    )
    check.contains(
        "src/web/templates/base.html",
        "/static/js/core/performanceCoordinator.js",
        "base template should load the coordinator before app composition",
    )


def test_app_ambient_and_hydration_throttles(check: Check) -> None:
    """Verify ambient animation and startup hydration respect visibility."""
    app = (ROOT / "src/web/static/js/app.js").read_text(encoding="utf-8")
    check.ok("new FrontendPerformanceCoordinator(this._eventBus)" in app, "AppDeck should instantiate the performance coordinator")
    check.ok("!this._perf.isVisible()" in app and "_deferredHydrateReason" in app, "panel hydration should defer while hidden")
    check.ok("allowAmbientAnimation()" in app and "maxBubbles" in app, "bubble animation should be capped and visibility-aware")
    check.ok("registerAdaptiveInterval(() => this._refreshVoyageLogs()" in app, "voyage logs should use adaptive visible-tab polling")


def test_download_manager_batches_and_skips_hidden_dom(check: Check) -> None:
    """Verify high-rate download telemetry no longer forces immediate full renders."""
    js = (ROOT / "src/web/static/js/components/downloadManagerUI.js").read_text(encoding="utf-8")
    check.ok("_queueStatsUpdate" in js and "_flushQueuedStats" in js, "download stats should be batched before DOM patching")
    check.ok("registerAdaptiveInterval(() => this.load({ silent: true })" in js, "download polling should use adaptive intervals")
    check.ok("_canTouchDownloadDom" in js and "isViewActive('hold')" in js, "download DOM patching should skip hidden Hold views")
    check.ok("_needsRenderOnVisible" in js, "download renders should defer while the browser tab is hidden")
    patcher = (ROOT / "src/web/static/js/components/downloadStatsPatcher.js").read_text(encoding="utf-8")
    check.ok("filesDiv.style.display === 'none'" in patcher, "collapsed file rows should not be patched on every telemetry tick")


def test_library_and_release_panels_are_visibility_aware(check: Check) -> None:
    """Verify expensive catalog/watch panels avoid unbounded repaint loops."""
    booty = (ROOT / "src/web/static/js/components/bootyPanel.js").read_text(encoding="utf-8")
    check.ok("_scheduleCatalogRender" in booty, "library catalog rendering should be frame-scheduled")
    check.ok("document.createDocumentFragment()" in booty, "library catalog should render into a document fragment")
    check.ok("debounce((value) => this._applyGridFilter(value), 120)" in booty, "library search filtering should be debounced")
    release = (ROOT / "src/web/static/js/components/releaseWatchPanel.js").read_text(encoding="utf-8")
    check.ok("_inFlightLoad" in release, "release watch panel should coalesce overlapping loads")
    check.ok("registerAdaptiveInterval(() => this.load({ quiet: true })" in release, "release watch refresh should be adaptive")
    check.ok("_needsRefreshOnVisible" in release, "release watch refresh should defer when hidden")


def test_css_low_power_guardrails(check: Check) -> None:
    """Verify CSS can actually stop invisible-view paint and GPU-heavy effects."""
    css = (ROOT / "src/web/static/css/style.css").read_text(encoding="utf-8")
    check.ok(".view:not(.active)" in css and "display: none" in css, "inactive views should be removed from layout/paint")
    check.ok(".ljs-background-tab .bubble" in css, "background tabs should hide decorative bubbles")
    check.ok(".ljs-perf-low-power .glass-panel" in css, "low-power mode should disable expensive backdrop filters")
    check.ok("@media (prefers-reduced-motion: reduce)" in css, "reduced-motion users should not run ambient animations")


if __name__ == "__main__":
    check = Check()
    test_shared_performance_coordinator(check)
    test_app_ambient_and_hydration_throttles(check)
    test_download_manager_batches_and_skips_hidden_dom(check)
    test_library_and_release_panels_are_visibility_aware(check)
    test_css_low_power_guardrails(check)
    check.finish()
