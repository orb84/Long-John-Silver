#!/usr/bin/env python3
"""Round 11 static reliability checks.

Keeps the recent UI/concurrency regressions from returning without needing a
running Jackett/libtorrent/TMDB environment.
"""
from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def assert_true(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def main() -> int:
    helm = read("src/web/static/js/components/helmPanel.js")
    assert_true("window.scuttleAll" not in helm and "onclick=\"scuttleAll" not in helm and "() => scuttleAll" not in helm, "helmPanel still references global scuttleAll")
    assert_true("_scuttleAll" in helm and "ActionClient.cancelDownloads" in helm, "Helm bulk cancel path missing")

    hold = read("src/web/static/js/components/holdPanel.js")
    assert_true("bulkAction('pause')" in hold and "bulkAction('resume')" in hold and "bulkAction('cancel')" in hold, "Hold bulk action buttons missing")

    action_client = read("src/web/static/js/api/actionClient.js")
    for name in ("pauseDownloads", "resumeDownloads", "cancelDownloads"):
        assert_true(name in action_client, f"ActionClient.{name} missing")

    ui = read("src/web/static/js/components/downloadManagerUI.js")
    assert_true("hadFiles" in ui and "nowHasFiles" in ui, "Download card does not rebuild when file metadata arrives")
    assert_true("ljsConfirm" in ui, "Download UI still lacks styled confirmation")

    modal = read("src/web/static/js/components/modalManager.js")
    assert_true("window.ljsConfirm" in modal and "window.ljsAlert" in modal and "window.ljsPrompt" in modal, "Generic modal helpers missing")
    base = read("src/web/templates/base.html")
    setup = read("src/web/templates/setup.html")
    assert_true("modalManager.js" in base and "modalManager.js" in setup, "Modal manager not loaded in base/setup")

    for rel in ["src/web/static", "src/web/templates"]:
        for path in (ROOT / rel).rglob("*"):
            if path.suffix not in {".js", ".html"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if path.name == "modalManager.js":
                continue
            offenders = re.findall(r"\b(confirm|alert|prompt)\s*\(", text)
            assert_true(not offenders, f"Browser-native popup remains in {path.relative_to(ROOT)}: {offenders[:3]}")

    downloader = read("src/core/downloader.py")
    assert_true("async def set_max_concurrent" in downloader, "Downloader hot max concurrency setter missing")
    assert_true("_enforce_concurrency_limit" in downloader, "Downloader does not enforce lowered concurrency")
    assert_true("sync_active" in downloader, "Downloader does not sync active slot bookkeeping")
    assert_true("DownloadStatus.COMPLETED" not in downloader, "Invalid DownloadStatus.COMPLETED reference")
    assert_true("DownloadStatus.COMPLETE" in downloader, "Expected COMPLETE status guard missing")

    queue = read("src/core/queue_manager.py")
    assert_true("def sync_active" in queue and "set_max_concurrent" in queue, "Queue manager cannot hot-sync limits")

    settings = read("src/web/action_handlers/settings.py")
    assert_true("set_max_concurrent" in settings, "Settings save does not hot-apply max_concurrent")

    vm = read("src/web/view_models/download_view_model.py")
    assert_true("def _norm" in vm and "file_index" in vm, "File-progress merge lacks normalized path / file index fallback")

    print("round11 reliability audit passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"round11 reliability audit failed: {exc}", file=sys.stderr)
        raise
