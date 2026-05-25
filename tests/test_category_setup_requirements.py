"""Tests for manifest-driven category setup requirements."""

from src.core.categories.registry import CategoryRegistry
from src.core.models import Settings, WebSearchConfig


def test_builtin_categories_expose_educational_setup_requirements() -> None:
    """TV and movie manifests advertise setup requirements without router hardcoding."""
    settings = Settings(
        jackett_url="http://localhost:9117",
        jackett_api_key="secret",
        tmdb_api_key="tmdb",
        web_search=WebSearchConfig(provider="brave", api_key="brave-key"),
        category_settings={
            "tv": {"library_path": "/media/tv"},
            "movie": {"library_path": "/media/movies"},
        },
    )
    manifests = {manifest.category_id: manifest for manifest in CategoryRegistry.with_defaults().manifests(settings)}

    assert "tv" in manifests
    assert "movie" in manifests
    tv_requirements = {req.id: req for req in manifests["tv"].setup_requirements}
    movie_requirements = {req.id: req for req in manifests["movie"].setup_requirements}

    assert tv_requirements["library_path"].configured is True
    assert tv_requirements["library_path"].setting_key == "category_config.tv.paths.library_path"
    assert tv_requirements["jackett"].configured is True
    assert tv_requirements["tmdb_api_key"].configured is True
    assert tv_requirements["tvmaze_metadata"].configured is True
    assert movie_requirements["web_search"].configured is True


def test_missing_required_category_setup_is_visible() -> None:
    """Unconfigured required category requirements are marked for the setup wizard."""
    movie = CategoryRegistry.with_defaults().get("movie")
    assert movie is not None

    requirements = {req.id: req for req in movie.setup_requirements(Settings())}

    assert requirements["library_path"].required is True
    assert requirements["library_path"].configured is False
    assert requirements["jackett"].required is True
    assert requirements["jackett"].configured is False
