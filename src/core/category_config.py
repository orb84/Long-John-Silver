"""
Category configuration persistence for LJS.

The public repository tracks sanitized category templates under
``config/category-templates``. Live category settings are copied from those
public templates into ignored ``config/categories`` files on first launch and
then edited by setup/Compass at runtime.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import yaml
from loguru import logger


class CategoryConfigStore:
    """Loads and saves category-specific local configuration YAML files.

    The store supports a human-readable category config shape while exposing a
    flattened dict to the runtime ``Settings.category_settings`` field. This
    keeps UI, scheduler, storage, and category manifests on a single effective
    settings object while preventing public templates from receiving live paths,
    API credentials, or other private values.
    """

    _RESERVED_TOP_LEVEL = {
        'category_id',
        'enabled',
        'paths',
        'properties',
        'metadata',
        'scheduler',
        'storage',
        'lifecycle_policy',
        'notes',
    }

    def __init__(
        self,
        directory: str | Path = 'config/categories',
        template_directory: str | Path | None = None,
    ) -> None:
        """Create a store rooted at the provided live category directory.

        Args:
            directory: Ignored folder containing live ``<category_id>.yaml``
                files edited by setup and Compass.
            template_directory: Tracked folder containing public category YAML
                templates copied into ``directory`` when live files are missing.
        """
        self._directory = Path(directory)
        self._template_directory = Path(template_directory) if template_directory else self._directory.parent / 'category-templates'

    @property
    def directory(self) -> Path:
        """Return the live directory that stores category config files."""
        return self._directory

    @property
    def template_directory(self) -> Path:
        """Return the public template directory used for first-launch bootstrap."""
        return self._template_directory

    def load_all(self) -> dict[str, dict[str, Any]]:
        """Load and flatten all live category config files.

        Returns:
            Mapping keyed by category ID with flattened runtime settings.
        """
        self.ensure_live_configs()
        configs: dict[str, dict[str, Any]] = {}
        if not self._directory.exists():
            return configs
        for path in sorted(self._directory.glob('*.yaml')):
            try:
                category_id, values = self.load_file(path)
            except (OSError, ValueError, yaml.YAMLError) as exc:
                logger.warning(f'Failed to load category config {path}: {exc}')
                continue
            configs[category_id] = values
        return configs

    def ensure_live_configs(self) -> None:
        """Create missing live category config files from public templates."""
        if not self._template_directory.exists():
            return
        self._directory.mkdir(parents=True, exist_ok=True)
        for template_path in sorted(self._template_directory.glob('*.yaml')):
            target_path = self._directory / template_path.name
            if target_path.exists():
                continue
            try:
                shutil.copyfile(template_path, target_path)
                logger.info('Created local category config {} from template {}.', target_path, template_path)
            except OSError as exc:
                logger.warning(f'Failed to bootstrap category config {target_path}: {exc}')

    def load_file(self, path: Path) -> tuple[str, dict[str, Any]]:
        """Load one category config file.

        Args:
            path: YAML path to read.

        Returns:
            Tuple of category ID and flattened settings.
        """
        with path.open('r', encoding='utf-8') as handle:
            data = yaml.safe_load(handle) or {}
        if not isinstance(data, dict):
            raise ValueError('category config must be a mapping')
        category_id = str(data.get('category_id') or path.stem).strip()
        if not category_id:
            raise ValueError('category config is missing category_id')
        return category_id, self.flatten(data)

    def save_all(self, category_settings: dict[str, dict[str, Any]]) -> None:
        """Persist flattened runtime category settings into live YAML files.

        Args:
            category_settings: Effective runtime settings keyed by category ID.
        """
        self._directory.mkdir(parents=True, exist_ok=True)
        for category_id, values in sorted((category_settings or {}).items()):
            if not category_id:
                continue
            payload = self.inflate(category_id, values or {})
            self.save_file(category_id, payload)

    def save_file(self, category_id: str, payload: dict[str, Any]) -> None:
        """Atomically save one live category config payload.

        Args:
            category_id: Category identifier used as the filename stem.
            payload: Human-readable category config mapping.
        """
        path = self._directory / f'{category_id}.yaml'
        tmp_path = path.with_suffix('.yaml.tmp')
        with tmp_path.open('w', encoding='utf-8') as handle:
            yaml.safe_dump(payload, handle, default_flow_style=False, sort_keys=False)
        tmp_path.replace(path)

    def flatten(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Flatten a human-readable category config into runtime settings.

        The flattened mapping intentionally preserves nested ``metadata``,
        ``scheduler``, and ``storage`` sections because category-specific
        services may consume them directly, while common path/property values
        become direct keys for dynamic manifest/UI property handling.
        """
        result: dict[str, Any] = {}
        if 'enabled' in payload:
            result['enabled'] = bool(payload.get('enabled'))

        paths = payload.get('paths')
        if isinstance(paths, dict):
            library_path = paths.get('library_path') or paths.get('library_root')
            if library_path is not None:
                result['library_path'] = library_path
            for key, value in paths.items():
                if key not in {'library_path', 'library_root'}:
                    result[key] = value

        properties = payload.get('properties')
        if isinstance(properties, dict):
            result.update(properties)

        for nested_key in ('metadata', 'scheduler', 'storage', 'lifecycle_policy'):
            nested_value = payload.get(nested_key)
            if isinstance(nested_value, dict):
                result[nested_key] = nested_value

        for key, value in payload.items():
            if key not in self._RESERVED_TOP_LEVEL:
                result[key] = value
        return result

    def inflate(self, category_id: str, values: dict[str, Any]) -> dict[str, Any]:
        """Inflate flattened runtime category settings into YAML structure.

        Args:
            category_id: Category identifier.
            values: Flattened category settings from ``Settings``.

        Returns:
            Human-readable category config payload.
        """
        values = dict(values or {})
        payload: dict[str, Any] = {
            'category_id': category_id,
            'enabled': bool(values.pop('enabled', True)),
            'paths': {},
            'properties': {},
        }

        library_path = values.pop('library_path', None)
        if library_path is not None:
            payload['paths']['library_path'] = library_path

        for key in list(values.keys()):
            if key.endswith('_path') or key.endswith('_root'):
                payload['paths'][key] = values.pop(key)

        for nested_key in ('metadata', 'scheduler', 'storage', 'lifecycle_policy'):
            nested_value = values.pop(nested_key, None)
            if isinstance(nested_value, dict):
                payload[nested_key] = nested_value

        payload['properties'].update(values)
        if not payload['paths']:
            payload.pop('paths')
        if not payload['properties']:
            payload.pop('properties')
        return payload
