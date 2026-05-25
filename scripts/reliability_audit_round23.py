#!/usr/bin/env python3
"""Round 23 UI and bandwidth enforcement audit.

Round 23 addresses three user-visible issues: suggestions clipping, upload caps
behaving as per-torrent rather than aggregate caps, and Compass controls being
split across unrelated settings panels.  This audit locks those invariants so
future refactors cannot silently regress them.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Round23UiBandwidthAudit:
    """Verify suggestions scrolling, aggregate bandwidth caps, and Compass layout."""

    def run(self) -> None:
        """Run all Round 23 invariants and raise AssertionError on failure."""
        self.require_suggestions_scroll_container()
        self.require_aggregate_bandwidth_enforcement()
        self.require_compass_download_controls_are_grouped()
        print("round23 UI/bandwidth audit passed")

    def require_suggestions_scroll_container(self) -> None:
        """Ensure the suggestion list itself is the scrollable element."""
        index = (ROOT / "src/web/templates/index.html").read_text(encoding="utf-8")
        css = (ROOT / "src/web/static/css/style.css").read_text(encoding="utf-8")
        if 'id="suggestion-list" class="suggestion-list"' not in index:
            raise AssertionError("suggestion-list must carry the suggestion-list class")
        for required in ("#suggestion-list", "overflow-y: auto", "overscroll-behavior: contain", "min-height: 0"):
            if required not in css:
                raise AssertionError(f"Suggestions CSS is missing {required!r}")

    def require_aggregate_bandwidth_enforcement(self) -> None:
        """Ensure upload/download limits are applied as aggregate caps."""
        engine = (ROOT / "src/core/torrent_engine.py").read_text(encoding="utf-8")
        downloader = (ROOT / "src/core/downloader.py").read_text(encoding="utf-8") + (ROOT / "src/core/downloader_sharing_mixin.py").read_text(encoding="utf-8")
        settings = (ROOT / "src/web/action_handlers/settings.py").read_text(encoding="utf-8")
        for required in (
            "self._rate_limits",
            "rebalance_rate_limits",
            "set_upload_limit",
            "set_download_limit",
            "per-handle fallback",
        ):
            if required not in engine:
                raise AssertionError(f"TorrentEngine aggregate-cap guard missing {required!r}")
        if '"upload_rate_limit": int(quality.max_upload_speed_kbps or 0) * 1024' not in downloader:
            raise AssertionError("DownloadManager must send zero upload caps so clearing the limit works")
        if '"download_rate_limit": int(quality.max_download_speed_kbps or 0) * 1024' not in downloader:
            raise AssertionError("DownloadManager must send zero download caps so clearing the limit works")
        if "refresh_bandwidth_limits" not in settings:
            raise AssertionError("Bandwidth schedule updates must apply immediately")

    def require_compass_download_controls_are_grouped(self) -> None:
        """Ensure related download controls now live in one Compass panel."""
        panel = (ROOT / "src/web/static/js/components/settingsPanel.js").read_text(encoding="utf-8")
        required = (
            "Downloads & Queue",
            "pref-download-dir",
            "pref-max-concurrent",
            "pref-max-dl-speed",
            "pref-max-ul-speed",
            "saveDownloadQueue",
            "Content Selection",
            "Library Categories",
        )
        for token in required:
            if token not in panel:
                raise AssertionError(f"Compass settings panel missing {token!r}")
        downloads_idx = panel.index("Downloads & Queue")
        content_idx = panel.index("Content Selection")
        ai_idx = panel.index("AI & LLM Gateway")
        if not (downloads_idx < content_idx < ai_idx):
            raise AssertionError("Compass order must start with Downloads, then Content, then AI later")


if __name__ == "__main__":
    Round23UiBandwidthAudit().run()
