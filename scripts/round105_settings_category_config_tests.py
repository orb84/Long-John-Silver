#!/usr/bin/env python3
"""Round 105 regression checks for category-owned Compass settings."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def assert_contains(text: str, needle: str, label: str) -> None:
    if needle not in text:
        raise AssertionError(f"Missing {label}: {needle}")


def assert_not_contains(text: str, needle: str, label: str) -> None:
    if needle in text:
        raise AssertionError(f"Unexpected {label}: {needle}")


def test_compass_category_owned_settings() -> None:
    panel = read("src/web/static/js/components/settingsPanel.js")
    assert_not_contains(panel, "Metadata and watch state", "old global metadata/watch-state panel")
    assert_contains(panel, "Library Categories", "category settings panel")
    assert_contains(panel, "Shared Torrent Search & Indexers", "shared search-only services panel")
    assert_contains(panel, "_categorySettingsBlock", "manifest-driven category block")
    assert_contains(panel, "pref-category-provider-input", "category provider toggles")
    assert_contains(panel, "pref-category-service-input", "category service controls")
    assert_contains(panel, "pref-category-nested-input", "nested category config controls")
    assert_contains(panel, "_categoryIntegrationPayload", "category integration payload")
    assert_contains(panel, "scheduler.enabled", "scheduler nested config UI")
    assert_contains(panel, "storage.inherit_global_thresholds", "storage nested config UI")
    assert_contains(panel, "lifecycle_policy", "lifecycle policy summary")


def test_advanced_contracts_panel_is_not_empty_or_confusing() -> None:
    settings_panel = read("src/web/static/js/components/settingsPanel.js")
    manifest_panel = read("src/web/static/js/components/categoryManifestPanel.js")
    assert_contains(settings_panel, "Advanced Category Contracts", "renamed manifest panel")
    assert_contains(settings_panel, "This is not another settings form", "manifest panel explanation")
    assert_contains(manifest_panel, "No category contracts returned", "empty-state message")
    assert_contains(manifest_panel, "Live config: config/categories/", "config file ownership row")
    assert_contains(manifest_panel, "Setup requirements / services", "setup requirements section")
    assert_contains(manifest_panel, "Editable properties declared by manifest", "properties section")
    assert_contains(manifest_panel, "Discovery sources", "discovery sources section")


def test_tv_yaml_settings_are_runtime_owned() -> None:
    tv_yaml = read("config/category-templates/tv.yaml")
    category_config = read("src/core/category_config.py")
    lifecycle = read("src/core/category_lifecycle.py")
    scheduler = read("src/core/scheduler.py")
    tv = read("src/core/categories/tv.py")
    base_category = read("src/core/categories/base.py")
    tv_agent = read("src/core/categories/tv_agent.py")
    tv_workflows = read("src/core/categories/tv_workflows.py")

    for key in (
        "library_path",
        "naming_template",
        "active_update_interval_days",
        "inactive_update_interval_days",
        "ended_update_interval_days",
        "upgrade_scan_interval_days",
        "metadata:",
        "scheduler:",
        "storage:",
        "lifecycle_policy:",
    ):
        assert_contains(tv_yaml, key, f"tv.yaml {key}")

    assert_contains(category_config, "'lifecycle_policy'", "category config reserved lifecycle policy")
    assert_contains(category_config, "('metadata', 'scheduler', 'storage', 'lifecycle_policy')", "nested config roundtrip")
    assert_contains(lifecycle, "lifecycle_policy_from_settings", "lifecycle policy uses settings")
    assert_contains(scheduler, "scheduler_config.get(\"enabled\") is False", "scheduler honors category scheduler.enabled")
    assert_contains(tv, "active_update_interval_days", "TV active interval property")
    assert_contains(base_category, "def get_naming_template", "base naming template accessor used by TV")
    assert_contains(tv_agent, "metadata_provider_enabled(settings, \"tmdb\"", "TV agent honors TMDB provider toggle")
    assert_contains(tv_agent, "metadata_provider_enabled(settings, \"tvmaze\"", "TV agent honors TVMaze provider toggle")
    assert_contains(tv_workflows, "metadata_provider_enabled(getattr(context, \"settings\", None), \"tmdb\"", "TV workflows honor TMDB provider toggle")
    assert_contains(tv_workflows, "metadata_provider_enabled(getattr(context, \"settings\", None), \"tvmaze\"", "TV workflows honor TVMaze provider toggle")


def test_category_services_are_manifest_declared() -> None:
    tv = read("src/core/categories/tv.py")
    movie = read("src/core/categories/movie.py")
    base_contract = read("src/core/categories/base_contract.py")
    for source, label in ((tv, "TV"), (movie, "Movie")):
        assert_contains(source, "def provider_setup_requirements", f"{label} provider setup requirements")
        for key in ("tmdb_api_key", "trakt_client_id", "plex_url", "plex_token", "opensubtitles_api_key"):
            assert_contains(source, key, f"{label} setup key {key}")
    assert_contains(tv, "TVMaze episode schedule provider", "TV TVMaze requirement")
    assert_contains(base_contract, "metadata_provider_enabled", "category metadata provider helper")
    assert_contains(base_contract, "category_config_section", "category nested config helper")
    assert_contains(base_contract, "lifecycle_policy_from_settings", "category lifecycle policy helper")


def test_category_creation_skill_points_to_category_owned_ui() -> None:
    guide = read("skills/category_creation_guide.md")
    tool = read("src/ai/tools/categories.py")
    assert_contains(guide, "Compass renders per-category paths, provider toggles, and service credentials", "skill Compass guidance")
    assert_contains(guide, "Advanced Category Contracts panel is read-only diagnostics", "skill manifest explanation")
    assert_contains(guide, "Do **not** add category-specific service fields to a global settings panel", "skill global-settings guardrail")
    assert_contains(tool, "Compass → Library Categories", "planner points to category settings")
    assert_contains(tool, "Advanced Category Contracts is read-only diagnostics", "planner manifest explanation")


def test_category_config_store_roundtrips_nested_sections() -> None:
    from src.core.category_config import CategoryConfigStore

    store = CategoryConfigStore(directory=ROOT / "config/categories", template_directory=ROOT / "config/category-templates")
    payload = {
        "category_id": "example",
        "enabled": True,
        "paths": {"library_path": "/tmp/example"},
        "properties": {"naming_template": "{title}"},
        "metadata": {"providers": {"tmdb": {"enabled": False}}},
        "scheduler": {"enabled": False},
        "storage": {"inherit_global_thresholds": True},
        "lifecycle_policy": {"policy_version": 7},
    }
    flat = store.flatten(payload)
    assert flat["metadata"]["providers"]["tmdb"]["enabled"] is False
    assert flat["scheduler"]["enabled"] is False
    assert flat["storage"]["inherit_global_thresholds"] is True
    assert flat["lifecycle_policy"]["policy_version"] == 7
    inflated = store.inflate("example", flat)
    assert inflated["metadata"] == payload["metadata"]
    assert inflated["scheduler"] == payload["scheduler"]
    assert inflated["storage"] == payload["storage"]
    assert inflated["lifecycle_policy"] == payload["lifecycle_policy"]


def main() -> None:
    tests = [
        test_compass_category_owned_settings,
        test_advanced_contracts_panel_is_not_empty_or_confusing,
        test_tv_yaml_settings_are_runtime_owned,
        test_category_services_are_manifest_declared,
        test_category_creation_skill_points_to_category_owned_ui,
        test_category_config_store_roundtrips_nested_sections,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("Round 105 category settings regression checks passed.")


if __name__ == "__main__":
    main()
