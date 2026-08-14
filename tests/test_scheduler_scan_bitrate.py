"""Regression test for category-owned TV scan bitrate enrichment."""

from src.core.categories.tv import TvShowCategory


def test_tv_category_exposes_scan_bitrate_estimator() -> None:
    one_gib = 1024 ** 3
    estimated = TvShowCategory._estimate_episode_bitrate_kbps(one_gib, runtime_minutes=55)

    assert estimated is not None
    assert 2500 <= estimated <= 2700
    assert TvShowCategory._estimate_episode_bitrate_kbps(None) is None
    assert TvShowCategory._estimate_episode_bitrate_kbps(0) is None
