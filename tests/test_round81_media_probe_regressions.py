from scripts.round81_media_probe_regression_tests import (
    test_movie_units_preserve_multi_audio_streams,
    test_tv_units_preserve_multi_audio_streams,
)


def test_round81_tv_multi_audio_survives_canonical_units() -> None:
    test_tv_units_preserve_multi_audio_streams()


def test_round81_movie_multi_audio_survives_canonical_units() -> None:
    test_movie_units_preserve_multi_audio_streams()
