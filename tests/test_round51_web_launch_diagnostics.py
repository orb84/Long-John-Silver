"""Round 51 regressions for hard web launch diagnostics."""

from pathlib import Path


def test_liveness_endpoint_is_dependency_free():
    source = Path("src/web/app.py").read_text(encoding="utf-8")
    live_start = source.index('@app.get("/api/live")')
    middleware_start = source.index('@app.middleware("http")')
    live_block = source[live_start:middleware_start]

    assert live_start < middleware_start
    assert '"service": "ljs-live"' in live_block
    assert "browser_runtime" not in live_block
    assert "storage_monitor" not in live_block
    assert "settings_manager" not in live_block


def test_readiness_probe_uses_complete_http_body_and_exact_build_identity():
    source = Path("src/web/readiness.py").read_text(encoding="utf-8")
    main_source = Path("main.py").read_text(encoding="utf-8")

    assert "/api/live" in source
    assert r'readuntil(b"\r\n\r\n")' in source
    assert "readexactly(content_length)" in source
    assert "expected_build_id" in source
    assert "expected_asset_version" in source
    assert "LJSWebReadinessGate" in main_source


def test_startup_logs_access_urls_and_defers_expensive_jobs():
    source = Path("main.py").read_text(encoding="utf-8")

    ready_log = source.index("LJS web UI answered /api/live")
    deferred_call = source.index('"deferred_startup_jobs"')
    helper_start = source.index("async def _run_deferred_startup_jobs")
    helper_end = source.index("async def main()", helper_start)
    helper = source[helper_start:helper_end]

    assert ready_log < deferred_call
    assert "scheduler.request_library_scan" in helper
    assert '"startup_suggestion_compilation"' not in helper
    assert "air/suggestion jobs" in helper.lower()
    assert "_format_access_urls" in source
    assert "LJS_HOST is bound to localhost only" in source


def test_liveness_endpoint_bypasses_setup_redirect():
    source = Path("src/web/app.py").read_text(encoding="utf-8")
    allowed_start = source.index("allowed = (")
    allowed_end = source.index(")", allowed_start)
    allowed = source[allowed_start:allowed_end]

    assert '"/api/live"' in allowed
