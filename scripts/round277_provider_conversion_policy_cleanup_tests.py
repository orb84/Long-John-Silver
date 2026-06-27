#!/usr/bin/env python3
"""Round 277 regression tests for provider/conversion policy cleanup."""

from __future__ import annotations

from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def require(condition: bool, message: str) -> None:
    """Raise AssertionError with a compact message when a guard fails."""
    if not condition:
        raise AssertionError(message)


def load_definition(category_id: str) -> dict:
    """Load a tracked category definition for test inspection."""
    return yaml.safe_load((ROOT / f"config/category-definitions/{category_id}.yaml").read_text(encoding="utf-8"))


def test_metadata_registry_is_provider_keyed_not_category_branched() -> None:
    """Provider adapter lookup should be driven by category-declared providers."""
    from src.core.categories.definition_backed import DefinitionBackedCategory
    from src.integrations.metadata_providers.registry import MetadataProviderRegistry

    source = (ROOT / "src/integrations/metadata_providers/registry.py").read_text(encoding="utf-8")
    require('category_id == "music"' not in source, "metadata registry must not branch on the Music category id")
    require('category_id in {"ebooks", "audiobooks"}' not in source, "metadata registry must not branch on Book category ids")
    require("ProviderRegistryEntry" in source and "profile_for_category" in source, "provider registry should be capability/provider keyed")

    registry = MetadataProviderRegistry()
    music = DefinitionBackedCategory(load_definition("music"))
    music_profile = registry.profile_for_category(music)
    require([spec.provider for spec in music_profile][:2] == ["musicbrainz", "discogs"], "Music profile should follow definition provider order")

    ebooks = DefinitionBackedCategory(load_definition("ebooks"))
    ebook_profile = {spec.provider: spec for spec in registry.profile_for_category(ebooks)}
    require(ebook_profile["internet_archive"].kwargs == {"mediatype": "texts"}, "Ebook IA media type should come from YAML kwargs")
    require(ebook_profile["apple_itunes_search"].kwargs == {"media": "ebook"}, "Ebook Apple media type should come from YAML kwargs")

    audiobooks = DefinitionBackedCategory(load_definition("audiobooks"))
    audiobook_profile = {spec.provider: spec for spec in registry.profile_for_category(audiobooks)}
    require(audiobook_profile["internet_archive"].kwargs == {"mediatype": "audio"}, "Audiobook IA media type should come from YAML kwargs")
    require(audiobook_profile["apple_itunes_search"].kwargs == {"media": "audiobook"}, "Audiobook Apple media type should come from YAML kwargs")


def test_audio_conversion_policy_is_profile_driven() -> None:
    """Automatic sidecar policy should use profile fields rather than category ids."""
    from src.core.categories.audio_conversion import AudioConversionPolicy

    source = (ROOT / "src/core/categories/audio_conversion.py").read_text(encoding="utf-8")
    require('category.category_id == "music"' not in source, "audio conversion must not special-case Music")
    require('category.category_id == "audiobooks"' not in source, "audio conversion must not special-case Audiobooks")

    require(
        AudioConversionPolicy({"preferred_lossless_format": "alac_m4a"}).automatic_sidecar_for_lossless()
        == ("apple_lossless_m4a", ".m4a"),
        "ALAC preference should choose lossless M4A sidecar",
    )
    require(
        AudioConversionPolicy({"auto_convert_lossless_to_preferred": True, "preferred_audio_format": "m4b"}).automatic_sidecar_for_lossless()
        == ("apple_aac_m4a", ".m4b"),
        "M4B preference should choose AAC M4B sidecar without category-id knowledge",
    )
    require(
        AudioConversionPolicy({"preferred_lossless_format": "flac", "auto_convert_lossless_to_preferred": False}).automatic_sidecar_for_lossless()
        is None,
        "FLAC-preserving profile should not auto-convert lossless sources",
    )


def test_quality_workspace_uses_generic_bundle_coverage_key() -> None:
    """Generic quality policy should consume category-neutral bundle coverage."""
    source = (ROOT / "src/ai/tools/search_workspace.py").read_text(encoding="utf-8")
    require("requested_bundle_coverage" in source, "quality workspace should expose generic bundle coverage")
    require("no_saved_bundle_quality_preference" in source, "quality policy reason should be bundle-neutral")
    require("no_saved_season_pack_quality_preference" not in source, "generic quality policy must not use season-pack reason names")
    require("compact 1080p HEVC full-season pack" not in source, "generic quality policy comments must not teach TV-specific examples")

    tv_source = (ROOT / "src/core/categories/tv_agent.py").read_text(encoding="utf-8")
    require("requested_bundle_coverage" in tv_source, "TV category should provide generic bundle coverage alongside TV-specific facts")


def test_metadata_resolver_uses_registry_boundary() -> None:
    """The resolver should use the registry collaborator instead of module-level category dispatch."""
    source = (ROOT / "src/integrations/category_metadata.py").read_text(encoding="utf-8")
    require("MetadataProviderRegistry" in source, "resolver should depend on MetadataProviderRegistry")
    require("provider_method(" not in source, "resolver should not call module-level provider dispatch helpers")
    require("provider_profile" not in source.splitlines()[17], "resolver import should not use provider_profile facade")


if __name__ == "__main__":
    for name in sorted(k for k in globals() if k.startswith("test_")):
        globals()[name]()
        print(f"PASS {name}")
