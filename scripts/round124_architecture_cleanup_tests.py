#!/usr/bin/env python3
"""Round 124 architecture cleanup regression checks."""

from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_category_config_save_does_not_persist_definition_only_fields() -> None:
    from src.core.category_config import CategoryConfigStore

    with TemporaryDirectory() as tmp:
        live = Path(tmp) / "categories"
        store = CategoryConfigStore(
            live,
            ROOT / "config/category-config-templates",
            ROOT / "config/category-definitions",
        )
        effective = store.load_all()
        store.save_all(effective)

        for category_id in ("music", "ebooks", "audiobooks"):
            body = (live / f"{category_id}.yaml").read_text(encoding="utf-8")
            for forbidden in (
                "display_name:",
                "default_folder:",
                "llm_profile:",
                "search_policy:",
                "capabilities:",
                "supported_operations:",
                "purpose:",
                "llm_usage:",
                "help_url:",
            ):
                require(forbidden not in body, f"{category_id} live config leaked definition-only field {forbidden}")


def test_category_config_save_cleans_older_polluted_properties() -> None:
    from src.core.category_config import CategoryConfigStore

    with TemporaryDirectory() as tmp:
        live = Path(tmp) / "categories"
        store = CategoryConfigStore(
            live,
            ROOT / "config/category-config-templates",
            ROOT / "config/category-definitions",
        )
        store.ensure_live_configs()
        music_path = live / "music.yaml"
        payload = yaml.safe_load(music_path.read_text(encoding="utf-8")) or {}
        payload.setdefault("properties", {})["display_name"] = "Music"
        payload["properties"]["search_policy"] = {"language_relevant": False}
        payload.setdefault("services", {}).setdefault("discogs", {})["label"] = "Discogs"
        music_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

        effective = store.load_all()
        store.save_all(effective)
        cleaned = yaml.safe_load(music_path.read_text(encoding="utf-8")) or {}
        require("properties" not in cleaned or "display_name" not in cleaned["properties"], "polluted display_name property should be removed")
        require("properties" not in cleaned or "search_policy" not in cleaned["properties"], "polluted search_policy property should be removed")
        require("discogs" not in cleaned.get("services", {}) or "label" not in cleaned["services"]["discogs"], "service labels should not persist in private config")


def test_prompt_builder_keeps_all_intents_reachable() -> None:
    from src.ai.prompt_builder import PromptBuilder
    from src.core.models import Intent

    builder = PromptBuilder()
    expected = {
        Intent.CONFIG: "TASK: Modify the user's configuration.",
        Intent.CHAT: "TASK: Open-ended conversation.",
        Intent.CLARIFY: "TASK: The user's intent was ambiguous.",
        Intent.DOWNLOAD: "TASK: Find/select torrents",
        Intent.SEARCH: "TASK: Research and report information.",
    }
    for intent, phrase in expected.items():
        guidance = builder._task_guidance(intent)
        require(isinstance(guidance, str) and phrase in guidance, f"{intent} guidance was unreachable or wrong")


def test_music_download_search_rules_are_category_owned() -> None:
    from src.ai.prompt_builder import PromptBuilder
    from src.core.categories.registry import CategoryRegistry
    from src.core.models import Intent

    music = CategoryRegistry.with_defaults().get("music")
    category_guidance = music.build_prompt_guidance("download")
    require("include the term 'discography'" in category_guidance, "music download guidance should include discography rule from category profile")
    require("OST" in category_guidance and "Original Soundtrack" in category_guidance, "music category owns soundtrack expansion wording")
    require("bootleg" in category_guidance, "music category owns bootleg constraints")

    generic_only = PromptBuilder().build_system_prompt(Intent.DOWNLOAD, active_category_id="music")
    require("For music," not in generic_only, "generic prompt should not hardcode music-specific rules")
    require("include the term 'discography'" not in generic_only, "discography rule should arrive through category guidance, not generic prompt code")

    with_category = PromptBuilder().build_system_prompt(
        Intent.DOWNLOAD,
        category_guidance="ACTIVE CATEGORY: Music (music)\n\n" + category_guidance,
        active_category_id="music",
    )
    require("include the term 'discography'" in with_category, "category-owned music rules should be present when music is active")


def test_definition_backed_non_video_parser_does_not_emit_video_traits() -> None:
    from src.core.categories.registry import CategoryRegistry

    music = CategoryRegistry.with_defaults().get("music")
    parsed = music.parse_name("Blur To The End 2024 1080p BluRay x265")
    require(parsed.resolution is None, "music parser should not emit video resolution traits")
    require(parsed.codec is None, "music parser should not emit video codec traits")


if __name__ == "__main__":
    for name in sorted(k for k in globals() if k.startswith("test_")):
        globals()[name]()
        print(f"PASS {name}")
