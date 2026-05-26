"""
Configuration module for LJS.

Loads live global settings from the untracked ``config/settings.local.yaml``
file and bootstraps that file from ``config/settings.template.yaml`` on first
launch. Category-owned live settings are loaded from ignored
``config/categories/<category_id>.yaml`` files. Category definitions live in
``config/category-definitions``; blank local config templates live in
``config/category-config-templates``.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional

import yaml
from loguru import logger

from src.core.category_config import CategoryConfigStore
from src.core.models import (
    CategoryItem,
    ItemList,
    LLMConfig,
    QualityProfile,
    SecurityConfig,
    Settings,
    SharingSettings,
    StorageConfig,
    WebSearchConfig,
    EmbeddingSettings,
    BandwidthSchedule,
    _deserialize_item,
)
from src.core.security.path_policy import SafePathResolver


class SettingsManager:
    """Manages loading, saving, bootstrap, and hot-reloading of settings.

    The public repository tracks only templates. Runtime settings live in
    ignored local files so API keys, bridge tokens, private paths, and password
    hashes are not staged accidentally.
    """

    DEFAULT_LIVE_SETTINGS_PATH = Path('config/settings.local.yaml')
    DEFAULT_TEMPLATE_SETTINGS_PATH = Path('config/settings.template.yaml')

    def __init__(
        self,
        yaml_path: str | None = None,
        env_file: str = '.env',
        category_config_dir: str | None = None,
        template_path: str | None = None,
        category_template_dir: str | None = None,
        category_definition_dir: str | None = None,
    ) -> None:
        """Create a settings manager.

        Args:
            yaml_path: Path to the live local YAML file. Defaults to
                ``config/settings.local.yaml`` and can be overridden with
                ``LJS_SETTINGS_PATH``.
            env_file: Environment file path reserved for future loading.
            category_config_dir: Optional live category config directory.
                Defaults to ``config/categories`` next to the settings file.
            template_path: Public template copied to ``yaml_path`` on first
                launch. Defaults to ``config/settings.template.yaml``.
            category_template_dir: Public blank config templates copied into the
                live category config directory when missing.
            category_definition_dir: Public shareable category definition directory.
        """
        env_yaml = os.getenv('LJS_SETTINGS_PATH')
        self._yaml_path = Path(yaml_path or env_yaml or self.DEFAULT_LIVE_SETTINGS_PATH)
        self._template_path = Path(
            template_path
            or os.getenv('LJS_SETTINGS_TEMPLATE')
            or self._default_template_path(self._yaml_path)
        )
        self._env_file = env_file
        category_dir = Path(category_config_dir) if category_config_dir else self._yaml_path.parent / 'categories'
        template_dir = Path(category_template_dir) if category_template_dir else self._yaml_path.parent / 'category-config-templates'
        definition_dir = Path(category_definition_dir) if category_definition_dir else self._yaml_path.parent / 'category-definitions'
        self._category_store = CategoryConfigStore(category_dir, template_directory=template_dir, definition_directory=definition_dir)
        self._settings: Optional[Settings] = None

    @property
    def settings(self) -> Settings:
        """Get current settings, loading if necessary."""
        if self._settings is None:
            self._settings = self.load()
        return self._settings

    @property
    def settings_path(self) -> Path:
        """Return the live local settings YAML path."""
        return self._yaml_path

    @property
    def settings_template_path(self) -> Path:
        """Return the tracked public settings template path."""
        return self._template_path

    @property
    def category_config_dir(self) -> Path:
        """Return the live directory used for per-category YAML files."""
        return self._category_store.directory

    @property
    def category_template_dir(self) -> Path:
        """Return the tracked directory used for blank category config templates."""
        return self._category_store.template_directory

    @property
    def category_definition_dir(self) -> Path:
        """Return the tracked directory used for shareable category definitions."""
        return self._category_store.definition_directory

    def load(self) -> Settings:
        """Load local YAML settings and merge category YAML settings.

        Missing local files are created from public templates. Fresh installs use
        ignored local settings only; the removed tracked settings path is not a
        runtime source.
        """
        self._ensure_live_settings_file()
        settings = Settings()
        if self._yaml_path.exists():
            try:
                with self._yaml_path.open('r', encoding='utf-8') as handle:
                    data = yaml.safe_load(handle)
                if isinstance(data, dict):
                    settings = self._apply_yaml(settings, data)
                logger.info('Settings loaded successfully from {}.', self._yaml_path)
            except Exception as exc:
                logger.error(
                    f'Error loading settings from {self._yaml_path}: {exc}. '
                    'Falling back to in-memory cached settings.',
                )
                if self._settings is not None:
                    return self._settings

        category_file_settings = self._category_store.load_all()
        if category_file_settings:
            settings.category_settings = category_file_settings
        return settings

    def save(self, settings: Settings) -> None:
        """Save global settings and split category settings into local files."""
        tmp_path = self._yaml_path.with_suffix('.yaml.tmp')
        self._yaml_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            global_payload = settings.model_dump(mode='json')
            category_settings = global_payload.pop('category_settings', {}) or {}
            with tmp_path.open('w', encoding='utf-8') as handle:
                yaml.safe_dump(global_payload, handle, default_flow_style=False, sort_keys=False)
            tmp_path.replace(self._yaml_path)
            self._category_store.save_all(category_settings)
            self._settings = settings
            logger.info('Settings saved to {}.', self._yaml_path)
        except Exception as exc:
            logger.error(f'Failed to write settings atomically: {exc}')
            if tmp_path.exists():
                SafePathResolver.for_application(extra_roots=[tmp_path.parent]).safe_unlink(
                    tmp_path,
                    purpose='settings.cleanup_tmp',
                    move_to_trash=False,
                )
            raise

    def reload(self) -> Settings:
        """Force reload settings from disk."""
        self._settings = self.load()
        return self._settings

    def _ensure_live_settings_file(self) -> None:
        """Create the ignored live settings file before reading it."""
        if self._yaml_path.exists():
            return
        self._yaml_path.parent.mkdir(parents=True, exist_ok=True)
        if self._template_path.exists():
            shutil.copyfile(self._template_path, self._yaml_path)
            logger.info('Created local settings file {} from template {}.', self._yaml_path, self._template_path)
            return
        logger.info('No settings template found at {}; using in-memory defaults until first save.', self._template_path)

    @classmethod
    def _default_template_path(cls, live_path: Path) -> Path:
        """Infer the public template path for a live settings path."""
        if live_path == cls.DEFAULT_LIVE_SETTINGS_PATH or live_path.name == 'settings.local.yaml':
            return live_path.with_name('settings.template.yaml')
        return live_path.with_suffix('.template.yaml')

    def _apply_yaml(self, settings: Settings, data: dict) -> Settings:
        """Apply global YAML data to a Settings instance, preserving types."""
        if 'llm' in data and isinstance(data['llm'], dict):
            settings.llm = LLMConfig(**{**settings.llm.model_dump(), **data['llm']})
        if 'web_search' in data and isinstance(data['web_search'], dict):
            settings.web_search = WebSearchConfig(**{**settings.web_search.model_dump(), **data['web_search']})
        if 'storage' in data and isinstance(data['storage'], dict):
            settings.storage = StorageConfig(**{**settings.storage.model_dump(), **data['storage']})
        if 'security' in data and isinstance(data['security'], dict):
            settings.security = SecurityConfig(**{**settings.security.model_dump(), **data['security']})
        if 'sharing' in data and isinstance(data['sharing'], dict):
            settings.sharing = SharingSettings(**{**settings.sharing.model_dump(), **data['sharing']})
        if 'embeddings' in data and isinstance(data['embeddings'], dict):
            settings.embeddings = EmbeddingSettings(**{**settings.embeddings.model_dump(), **data['embeddings']})
        if 'tracked_items' in data:
            settings.tracked_items = self._deserialize_tracked_items(data['tracked_items'])
        if 'bandwidth_schedules' in data and isinstance(data['bandwidth_schedules'], list):
            settings.bandwidth_schedules = [
                item if isinstance(item, BandwidthSchedule) else BandwidthSchedule(**item)
                for item in data['bandwidth_schedules']
                if isinstance(item, (dict, BandwidthSchedule))
            ]
        for key, value in data.items():
            if key in {'llm', 'web_search', 'storage', 'security', 'sharing', 'embeddings', 'tracked_items', 'bandwidth_schedules', 'category_settings', 'library_paths'}:
                continue
            if hasattr(settings, key) and value is not None:
                if key == 'default_quality' and isinstance(value, dict):
                    settings.default_quality = QualityProfile(**value)
                else:
                    setattr(settings, key, value)
        return settings

    def _deserialize_tracked_items(self, items_payload: object) -> ItemList:
        """Deserialize persisted tracked items into an ItemList."""
        items = ItemList()
        items_data = items_payload
        if isinstance(items_data, dict):
            items_data = items_data.get('items', [])
        elif isinstance(items_data, ItemList):
            items_data = items_data.items
        if isinstance(items_data, list):
            for item_data in items_data:
                if isinstance(item_data, dict):
                    if 'quality' in item_data and isinstance(item_data['quality'], dict):
                        item_data['quality'] = QualityProfile(**item_data['quality'])
                    items.append(_deserialize_item(item_data))
                elif isinstance(item_data, CategoryItem):
                    items.append(item_data)
        return items


def load_settings(path: str | None = None) -> Settings:
    """Convenience function to load settings from the local YAML file."""
    return SettingsManager(yaml_path=path).load()
