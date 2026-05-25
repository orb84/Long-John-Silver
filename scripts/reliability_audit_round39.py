"""Static Round 39 reliability assertions.

Keeps the exact regressions from coming back without requiring runtime services.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_no_dashboard_password_inputs() -> None:
    haystack = "\n".join(
        path.read_text(errors="ignore")
        for folder in (ROOT / "src/web/templates", ROOT / "src/web/static/js")
        for path in folder.rglob("*")
        if path.suffix in {".html", ".js"}
    )
    assert 'type="password"' not in haystack
    assert "type: 'password'" not in haystack
    assert "ljs-secret-input" in haystack
    assert "data-lpignore" in haystack


def test_download_completion_reconciler_is_wired() -> None:
    downloader = (ROOT / "src/core/downloader.py").read_text()
    lifecycle = (ROOT / "src/core/downloader_lifecycle.py").read_text()
    health = (ROOT / "src/core/download_health.py").read_text()
    main = (ROOT / "main.py").read_text()
    assert "async def reconcile_completed_downloads" in downloader
    assert "_item_looks_complete" in downloader
    assert "_promote_completed_item" in downloader
    assert "_is_content_complete" in lifecycle
    assert "reconcile_completed_downloads(limit=100)" in health
    assert "await downloader.reconcile_completed_downloads()" in main


if __name__ == "__main__":
    test_no_dashboard_password_inputs()
    test_download_completion_reconciler_is_wired()
    print("Round 39 reliability audit passed.")
