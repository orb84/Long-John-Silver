#!/usr/bin/env python3
"""Round 174 regression tests for installer extraction imports and polling noise.

Covers two failures seen in the macOS first-run log:
- Jackett/slskd installers referenced safe archive helpers without importing them.
- /api/suggestions polling generated huge uvicorn access log spam.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.search.jackett_manager import JackettManager
from src.integrations.slskd_manager import SlskdManager
from src.web.access_logs import QuietPollingAccessLogFilter, DEFAULT_QUIET_ACCESS_PATHS


def _record(message: str, args: tuple | None = None) -> logging.LogRecord:
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=args or (),
        exc_info=None,
    )


def test_installer_extract_helpers_are_imported() -> None:
    assert callable(JackettManager._extract_tarball)
    assert callable(JackettManager._extract_zip)
    assert callable(SlskdManager._extract_zip)
    assert "safe_extract_tar" in JackettManager._extract_tarball.__globals__
    assert "safe_extract_zip" in JackettManager._extract_zip.__globals__
    assert "safe_extract_zip" in SlskdManager._extract_zip.__globals__


def test_suggestions_access_polling_is_quiet_but_failures_are_visible() -> None:
    assert "/api/suggestions" in DEFAULT_QUIET_ACCESS_PATHS
    filt = QuietPollingAccessLogFilter()
    ok = _record('%s - "%s %s HTTP/%s" %d', ("127.0.0.1:1", "GET", "/api/suggestions", "1.1", 200))
    failed = _record('%s - "%s %s HTTP/%s" %d', ("127.0.0.1:1", "GET", "/api/suggestions", "1.1", 500))
    post = _record('%s - "%s %s HTTP/%s" %d', ("127.0.0.1:1", "POST", "/api/suggestions/1/approve", "1.1", 200))
    assert filt.filter(ok) is False
    assert filt.filter(failed) is True
    assert filt.filter(post) is True


def test_suggestions_frontend_has_inflight_and_visible_poll_guard() -> None:
    js = Path("src/web/static/js/components/suggestionsPanel.js").read_text()
    assert "this._inFlightLoad" in js
    assert "this._minLoadIntervalMs" in js
    assert "_isVisible()" in js
    assert "setTimeout(() => this.load({ force: true }), 7500)" in js


if __name__ == "__main__":
    test_installer_extract_helpers_are_imported()
    test_suggestions_access_polling_is_quiet_but_failures_are_visible()
    test_suggestions_frontend_has_inflight_and_visible_poll_guard()
    print("round174 install extraction and polling log tests passed")
