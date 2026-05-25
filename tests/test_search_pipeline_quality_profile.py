from src.core.models import QualityProfile, Settings, TvShowItem
from src.core.search_pipeline import SearchPipeline


class DummySettingsManager:
    def __init__(self, settings):
        self.settings = settings


def test_effective_quality_profile_does_not_recurse_and_does_not_mutate_item():
    settings = Settings(default_quality=QualityProfile(preferred_resolution="1080p"))
    pipeline = SearchPipeline(
        aggregator=None,
        downloader=None,
        db=None,
        librarian=None,
        category_registry={},
        settings_manager=DummySettingsManager(settings),
    )
    item = TvShowItem(
        key="For All Mankind",
        language="Italian",
        quality=QualityProfile(preferred_resolution="720p"),
    )

    profile = pipeline._effective_quality_profile(item)

    assert profile is not None
    assert profile.preferred_resolution == "1080p"
    assert item.quality.preferred_resolution == "720p"
