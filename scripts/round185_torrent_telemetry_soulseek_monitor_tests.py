#!/usr/bin/env python3
"""Round 185 regression checks for torrent telemetry and Soulseek monitor noise."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_soulseek_import_roots_are_not_logged_every_poll() -> None:
    monitor = read("src/integrations/slskd_import_monitor.py")
    require("_log_roots_if_needed(settings)" in monitor, "run_once must delegate root diagnostics to a throttled helper")
    require("_last_roots_signature" in monitor and 'verb = "changed"' in monitor and 'else "initialized"' in monitor,
            "monitor must remember root signatures and log only initialization/change events")
    require("now - self._last_roots_debug_at >= 1800.0" in monitor,
            "unchanged root heartbeat must be very low frequency, not every minute")
    require('"Soulseek import monitor roots: "' not in monitor,
            "old unconditional per-pass root log must be removed")


def test_download_rates_are_smoothed_from_byte_progress() -> None:
    lifecycle = read("src/core/downloader_lifecycle.py")
    require("_stabilize_transfer_stats" in lifecycle,
            "download lifecycle monitor must stabilize bursty libtorrent telemetry")
    require("delta_rate" in lifecycle and "raw_download_rate" in lifecycle,
            "rate stabilizer must compare raw libtorrent rate with byte-delta rate")
    require("self._smoothed_download_rate *= 0.50" in lifecycle,
            "smoothed rate must decay quickly so true stalls still become visible")
    require("stats = self._stabilize_transfer_stats(stats)" in lifecycle,
            "progress loop must apply stabilized stats before persisting/broadcasting")


def test_swarm_display_uses_stable_source_snapshot_when_live_count_is_zero() -> None:
    view_model = read("src/web/view_models/download_view_model.py")
    patcher = read("src/web/static/js/components/downloadStatsPatcher.js")
    ui = read("src/web/static/js/components/downloadManagerUI.js")
    require("display_seeders" in view_model and "display_seeders_basis" in view_model,
            "download API view model must expose stable display seed fields")
    require("source_int > 0" in view_model and "display_seeders_basis'] = 'source'" in view_model,
            "API display seeds must fall back to source/indexer seed snapshot")
    require("_swarmDisplay(dl)" in patcher and "source snapshot" in patcher,
            "stats patcher must avoid rendering live zero seeds as total swarm collapse")
    require("_smoothIncomingStats" in ui and "display_download_rate" in ui,
            "download UI must keep a short display-rate grace window for zero samples")


def main() -> None:
    test_soulseek_import_roots_are_not_logged_every_poll()
    test_download_rates_are_smoothed_from_byte_progress()
    test_swarm_display_uses_stable_source_snapshot_when_live_count_is_zero()
    print("round185 torrent telemetry / soulseek monitor tests passed")


if __name__ == "__main__":
    main()
