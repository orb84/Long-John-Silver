#!/usr/bin/env python3
"""Round 113 checks for category definition/config split and model picking.

These checks are structural and use only synthetic values. They never compare
against real API keys or private live configuration.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    """Read a repository file as UTF-8 text."""
    return (ROOT / rel).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    """Raise an assertion with a clear Round 113 failure message."""
    if not condition:
        raise AssertionError(message)


def load_yaml(path: Path) -> dict:
    """Load YAML as a mapping for structural assertions."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    require(isinstance(data, dict), f"{path} should be a YAML mapping")
    return data


def test_category_definitions_are_split_from_private_config_templates() -> None:
    """Tracked definitions hold behavior; config templates hold blank private values."""
    require(not (ROOT / "config/category-templates").exists(), "old mixed category-templates directory should not exist")
    for category_id in ("media", "tv", "movie", "general"):
        definition_path = ROOT / "config/category-definitions" / f"{category_id}.yaml"
        template_path = ROOT / "config/category-config-templates" / f"{category_id}.yaml"
        require(definition_path.exists(), f"missing tracked definition for {category_id}")
        require(template_path.exists(), f"missing blank config template for {category_id}")
        definition = load_yaml(definition_path)
        template = load_yaml(template_path)
        require(definition.get("category_id") == category_id, f"definition id mismatch for {category_id}")
        require(template.get("category_id") == category_id, f"config template id mismatch for {category_id}")
        require("llm_guidance" in definition or category_id == "media", f"{category_id} definition should teach LLM behavior")
        if category_id != "media":
            require("tools" in definition, f"{category_id} definition should declare tools/workflows")
        for definition_only in ("llm_guidance", "tools", "lifecycle_policy", "formats"):
            require(definition_only not in template, f"{definition_only} should not be stored in private config template for {category_id}")
    ignore = read(".gitignore")
    require("config/categories/*.yaml" in ignore, "live category YAML should be ignored")
    require("!config/category-definitions/*.yaml" in ignore, "tracked category definitions should be unignored")
    require("!config/category-config-templates/*.yaml" in ignore, "tracked config templates should be unignored")


def test_category_store_merges_definitions_but_saves_private_only() -> None:
    """Runtime sees inherited behavior, while save writes only private settings."""
    from src.core.category_config import CategoryConfigStore

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        definitions = root / "category-definitions"
        templates = root / "category-config-templates"
        live = root / "categories"
        shutil.copytree(ROOT / "config/category-definitions", definitions)
        shutil.copytree(ROOT / "config/category-config-templates", templates)
        store = CategoryConfigStore(live, template_directory=templates, definition_directory=definitions)
        loaded = store.load_all()
        media = loaded["media"]
        tv = loaded["tv"]
        require(tv.get("extends") == "media", "TV should inherit from the media definition")
        require("tmdb" in tv.get("services", {}), "TV should inherit shared media TMDB service")
        require("trakt" in tv.get("services", {}), "TV should inherit shared media Trakt service")
        require("tvmaze" in tv.get("services", {}), "TV should keep TV-specific TVMaze service")
        require("tv.download_season_pack" in tv.get("tools", {}).get("category_workflows", []), "TV workflow should come from definition")
        require(media.get("abstract") is True, "Media should remain an abstract runtime base")

        loaded["media"]["services"]["tmdb"]["api_key"] = "synthetic-round113-key"
        loaded["tv"]["services"]["tvmaze"]["enabled"] = False
        loaded["tv"]["library_path"] = "./synthetic/TV"
        store.save_all(loaded)

        media_private = load_yaml(live / "media.yaml")
        tv_private_text = (live / "tv.yaml").read_text(encoding="utf-8")
        tv_private = yaml.safe_load(tv_private_text) or {}
        require(media_private["services"]["tmdb"]["api_key"] == "synthetic-round113-key", "private media config should keep local API key")
        require(tv_private["services"]["tvmaze"]["enabled"] is False, "private TV config should keep TVMaze toggle")
        require(tv_private["paths"]["library_path"] == "./synthetic/TV", "private TV config should keep local library path")
        for definition_only in ("llm_guidance", "tools", "lifecycle_policy", "formats"):
            require(definition_only not in tv_private_text, f"{definition_only} should not leak into TV private config")
        require("tmdb:" not in tv_private_text, "inherited shared media services should not be duplicated into TV private config")


def test_compass_llm_models_are_endpoint_selects_not_text_inputs() -> None:
    """Compass LLM model fields should be endpoint-backed selectors."""
    panel = read("src/web/static/js/components/settingsPanel.js")
    require("_modelSelectControl('pref-llm-model'" in panel, "base model should use the endpoint model selector")
    require("/api/providers/${encodeURIComponent(provider)}/models?refresh=" in panel, "model selector should call provider /models endpoint")
    require("_populateModelSelect" in panel, "model selector should populate endpoint results")
    require("Current/custom:" in panel, "model selector should preserve current custom IDs")
    require("DOM.el('select', {\n                id: modelId" in panel, "model control should render a select")
    require("DOM.el('input', { type: 'text', id: 'pref-llm-model'" not in panel, "base model must not be a raw text input")
    require("pref-llm-lw-model" in panel and "pref-llm-std-model" in panel and "pref-llm-hv-model" in panel, "tier model selectors should still be present")


def test_scaffold_writes_definition_and_config_template() -> None:
    """New categories should scaffold shareable definitions and private templates separately."""
    scaffold = read("src/core/categories/scaffold.py")
    require("config/category-definitions/{spec.category_id}.yaml" in scaffold, "scaffold should emit category definition path")
    require("config/category-config-templates/{spec.category_id}.yaml" in scaffold, "scaffold should emit private config template path")
    require("def render_definition" in scaffold, "scaffold should render shareable category definitions")
    require("def render_config_template" in scaffold, "scaffold should render blank private config templates")


def main() -> None:
    """Run Round 113 checks as a standalone script."""
    for test in (
        test_category_definitions_are_split_from_private_config_templates,
        test_category_store_merges_definitions_but_saves_private_only,
        test_compass_llm_models_are_endpoint_selects_not_text_inputs,
        test_scaffold_writes_definition_and_config_template,
    ):
        test()
        print(f"PASS {test.__name__}")
    print("Round 113 category definition/config and model picker checks passed.")


if __name__ == "__main__":
    main()
