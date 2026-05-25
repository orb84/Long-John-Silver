"""Tests for category-owned configuration files."""

from pathlib import Path

import yaml

from src.core.config import SettingsManager
from src.core.models import Settings


def test_settings_manager_loads_category_yaml_files(tmp_path: Path) -> None:
    """Category YAML files populate the effective runtime category settings."""
    settings_path = tmp_path / 'settings.yaml'
    categories_dir = tmp_path / 'categories'
    categories_dir.mkdir()
    settings_path.write_text('download_dir: ./downloads\n', encoding='utf-8')
    (categories_dir / 'movie.yaml').write_text(
        yaml.safe_dump({
            'category_id': 'movie',
            'enabled': True,
            'paths': {'library_path': '/media/movies'},
            'properties': {'naming_template': '{title} ({year})'},
            'metadata': {'providers': {'tmdb': {'enabled': True}}},
        }),
        encoding='utf-8',
    )

    settings = SettingsManager(yaml_path=str(settings_path), category_config_dir=str(categories_dir)).load()

    assert settings.category_settings['movie']['library_path'] == '/media/movies'
    assert settings.category_settings['movie']['naming_template'] == '{title} ({year})'
    assert settings.category_settings['movie']['metadata']['providers']['tmdb']['enabled'] is True


def test_settings_manager_saves_categories_outside_global_yaml(tmp_path: Path) -> None:
    """Saving writes category settings to config/categories instead of settings.yaml."""
    settings_path = tmp_path / 'settings.yaml'
    categories_dir = tmp_path / 'categories'
    settings = Settings(category_settings={'tv': {'library_path': '/media/tv', 'ended_update_interval_days': 90}})
    manager = SettingsManager(yaml_path=str(settings_path), category_config_dir=str(categories_dir))

    manager.save(settings)

    global_payload = yaml.safe_load(settings_path.read_text(encoding='utf-8'))
    category_payload = yaml.safe_load((categories_dir / 'tv.yaml').read_text(encoding='utf-8'))
    assert 'category_settings' not in global_payload
    assert category_payload['category_id'] == 'tv'
    assert category_payload['paths']['library_path'] == '/media/tv'
    assert category_payload['properties']['ended_update_interval_days'] == 90
