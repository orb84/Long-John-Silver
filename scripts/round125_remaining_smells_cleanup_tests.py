#!/usr/bin/env python3
"""Round 125 cleanup regression checks for remaining architecture smells."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_router_matching_is_boundary_aware() -> None:
    from src.core.categories.router_matching import router_token_matches

    require(not router_token_matches("please find Blur", "ep"), "short router token EP must not match the middle of 'please'")
    require(router_token_matches("Find the Blur EP", "ep"), "short router token EP should match as its own token")
    require(router_token_matches("download a TV show", "tv"), "short category token TV should match as its own token")
    require(router_token_matches("Original-Soundtrack FLAC", "original soundtrack"), "multi-word router tokens should tolerate release separators")


def test_registry_resolution_does_not_double_count_or_substring_match_router_tokens() -> None:
    from src.core.categories.registry import CategoryRegistry

    registry = CategoryRegistry.with_defaults()
    require(registry.resolve_from_text("please help me choose settings") is None, "router should not infer Music from the EP inside 'please'")
    music = registry.resolve_from_text("find the Blur EP in FLAC")
    require(music is not None and music.category_id == "music", "explicit EP/FLAC music request should still route to music")
    ost = registry.resolve_from_text("find the soundtrack of Interstellar")
    require(ost is not None and ost.category_id == "music", "soundtrack/OST requests should route to music before search expansion")


def test_definition_backed_category_delegates_candidate_and_audio_workflow_smells() -> None:
    source = (ROOT / "src/core/categories/definition_backed.py").read_text(encoding="utf-8")
    require("DefinitionCandidateValidator" in source, "definition-backed category should delegate candidate validation")
    require("AudioConversionService" in source, "definition-backed category should delegate FFmpeg/audio conversion")
    for old_marker in (
        "def _ffmpeg_command",
        "def _run_ffmpeg_conversion",
        "def _has_foreign_category_signature",
        "def _candidate_has_bundle_term",
    ):
        require(old_marker not in source, f"definition-backed category still owns extracted concern: {old_marker}")


def test_audio_conversion_command_preserves_metadata_chapters_and_artwork() -> None:
    from pathlib import Path
    from src.core.categories.audio_conversion import AudioConversionService

    command = AudioConversionService.ffmpeg_command(
        "ffmpeg",
        Path("Album.flac"),
        Path("Album.m4a"),
        "apple_lossless_m4a",
        overwrite=False,
    )
    joined = " ".join(command)
    require("-map_metadata 0" in joined, "conversion command must preserve global metadata")
    require("-map_chapters 0" in joined, "conversion command must preserve chapters")
    require("-map 0:v?" in joined, "conversion command must preserve optional embedded cover art")
    require("-c:a alac" in joined, "lossless Apple profile should encode ALAC")


def test_metadata_resolver_uses_declarative_provider_profiles() -> None:
    from src.integrations.category_metadata import CategoryMetadataResolver

    import yaml
    from src.core.categories.definition_backed import DefinitionBackedCategory

    category = DefinitionBackedCategory(yaml.safe_load((ROOT / "config/category-definitions/music.yaml").read_text(encoding="utf-8")))
    resolver = CategoryMetadataResolver(category, settings=None)
    profile = resolver._provider_profile()
    require([spec.provider for spec in profile][:2] == ["musicbrainz", "discogs"], "music profile should be declarative and ordered")
    body = (ROOT / "src/integrations/category_metadata.py").read_text(encoding="utf-8")
    registry = (ROOT / "src/integrations/metadata_providers/registry.py").read_text(encoding="utf-8")
    require('if self.category.category_id == "music"' not in body, "resolver should not dispatch provider profiles through category-specific if ladders")
    require("_PROVIDERS" in registry, "provider registry should expose provider profiles as data")


if __name__ == "__main__":
    for name in sorted(k for k in globals() if k.startswith("test_")):
        globals()[name]()
        print(f"PASS {name}")
