"""
Category definition and local-configuration persistence for LJS.

Tracked category definitions live in ``config/category-definitions``.  They are
safe to share and describe a category's class-like contract: inherited base,
services, tools, LLM instructions, lifecycle policy, filename examples, and
format rules.

Ignored local category configs live in ``config/categories`` and are
bootstrapped from tracked blank templates in ``config/category-config-templates``.
Those local files hold only machine/user-specific values: library paths,
service credentials, enable flags, scheduler/storage toggles, and user download
preferences.  Runtime code receives one effective merged view, but save-time
filtering prevents definition-only content from leaking back into local config.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import yaml
from loguru import logger


class CategoryConfigStore:
    """Load category definitions, merge local config, and save private settings.

    The store deliberately separates three concepts:

    * ``category-definitions``: tracked, shareable category contract files.
    * ``category-config-templates``: tracked blank defaults for new installs.
    * ``categories``: ignored live files edited by setup/Compass.

    ``load_all`` returns effective flattened configs because category runtime
    code needs a single view. ``save_all`` writes back only user-configurable
    fields so people can improve/share a category definition without exposing
    API keys, private paths, or personal preferences.
    """

    _DEFINITION_ONLY_TOP_LEVEL = {
        'display_name',
        'default_folder',
        'icon',
        'media_kind',
        'capabilities',
        'supported_operations',
        'llm_profile',
        'search_policy',
        'router_priority',
        'object_model',
    }

    _RESERVED_TOP_LEVEL = {
        'category_id',
        'abstract',
        'extends',
        'mixins',
        'enabled',
        'paths',
        'properties',
        'metadata',
        'scheduler',
        'storage',
        'lifecycle_policy',
        'services',
        'tools',
        'download_profile',
        'preferences',
        'llm_guidance',
        'notes',
        'formats',
        'definition_version',
        'runtime_dependencies',
        *_DEFINITION_ONLY_TOP_LEVEL,
    }

    _SERVICE_USER_FIELDS = {
        'enabled',
        'api_key',
        'token',
        'url',
        'client_id',
        'client_secret',
        'access_token',
        'refresh_token',
        'username',
        'password',
        'base_url',
        'server_url',
        'phone_number_id',
        'verify_token',
    }

    _SERVICE_DEFINITION_FIELDS = {
        'label',
        'purpose',
        'llm_usage',
        'help_url',
        'fields',
        'severity',
        'required',
        'required_fields',
        'why_it_matters',
        'validation_action',
    }

    _DOWNLOAD_USER_FIELDS = {
        'language',
        'preferred_language',
        'audio_language',
        'subtitle_languages',
        'preferred_resolution',
        'preferred_formats',
        'size_limit_mode',
        'max_size_gb',
        'max_file_size_mb',
        'max_bitrate_kbps',
        'quality',
        'quality_floor',
        'quality_ceiling',
        'pack_preference',
        'preserve_original_filename',
        'allow_archives',
        'allow_multi_file',
        'preferred_container',
        'preferred_audio_format',
        'preferred_lossless_format',
        'preferred_lossy_format',
        'preferred_bitrate_kbps',
        'quality_target',
        'apple_compatibility',
        'transcode_policy',
        'preserve_chapters',
        'chapter_strategy',
        'preferred_ebook_format',
        'preferred_ebook_formats',
        'format_priority',
        'edition_preference',
        'reader_device',
    }

    def __init__(
        self,
        directory: str | Path = 'config/categories',
        template_directory: str | Path | None = None,
        definition_directory: str | Path | None = None,
    ) -> None:
        """Create a store rooted at the provided live category directory."""
        self._directory = Path(directory)
        parent = self._directory.parent
        self._template_directory = Path(template_directory) if template_directory else parent / 'category-config-templates'
        self._definition_directory = Path(definition_directory) if definition_directory else parent / 'category-definitions'

    @property
    def directory(self) -> Path:
        """Return the ignored live directory that stores local category config."""
        return self._directory

    @property
    def template_directory(self) -> Path:
        """Return the tracked blank config-template directory."""
        return self._template_directory

    @property
    def config_template_directory(self) -> Path:
        """Return the tracked blank config-template directory."""
        return self._template_directory

    @property
    def definition_directory(self) -> Path:
        """Return the tracked category-definition directory."""
        return self._definition_directory

    def load_all(self) -> dict[str, dict[str, Any]]:
        """Load definitions plus local config with abstract inheritance applied."""
        self.ensure_live_configs()
        definitions = self._load_payloads_from(self._definition_directory)
        configs = self._load_payloads_from(self._directory)
        combined = self._combine_definitions_and_configs(definitions, configs)
        resolved_payloads: dict[str, dict[str, Any]] = {}
        for category_id in sorted(combined):
            try:
                resolved_payloads[category_id] = self._resolve_effective(category_id, combined, stack=[])
            except ValueError as exc:
                logger.warning(f'Failed to resolve category inheritance for {category_id}: {exc}')
                resolved_payloads[category_id] = combined[category_id]
        return {category_id: self.flatten(payload) for category_id, payload in resolved_payloads.items()}

    def load_definitions_only(self) -> dict[str, dict[str, Any]]:
        """Load tracked category definitions with inheritance/mixins, without live config.

        Registry discovery uses this method so adding a shareable YAML category
        can create a real runtime category without creating or reading private
        user config files.  Settings loading still uses ``load_all`` so live
        paths, credentials, and preferences are applied separately.
        """
        definitions = self._load_payloads_from(self._definition_directory)
        resolved_payloads: dict[str, dict[str, Any]] = {}
        for category_id in sorted(definitions):
            try:
                resolved_payloads[category_id] = self._resolve_effective(category_id, definitions, stack=[])
            except ValueError as exc:
                logger.warning(f'Failed to resolve category definition inheritance for {category_id}: {exc}')
                resolved_payloads[category_id] = definitions[category_id]
        return resolved_payloads

    def ensure_live_configs(self) -> None:
        """Create missing ignored category configs from blank config templates."""
        self._directory.mkdir(parents=True, exist_ok=True)
        for template_path in sorted(self._template_directory.glob('*.yaml')) if self._template_directory.exists() else []:
            target_path = self._directory / template_path.name
            if target_path.exists():
                continue
            try:
                shutil.copyfile(template_path, target_path)
                logger.info('Created local category config {} from template {}.', target_path, template_path)
            except OSError as exc:
                logger.warning(f'Failed to bootstrap category config {target_path}: {exc}')

        # Custom definitions may omit a user config template.  Create a minimal
        # ignored config so Compass has a stable place to save user values.
        for definition_path in sorted(self._definition_directory.glob('*.yaml')) if self._definition_directory.exists() else []:
            target_path = self._directory / definition_path.name
            if target_path.exists():
                continue
            try:
                category_id, _ = self.load_payload(definition_path)
                self.save_file(category_id, {'category_id': category_id, 'enabled': True})
                logger.info('Created minimal local category config {} for definition {}.', target_path, definition_path)
            except Exception as exc:
                logger.warning(f'Failed to create minimal local config for {definition_path}: {exc}')

    def _load_payloads_from(self, directory: Path) -> dict[str, dict[str, Any]]:
        """Load all YAML payloads from a directory keyed by category ID."""
        payloads: dict[str, dict[str, Any]] = {}
        if not directory.exists():
            return payloads
        for path in sorted(directory.glob('*.yaml')):
            try:
                category_id, payload = self.load_payload(path)
                payloads[category_id] = payload
            except (OSError, ValueError, yaml.YAMLError) as exc:
                logger.warning(f'Failed to load category YAML {path}: {exc}')
        return payloads

    def _combine_definitions_and_configs(
        self,
        definitions: dict[str, dict[str, Any]],
        configs: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Return definition payloads deep-merged with local config payloads."""
        combined: dict[str, dict[str, Any]] = {}
        for category_id in sorted(set(definitions) | set(configs)):
            definition = definitions.get(category_id) or {}
            config = configs.get(category_id) or {}
            payload = self._deep_merge(definition, config)
            payload.setdefault('category_id', category_id)
            # Inheritance is a category-definition concern.  A legacy/custom live
            # config may still provide it when no definition exists.
            if 'extends' not in payload and definition.get('extends'):
                payload['extends'] = definition.get('extends')
            if 'mixins' not in payload and definition.get('mixins'):
                payload['mixins'] = definition.get('mixins')
            combined[category_id] = payload
        return combined

    def load_payload(self, path: Path) -> tuple[str, dict[str, Any]]:
        """Load one human-readable category YAML payload without flattening."""
        with path.open('r', encoding='utf-8') as handle:
            data = yaml.safe_load(handle) or {}
        if not isinstance(data, dict):
            raise ValueError('category YAML must be a mapping')
        category_id = str(data.get('category_id') or path.stem).strip()
        if not category_id:
            raise ValueError('category YAML is missing category_id')
        data = dict(data)
        data.setdefault('category_id', category_id)
        return category_id, data

    def load_file(self, path: Path) -> tuple[str, dict[str, Any]]:
        """Load one category YAML file and return flattened settings."""
        category_id, payload = self.load_payload(path)
        return category_id, self.flatten(payload)

    def _resolve_extends(
        self,
        category_id: str,
        payloads: dict[str, dict[str, Any]],
        *,
        stack: list[str],
    ) -> dict[str, Any]:
        """Compatibility wrapper for older tests/callers."""
        return self._resolve_effective(category_id, payloads, stack=stack)

    def _resolve_effective(
        self,
        category_id: str,
        payloads: dict[str, dict[str, Any]],
        *,
        stack: list[str],
    ) -> dict[str, Any]:
        """Return an effective payload with parent inheritance and mixins applied.

        ``extends`` models an is-a relationship: parent values load first, then
        child values override them. ``mixins`` model additive capabilities such as
        audio conversion; they are merged after the parent and before the child.
        Non-inheritable identity flags such as ``abstract`` and ``category_id`` do
        not leak from parents/mixins into concrete children.
        """
        if category_id in stack:
            raise ValueError('circular category inheritance/mixins: ' + ' -> '.join(stack + [category_id]))
        payload = dict(payloads.get(category_id) or {})
        if not payload:
            raise ValueError(f'category {category_id!r} not found')

        base: dict[str, Any] = {}
        parent_key = self._clean_ref(payload.get('extends'))
        if parent_key:
            if parent_key not in payloads:
                raise ValueError(f'parent category {parent_key!r} not found')
            parent_payload = self._resolve_effective(parent_key, payloads, stack=stack + [category_id])
            base = self._deep_merge(base, self._inheritable_payload(parent_payload))

        mixin_keys = self._normalize_mixins(payload.get('mixins'))
        for mixin_key in mixin_keys:
            if mixin_key not in payloads:
                raise ValueError(f'mixin category {mixin_key!r} not found')
            mixin_payload = self._resolve_effective(mixin_key, payloads, stack=stack + [category_id])
            base = self._deep_merge(base, self._inheritable_payload(mixin_payload))

        merged = self._deep_merge(base, payload)
        merged['category_id'] = payload.get('category_id') or category_id
        if parent_key:
            merged['extends'] = parent_key
        if mixin_keys:
            merged['mixins'] = mixin_keys
        if 'abstract' not in payload:
            merged.pop('abstract', None)
        return merged

    @staticmethod
    def _clean_ref(value: object) -> str:
        """Return a normalized category reference or an empty string."""
        return str(value or '').strip()

    @classmethod
    def _normalize_mixins(cls, value: object) -> list[str]:
        """Normalize ``mixins`` from YAML into a de-duplicated list of IDs."""
        if value in (None, ''):
            return []
        raw_values = value if isinstance(value, list) else [value]
        result: list[str] = []
        seen: set[str] = set()
        for item in raw_values:
            token = cls._clean_ref(item)
            if token and token not in seen:
                seen.add(token)
                result.append(token)
        return result

    @staticmethod
    def _inheritable_payload(payload: dict[str, Any]) -> dict[str, Any]:
        """Strip identity-only keys before merging parent/mixin payloads."""
        result = dict(payload or {})
        for key in ('category_id', 'abstract'):
            result.pop(key, None)
        return result

    @classmethod
    def _deep_merge(cls, base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        """Recursively merge dictionaries while replacing scalar/list values."""
        result = dict(base or {})
        for key, value in (override or {}).items():
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = cls._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    def save_all(self, category_settings: dict[str, dict[str, Any]]) -> None:
        """Persist only user-editable category config into ignored YAML files."""
        self._directory.mkdir(parents=True, exist_ok=True)
        raw_configs = self._load_payloads_from(self._directory)
        definitions = self._load_payloads_from(self._definition_directory)
        combined_before = self._combine_definitions_and_configs(definitions, raw_configs)

        for category_id, values in sorted((category_settings or {}).items()):
            if not category_id:
                continue
            payload = self.inflate(category_id, values or {})
            definition = definitions.get(category_id, {})
            previous_config = raw_configs.get(category_id, {})
            payload = self._live_config_payload(category_id, payload, definition, previous_config)

            try:
                base_payload = self._resolve_inheritance_base_payload(category_id, values or {}, definition, previous_config, combined_before)
                if base_payload:
                    payload = self._compact_child_payload(payload, base_payload, previous_config)
            except ValueError as exc:
                logger.warning(f'Could not compact inherited config for {category_id}: {exc}')
            payload['category_id'] = category_id
            self.save_file(category_id, payload)

    def _resolve_inheritance_base_payload(
        self,
        category_id: str,
        values: dict[str, Any],
        definition: dict[str, Any],
        previous_config: dict[str, Any],
        combined_before: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Return the effective parent/mixin payload inherited by one category.

        Save compaction must consider both ``extends`` and ``mixins``.  Without
        this, mixin-provided defaults such as audio conversion preferences would
        be copied into concrete private configs on every save, which defeats the
        definition/config split.
        """
        base: dict[str, Any] = {}
        parent_id = self._clean_ref(values.get('extends') or definition.get('extends') or previous_config.get('extends'))
        if parent_id:
            if parent_id not in combined_before:
                raise ValueError(f'parent category {parent_id!r} not found')
            parent_payload = self._resolve_effective(parent_id, combined_before, stack=[category_id])
            base = self._deep_merge(base, self._inheritable_payload(parent_payload))

        mixin_ids = self._normalize_mixins(values.get('mixins') or definition.get('mixins') or previous_config.get('mixins'))
        for mixin_id in mixin_ids:
            if mixin_id not in combined_before:
                raise ValueError(f'mixin category {mixin_id!r} not found')
            mixin_payload = self._resolve_effective(mixin_id, combined_before, stack=[category_id])
            base = self._deep_merge(base, self._inheritable_payload(mixin_payload))
        return base

    def save_file(self, category_id: str, payload: dict[str, Any]) -> None:
        """Atomically save one ignored local category config payload."""
        self._directory.mkdir(parents=True, exist_ok=True)
        path = self._directory / f'{category_id}.yaml'
        tmp_path = path.with_suffix('.yaml.tmp')
        with tmp_path.open('w', encoding='utf-8') as handle:
            yaml.safe_dump(payload, handle, default_flow_style=False, sort_keys=False)
        tmp_path.replace(path)

    def _live_config_payload(
        self,
        category_id: str,
        effective_payload: dict[str, Any],
        definition_payload: dict[str, Any],
        previous_config: dict[str, Any],
    ) -> dict[str, Any]:
        """Filter an effective payload down to user/machine-specific config."""
        has_definition = bool(definition_payload)
        result: dict[str, Any] = {'category_id': category_id}

        if not has_definition:
            for key in ('abstract', 'extends', 'mixins'):
                if key in effective_payload:
                    result[key] = effective_payload[key]
        for key in ('enabled',):
            if key in effective_payload or key in previous_config:
                result[key] = bool(effective_payload.get(key, previous_config.get(key, True)))

        for section in ('paths', 'properties', 'scheduler', 'storage', 'preferences'):
            value = effective_payload.get(section)
            previous_value = previous_config.get(section)
            if not isinstance(value, dict):
                continue
            section_value = dict(value)
            if section == 'properties':
                for definition_key in self._DEFINITION_ONLY_TOP_LEVEL:
                    section_value.pop(definition_key, None)
            if section == 'paths':
                previous_paths = previous_value if isinstance(previous_value, dict) else {}
                # Blank category paths mean "use settings.library_root/<category folder>".
                # Keep a blank only when an older private config had an explicit
                # override so the user can clear that override; otherwise avoid
                # writing meaningless empty path keys into new installs.
                for path_key in ('library_path', 'library_root'):
                    if section_value.get(path_key) in (None, '') and path_key not in previous_paths:
                        section_value.pop(path_key, None)
            if section_value or isinstance(previous_value, dict):
                result[section] = section_value

        services = self._filter_service_config(
            effective_payload.get('services'),
            previous_config.get('services'),
            definition_payload.get('services'),
        )
        if services:
            result['services'] = services

        metadata = self._filter_metadata_config(
            effective_payload.get('metadata'),
            previous_config.get('metadata'),
            definition_payload.get('metadata'),
        )
        if metadata:
            result['metadata'] = metadata

        download_profile = self._filter_download_profile(
            effective_payload.get('download_profile'), previous_config.get('download_profile')
        )
        if download_profile:
            result['download_profile'] = download_profile
        return result

    @classmethod
    def _filter_service_config(
        cls,
        services: object,
        previous: object,
        definition: object | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Keep only credential/toggle fields that are genuinely local config.

        Effective settings contain definition service declarations merged with
        private values.  Save-time filtering must not write definition-only
        labels, purposes, or unchanged default enable flags back into ignored
        live config files, or the definition/config split collapses over time.
        """
        if not isinstance(services, dict):
            return {}
        previous = previous if isinstance(previous, dict) else {}
        definition = definition if isinstance(definition, dict) else {}
        result: dict[str, dict[str, Any]] = {}
        for service_id, raw_cfg in sorted(services.items()):
            if not isinstance(raw_cfg, dict):
                continue
            previous_cfg = previous.get(service_id) if isinstance(previous.get(service_id), dict) else {}
            definition_cfg = definition.get(service_id) if isinstance(definition.get(service_id), dict) else {}
            previous_user_keys = set(previous_cfg.keys()) - set(cls._SERVICE_DEFINITION_FIELDS)
            allowed = set(cls._SERVICE_USER_FIELDS) | previous_user_keys
            filtered = {key: value for key, value in raw_cfg.items() if key in allowed}
            local: dict[str, Any] = {}
            for key, value in filtered.items():
                if key in previous_cfg or key not in definition_cfg or value != definition_cfg.get(key):
                    local[key] = value
            if local:
                result[str(service_id)] = local
        return result

    @classmethod
    def _filter_metadata_config(cls, metadata: object, previous: object, definition: object | None = None) -> dict[str, Any]:
        """Keep only user-editable metadata provider enable flags."""
        if not isinstance(metadata, dict):
            return {}
        previous = previous if isinstance(previous, dict) else {}
        definition = definition if isinstance(definition, dict) else {}
        providers = metadata.get('providers') if isinstance(metadata.get('providers'), dict) else {}
        previous_providers = previous.get('providers') if isinstance(previous.get('providers'), dict) else {}
        definition_providers = definition.get('providers') if isinstance(definition.get('providers'), dict) else {}
        filtered_providers: dict[str, dict[str, Any]] = {}
        for provider, cfg in providers.items():
            previous_cfg = previous_providers.get(provider) if isinstance(previous_providers.get(provider), dict) else {}
            definition_cfg = definition_providers.get(provider) if isinstance(definition_providers.get(provider), dict) else {}
            if isinstance(cfg, dict):
                out = {}
                if 'enabled' in cfg and ('enabled' in previous_cfg or 'enabled' not in definition_cfg or bool(cfg.get('enabled')) != bool(definition_cfg.get('enabled'))):
                    out['enabled'] = bool(cfg.get('enabled'))
                for key in previous_cfg:
                    if key in cfg and key != 'enabled':
                        out[key] = cfg[key]
                if out:
                    filtered_providers[str(provider)] = out
            elif isinstance(cfg, bool):
                if provider in previous_providers or provider not in definition_providers or bool(cfg) != bool(definition_providers.get(provider)):
                    filtered_providers[str(provider)] = {'enabled': bool(cfg)}
        return {'providers': filtered_providers} if filtered_providers else {}

    @classmethod
    def _filter_download_profile(cls, profile: object, previous: object) -> dict[str, Any]:
        """Keep user preference fields from a category download profile."""
        if not isinstance(profile, dict):
            return {}
        previous = previous if isinstance(previous, dict) else {}
        allowed = set(cls._DOWNLOAD_USER_FIELDS) | set(previous.keys())
        return {key: value for key, value in profile.items() if key in allowed}

    @classmethod
    def _compact_child_payload(
        cls,
        child: dict[str, Any],
        parent: dict[str, Any],
        explicit_child: dict[str, Any],
    ) -> dict[str, Any]:
        """Remove inherited parent values unless they were explicit child config."""
        result: dict[str, Any] = {}
        explicit_child = explicit_child or {}
        parent = parent or {}
        for key, value in (child or {}).items():
            if key == 'category_id':
                result[key] = value
                continue
            explicit_has_key = key in explicit_child
            parent_has_key = key in parent
            if not explicit_has_key and parent_has_key:
                continue
            parent_value = parent.get(key)
            explicit_value = explicit_child.get(key) if explicit_has_key else None
            if isinstance(value, dict):
                nested_parent = parent_value if isinstance(parent_value, dict) else {}
                nested_explicit = explicit_value if isinstance(explicit_value, dict) else {}
                nested = cls._compact_child_payload(value, nested_parent, nested_explicit)
                nested.pop('category_id', None)
                if nested:
                    result[key] = nested
            elif not parent_has_key or value != parent_value:
                result[key] = value
        return result

    def flatten(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Flatten a human-readable effective payload into runtime settings."""
        result: dict[str, Any] = {}
        for key in ('abstract', 'extends', 'mixins', 'enabled'):
            if key in payload:
                result[key] = bool(payload[key]) if key in {'abstract', 'enabled'} else payload[key]

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

        for nested_key in (
            'metadata', 'scheduler', 'storage', 'lifecycle_policy', 'services',
            'tools', 'download_profile', 'preferences', 'llm_guidance', 'formats',
            'runtime_dependencies',
        ):
            nested_value = payload.get(nested_key)
            if isinstance(nested_value, dict):
                result[nested_key] = nested_value

        for key, value in payload.items():
            if key not in self._RESERVED_TOP_LEVEL:
                result[key] = value
        return result

    def inflate(self, category_id: str, values: dict[str, Any]) -> dict[str, Any]:
        """Inflate flattened runtime category settings into YAML structure."""
        values = dict(values or {})
        payload: dict[str, Any] = {'category_id': category_id, 'paths': {}, 'properties': {}}
        if 'enabled' in values:
            payload['enabled'] = bool(values.pop('enabled'))
        else:
            payload['enabled'] = True
        abstract = values.pop('abstract', None)
        if abstract is not None:
            payload['abstract'] = bool(abstract)
        parent = values.pop('extends', None)
        if parent:
            payload['extends'] = parent
        mixins = values.pop('mixins', None)
        if mixins:
            payload['mixins'] = mixins

        library_path = values.pop('library_path', None)
        if library_path is not None:
            payload['paths']['library_path'] = library_path

        for key in list(values.keys()):
            if key.endswith('_path') or key.endswith('_root'):
                payload['paths'][key] = values.pop(key)

        for nested_key in (
            'metadata', 'scheduler', 'storage', 'lifecycle_policy', 'services',
            'tools', 'download_profile', 'preferences', 'llm_guidance', 'formats',
            'runtime_dependencies',
        ):
            nested_value = values.pop(nested_key, None)
            if isinstance(nested_value, dict):
                payload[nested_key] = nested_value

        # Runtime settings may contain definition-only keys from older polluted
        # private configs.  Do not reinterpret those as user properties.
        for definition_key in self._DEFINITION_ONLY_TOP_LEVEL:
            values.pop(definition_key, None)

        for key, value in values.items():
            payload['properties'][key] = value

        if not payload['paths']:
            payload.pop('paths')
        if not payload['properties']:
            payload.pop('properties')
        return payload
