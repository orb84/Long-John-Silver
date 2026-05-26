#!/usr/bin/env python3
"""Round 126 metadata-cache/object-model regression checks."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_category_definitions_declare_object_models_without_private_config_leak() -> None:
    from src.core.category_config import CategoryConfigStore

    for category_id, expected in {
        "music": "release_group",
        "ebooks": "edition",
        "audiobooks": "narrator",
    }.items():
        payload = yaml.safe_load((ROOT / f"config/category-definitions/{category_id}.yaml").read_text(encoding="utf-8"))
        model = payload.get("object_model") or {}
        require(expected in " ".join(model.get("canonical_types", []) + model.get("disambiguation_facets", [])), f"{category_id} object model missing {expected}")

    store_source = (ROOT / "src/core/category_config.py").read_text(encoding="utf-8")
    require("'object_model'" in store_source, "object_model must be treated as definition-only, not private local config")


def test_metadata_cache_and_rate_limit_schema_are_in_base_schema_and_migration() -> None:
    db_source = (ROOT / "src/core/database.py").read_text(encoding="utf-8")
    migration = (ROOT / "migrations/108_metadata_cache_and_provider_rate_limits.sql").read_text(encoding="utf-8")
    for marker in ("category_metadata_cache", "provider_rate_limits", "idx_category_metadata_cache_stable_id"):
        require(marker in db_source, f"base schema missing {marker}")
        require(marker in migration, f"migration missing {marker}")


def test_metadata_results_have_stable_ids_object_models_and_disambiguation_hints() -> None:
    from src.integrations.category_metadata import CategoryMetadataResolver, ProviderResult, make_stable_id

    stable = make_stable_id("musicbrainz", {"musicbrainz_release_id": "abc"}, "Blur", ["Blur"], "1997")
    require(stable == "musicbrainz:musicbrainz_release_id:abc", "provider IDs should produce stable deterministic IDs")

    class DummyCategory:
        category_id = "music"
        def category_service_enabled(self, settings, provider, default=True):
            return default
        def category_service_secret(self, settings, provider, key):
            return None

    resolver = CategoryMetadataResolver(DummyCategory(), settings=None)
    results = [
        ProviderResult(
            provider="musicbrainz",
            title="Blur",
            contributors=["Blur"],
            year="1997",
            identifiers={"musicbrainz_release_id": "rel-1"},
            object_model={"model_type": "music_release", "release_type": "Album", "artist_credit": ["Blur"]},
        ),
        ProviderResult(
            provider="discogs",
            title="Blur",
            contributors=["Blur"],
            year="1997",
            identifiers={"discogs_id": "disc-1"},
            object_model={"model_type": "music_release", "release_type": "Album"},
        ),
    ]
    ranked, groups, disambiguation = resolver._rank_and_group("Blur album", results, limit=5)
    require(ranked and ranked[0].stable_id, "ranked results must keep stable IDs")
    require(ranked[0].object_model.get("model_type") == "music_release", "ranked result should expose the normalized object model")
    require("needs_llm_selection" in disambiguation, "resolver must expose LLM disambiguation state")
    require(disambiguation["selection_facets"], "resolver must expose selection facets for the LLM")


def test_provider_profiles_encode_cache_ttl_and_rate_limits() -> None:
    from src.integrations.category_metadata import CategoryMetadataResolver

    class DummyCategory:
        category_id = "music"
        def category_service_enabled(self, settings, provider, default=True):
            return default
        def category_service_secret(self, settings, provider, key):
            return None

    resolver = CategoryMetadataResolver(DummyCategory(), settings=None)
    profile = resolver._provider_profile()
    mb = next(spec for spec in profile if spec.provider == "musicbrainz")
    require(mb.ttl_seconds >= 7 * 24 * 60 * 60, "MusicBrainz responses should be persistently cached")
    require(mb.min_interval_seconds >= 1.0, "MusicBrainz provider profile must honor one-request-per-second discipline")

    source = (ROOT / "src/integrations/category_metadata.py").read_text(encoding="utf-8")
    require("MetadataCacheStore" in source, "resolver should use persistent cache store")
    require("ProviderRateLimiter" in source, "resolver should use provider rate limiter")
    require("llm_selection_instruction" in source, "resolver should explicitly delegate ambiguous selection to the LLM")


def test_definition_backed_resolve_metadata_passes_database_to_resolver() -> None:
    source = (ROOT / "src/core/categories/definition_backed.py").read_text(encoding="utf-8")
    require("db=getattr(context, \"db\", None)" in source, "metadata resolver should receive workflow DB for persistent cache access")


if __name__ == "__main__":
    for name in sorted(k for k in globals() if k.startswith("test_")):
        globals()[name]()
        print(f"PASS {name}")
