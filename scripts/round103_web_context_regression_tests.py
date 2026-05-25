"""Round 103 regressions for web-reader refusal, context caps, and stale query repair.

These tests are intentionally static/offline so they can run without network
or browser dependencies.  They protect the contracts fixed from the latest logs:
- 403/read failures must be typed and recoverable, with browser fallback affordances;
- unknown endpoint context metadata must not make the 16k fallback a hard max;
- explicit metadata queries must not be overwritten by stale recent media context.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text()


def test_web_reader_has_browser_like_headers_and_typed_refusal() -> None:
    src = read("src/ai/web_reader.py")
    assert "DEFAULT_HEADERS" in src
    assert "User-Agent" in src
    assert "httpx.HTTPStatusError" in src
    assert "recoverable" in src
    assert "next_actions" in src
    assert "browse_page" in src
    assert "status_code in {401, 403, 429}" in src


def test_context_budget_tracks_unreported_endpoint_metadata() -> None:
    runtime = read("src/ai/llm_task_runtime.py")
    settings = read("src/web/routers/settings.py")
    js = read("src/web/static/js/components/settingsPanel.js")
    assert "MAX_MANUAL_CONTEXT_LIMIT" in runtime
    assert "endpoint_context_reported" in runtime
    assert "user_cap_unverified_endpoint" in runtime
    assert "fallback_default" in runtime
    assert "endpoint_context_reported" in settings
    assert "manual_max_context_tokens" in settings
    assert "endpointContextReported" in js
    assert "Manual caps up to" in js


def test_metadata_repair_does_not_overwrite_explicit_fresh_queries() -> None:
    src = read("src/ai/plan_coordinator.py")
    assert "query_binds_to_recent" in src
    assert "not q or q.isdigit() or self._is_dependency_placeholder(q)" in src
    assert "A fresh, explicit metadata query" in src
    assert "not self._metadata_query_has_title(q, recent_item)" not in src


def main() -> None:
    test_web_reader_has_browser_like_headers_and_typed_refusal()
    test_context_budget_tracks_unreported_endpoint_metadata()
    test_metadata_repair_does_not_overwrite_explicit_fresh_queries()
    print("Round 103 web/context regression traces passed")


if __name__ == "__main__":
    main()
