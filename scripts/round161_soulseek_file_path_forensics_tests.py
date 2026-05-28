#!/usr/bin/env python3
"""Round 161 Soulseek path normalization, forensic logging, and UI refresh tests."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.download_handler import DownloadCompletionHandler


def test_remote_windows_path_is_reduced_to_filename_before_library_planning() -> None:
    raw = r"music\Albums\P\Persiana Jones - 1999 - Puerto Hurraco\03, Spacco Tutto.mp3"
    assert DownloadCompletionHandler._clean_source_name(raw) == "03, Spacco Tutto.mp3"


def test_remote_posix_path_is_reduced_to_filename_before_library_planning() -> None:
    raw = "Music/Albums/P/Persiana Jones - 1999 - Puerto Hurraco/04, 15.mp3"
    assert DownloadCompletionHandler._clean_source_name(raw) == "04, 15.mp3"


def test_completion_handler_has_forensic_materialize_logging() -> None:
    text = (ROOT / "src/core/download_handler.py").read_text(encoding="utf-8")
    assert "Library target planned:" in text
    assert "Library materialize start:" in text
    assert "source_probe" in text
    assert "target_probe" in text


def test_slskd_import_monitor_logs_resolution_and_cleanup_probes() -> None:
    text = (ROOT / "src/integrations/slskd_import_monitor.py").read_text(encoding="utf-8")
    assert "Soulseek import resolved completed file:" in text
    assert "Soulseek import materialized library target:" in text
    assert "Soulseek import cleanup removing staging source" in text
    assert "roots=" in text


def test_download_manager_polls_for_soulseek_progress_without_reload() -> None:
    text = (ROOT / "src/web/static/js/components/downloadManagerUI.js").read_text(encoding="utf-8")
    assert "setInterval" in text
    assert "this.load({ silent: true })" in text
    assert "slskd/Soulseek transfers do not currently emit torrent telemetry events" in text


def main() -> None:
    test_remote_windows_path_is_reduced_to_filename_before_library_planning()
    test_remote_posix_path_is_reduced_to_filename_before_library_planning()
    test_completion_handler_has_forensic_materialize_logging()
    test_slskd_import_monitor_logs_resolution_and_cleanup_probes()
    test_download_manager_polls_for_soulseek_progress_without_reload()
    print("Round 161 Soulseek file/path forensics tests passed")


if __name__ == "__main__":
    main()
