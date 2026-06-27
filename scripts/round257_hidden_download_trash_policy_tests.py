#!/usr/bin/env python3
"""Round 257 regression tests for hidden download trash cleanup policy."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.models import SecurityConfig
from src.core.security.path_policy import SafePathResolver


class Round257HiddenTrashPolicyTests:
    """Validate that cleanup/delete paths no longer hide media in ``.ljs-trash``."""

    def run(self) -> None:
        """Run all Round 257 checks."""
        self._default_safe_unlink_is_permanent()
        self._download_cleanup_callers_do_not_request_trash()
        self._category_delete_copy_says_deleted_not_quarantined()
        self._purge_script_dry_run_and_execute()
        print("ROUND257_HIDDEN_DOWNLOAD_TRASH_POLICY_PASS")

    def _default_safe_unlink_is_permanent(self) -> None:
        """The model default must not create invisible trash folders."""
        assert SecurityConfig().use_trash_for_deletes is False
        sandbox = PROJECT_ROOT / "data" / "round257-trash-test"
        if sandbox.exists():
            subprocess.run(["rm", "-rf", str(sandbox)], check=True)
        target = sandbox / "downloads" / "Release" / "episode.mkv"
        target.parent.mkdir(parents=True)
        target.write_text("payload", encoding="utf-8")
        resolver = SafePathResolver([sandbox / "downloads"], config=SecurityConfig())
        operation = resolver.safe_unlink(target, purpose="round257.cleanup")
        assert operation.operation == "unlink"
        assert not target.exists()
        assert not (sandbox / "downloads" / ".ljs-trash").exists()
        subprocess.run(["rm", "-rf", str(sandbox)], check=True)

    def _download_cleanup_callers_do_not_request_trash(self) -> None:
        """Routine download lifecycle cleanup must permanently delete staged payloads."""
        files = [
            PROJECT_ROOT / "src" / "core" / "downloader.py",
            PROJECT_ROOT / "src" / "core" / "download_handler.py",
            PROJECT_ROOT / "src" / "core" / "torrent_racer.py",
        ]
        for path in files:
            text = path.read_text(encoding="utf-8")
            assert "move_to_trash=True" not in text, path
            assert "quarantined partial file" not in text, path

    def _category_delete_copy_says_deleted_not_quarantined(self) -> None:
        """User-facing delete workflows must not promise hidden quarantine anymore."""
        files = [
            PROJECT_ROOT / "src" / "core" / "categories" / "movie.py",
            PROJECT_ROOT / "src" / "core" / "categories" / "tv_workflows.py",
        ]
        for path in files:
            text = path.read_text(encoding="utf-8")
            assert "Files will be quarantined" not in text, path
            assert "files_quarantined" not in text, path
            assert "files_deleted" in text, path

    def _purge_script_dry_run_and_execute(self) -> None:
        """Legacy purge helper should report first and delete only with --execute."""
        sandbox = PROJECT_ROOT / "data" / "round257-purge-test"
        if sandbox.exists():
            subprocess.run(["rm", "-rf", str(sandbox)], check=True)
        legacy = sandbox / "downloads" / ".ljs-trash" / "app" / "old.mkv.deadbeef"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("old", encoding="utf-8")
        dry = subprocess.check_output(
            [sys.executable, "scripts/round257_purge_ljs_trash.py", str(sandbox / "downloads")],
            cwd=PROJECT_ROOT,
            text=True,
        )
        dry_report = json.loads(dry)
        assert dry_report["mode"] == "dry_run"
        assert dry_report["folders_found"] == 1
        assert legacy.exists()
        executed = subprocess.check_output(
            [sys.executable, "scripts/round257_purge_ljs_trash.py", str(sandbox / "downloads"), "--execute"],
            cwd=PROJECT_ROOT,
            text=True,
        )
        executed_report = json.loads(executed)
        assert executed_report["mode"] == "execute"
        assert executed_report["folders_deleted"] == 1
        assert not (sandbox / "downloads" / ".ljs-trash").exists()
        subprocess.run(["rm", "-rf", str(sandbox)], check=True)


if __name__ == "__main__":
    Round257HiddenTrashPolicyTests().run()
