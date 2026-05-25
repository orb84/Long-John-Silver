#!/usr/bin/env python3
"""Round 108 checks for local-only settings and public templates.

The checks are structural and intentionally do not contain or compare against any
real credential values. They verify that source-controlled files are templates,
while live settings/config files are ignored and bootstrapped locally.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
import sys

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]


SECRET_FIELD_HINTS = (
    "api_key",
    "token",
    "password_hash",
    "secret",
    "access_token",
    "refresh_token",
)


def read(rel: str) -> str:
    """Read a repository file as UTF-8 text."""
    return (ROOT / rel).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    """Raise an assertion with a clear Round 108 failure message."""
    if not condition:
        raise AssertionError(message)


def _walk_secret_like_values(obj: object, path: str = "") -> list[str]:
    """Return non-empty values under secret-looking keys in a template object."""
    findings: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            next_path = f"{path}.{key}" if path else str(key)
            if any(hint in str(key).lower() for hint in SECRET_FIELD_HINTS):
                if value not in (None, "", [], {}, False):
                    findings.append(next_path)
            findings.extend(_walk_secret_like_values(value, next_path))
    elif isinstance(obj, list):
        for index, item in enumerate(obj):
            findings.extend(_walk_secret_like_values(item, f"{path}[{index}]"))
    return findings


def test_public_settings_are_templates_only() -> None:
    """The public config root must expose a template, not a live settings file."""
    require((ROOT / "config/settings.template.yaml").exists(), "settings.template.yaml should exist")
    require(not (ROOT / "config/settings.yaml").exists(), "config/settings.yaml must not exist in the release tree")
    template = yaml.safe_load(read("config/settings.template.yaml")) or {}
    findings = _walk_secret_like_values(template)
    require(not findings, "settings template has non-empty secret-looking fields: " + ", ".join(findings))
    require(template.get("setup_complete") is False, "settings template must not mark setup complete")


def test_gitignore_blocks_live_settings_and_runtime_data() -> None:
    """Live local files and runtime stores must be covered by .gitignore."""
    ignore = read(".gitignore")
    for pattern in (
        "config/settings.yaml",
        "config/settings.local.yaml",
        "config/categories/*.yaml",
        "data/api_keys.json",
        "data/*.jsonl",
        "data/cache/",
    ):
        require(pattern in ignore, f".gitignore should include {pattern}")
    require("!config/settings.template.yaml" in ignore, "settings template should remain trackable")
    require("!config/category-templates/*.yaml" in ignore, "category templates should remain trackable")


def test_settings_manager_migrates_legacy_settings_without_reset() -> None:
    """An old config/settings.yaml install is renamed into the ignored local path."""
    from src.core.config import SettingsManager

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        legacy = root / "settings.yaml"
        live = root / "settings.local.yaml"
        template = root / "settings.template.yaml"
        categories = root / "categories"
        cat_templates = root / "category-templates"
        template.write_text("setup_complete: false\ndownload_dir: ./downloads\n", encoding="utf-8")
        legacy.write_text("setup_complete: true\ndownload_dir: /kept/downloads\n", encoding="utf-8")
        manager = SettingsManager(
            yaml_path=str(live),
            template_path=str(template),
            legacy_yaml_path=str(legacy),
            category_config_dir=str(categories),
            category_template_dir=str(cat_templates),
        )
        settings = manager.load()
        require(live.exists(), "legacy settings should be migrated to the local settings path")
        require(not legacy.exists(), "legacy settings.yaml should be removed after migration")
        require(settings.setup_complete is True, "existing setup state should be preserved")
        require(settings.download_dir == "/kept/downloads", "existing paths should be preserved")


def test_category_templates_bootstrap_live_category_configs() -> None:
    """Missing live category files are copied from public category templates."""
    from src.core.category_config import CategoryConfigStore

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        live_dir = root / "categories"
        template_dir = root / "category-templates"
        template_dir.mkdir()
        (template_dir / "general.yaml").write_text(
            "category_id: general\npaths:\n  library_path: ./library/General\n",
            encoding="utf-8",
        )
        store = CategoryConfigStore(live_dir, template_directory=template_dir)
        loaded = store.load_all()
        require((live_dir / "general.yaml").exists(), "live general category config should be created")
        require(loaded["general"]["library_path"] == "./library/General", "template values should load")


def main() -> None:
    """Run Round 108 checks as a standalone script."""
    for test in (
        test_public_settings_are_templates_only,
        test_gitignore_blocks_live_settings_and_runtime_data,
        test_settings_manager_migrates_legacy_settings_without_reset,
        test_category_templates_bootstrap_live_category_configs,
    ):
        test()
        print(f"PASS {test.__name__}")
    print("Round 108 local settings bootstrap checks passed.")


if __name__ == "__main__":
    main()
