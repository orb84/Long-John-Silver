#!/usr/bin/env python3
"""Round 217 architecture/UI/mobile regression checks."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def assert_contains(rel: str, needle: str) -> None:
    data = read(rel)
    assert needle in data, f"{rel} is missing {needle!r}"


def test_release_watch_ui_is_generic_and_wired() -> None:
    assert_contains("src/web/templates/index.html", "id=\"release-watch-panel\"")
    assert_contains("src/web/templates/base.html", "/static/js/components/releaseWatchPanel.js")
    assert_contains("src/web/static/js/app.js", "new ReleaseWatchPanel('release-watch-panel'")
    js = read("src/web/static/js/components/releaseWatchPanel.js")
    assert "/api/release-watches" in js
    assert "category-neutral" in js or "category-provided" in js
    # UI diagnostics must not hardcode TV-specific interpretation.
    forbidden = ["S01", "episode", "season", "Tv", "TV "]
    lowered = js.lower()
    assert "sxxeyy" not in lowered, "release-watch UI must not interpret TV unit keys"
    for token in forbidden:
        assert token.lower() not in lowered, f"release-watch UI leaked category-specific token {token!r}"


def test_mobile_nav_labels_are_wrapped_and_css_has_aspect_breakpoints() -> None:
    base = read("src/web/templates/base.html")
    assert base.count("class=\"nav-label\"") >= 6
    css = read("src/web/static/css/style.css")
    assert "max-aspect-ratio" in css, "mobile shell must handle tall/narrow frames, not only width"
    assert ".nav-label" in css and "display: none" in css
    assert ".helm-grid" in css and "grid-template-columns: minmax(0, 1fr) !important" in css
    assert ".widgets-container" in css and "display: none !important" in css
    assert ".chat-input-area textarea" in css and "font-size: 16px" in css


def test_architecture_docs_cover_ui_boundary() -> None:
    arch = read("architecture.md")
    assert "Round 217 coordinator/watch-policy review" in arch
    assert "ReleaseWatchPanel" in arch
    assert "must not interpret category semantics" in arch
    assert "CategoryItemCoordinator" in arch


def test_local_agent_instructions_exist() -> None:
    text = read("docs/testing/ROUND217_LOCAL_AGENT_TEST_INSTRUCTIONS.md")
    assert "Final report requirements" in text
    assert "paths to all generated test artifacts" in text


def main() -> None:
    test_release_watch_ui_is_generic_and_wired()
    test_mobile_nav_labels_are_wrapped_and_css_has_aspect_breakpoints()
    test_architecture_docs_cover_ui_boundary()
    test_local_agent_instructions_exist()
    print("round217 architecture/ui/mobile tests: PASS")


if __name__ == "__main__":
    main()
