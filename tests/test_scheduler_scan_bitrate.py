"""Regression test for library scan bitrate enrichment helper."""

from src.core.scheduler import MediaScheduler


def test_scheduler_exposes_scan_bitrate_estimator() -> None:
    one_gib = 1024 ** 3
    estimated = MediaScheduler._estimate_bitrate_kbps(one_gib, runtime_minutes=55)

    assert estimated is not None
    assert 2500 <= estimated <= 2700
    assert MediaScheduler._estimate_bitrate_kbps(None) is None
    assert MediaScheduler._estimate_bitrate_kbps(0) is None
