#!/usr/bin/env python3
"""Round 118 deep-review checks for metadata adapters, conversion, and Soulseek posture."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def load_yaml(path: str) -> dict:
    data = yaml.safe_load(text(path)) or {}
    require(isinstance(data, dict), f"{path} should be a mapping")
    return data


def test_metadata_resolver_exists_and_uses_declared_providers() -> None:
    resolver = text("src/integrations/category_metadata.py")
    providers = "\n".join([
        text("src/integrations/metadata_providers/registry.py"),
        text("src/integrations/metadata_providers/music.py"),
        text("src/integrations/metadata_providers/books.py"),
    ])
    require("class CategoryMetadataResolver" in resolver, "metadata resolver class should exist")
    require("provider_method" in resolver and "ProviderRateLimiter" in resolver, "resolver should orchestrate provider adapters and rate limits")
    for phrase in (
        "musicbrainz.org/ws/2/release",
        "coverartarchive.org/release",
        "openlibrary.org/search.json",
        "gutendex.com/books",
        "archive.org/advancedsearch.php",
        "librivox.org/api/feed/audiobooks",
        "itunes.apple.com/search",
        "api.discogs.com/database/search",
        "comicvine.gamespot.com/api/search",
        "requires a Chromaprint fingerprint",
    ):
        require(phrase in providers, f"metadata provider adapters should contain {phrase}")

    category = text("src/core/categories/definition_backed.py")
    require("CategoryMetadataResolver" in category, "definition-backed categories should call metadata resolver")
    require('workflow_name == "resolve_metadata"' in category, "resolve_metadata workflow should be implemented")
    require('"query"' in category and '"limit"' in category, "resolve_metadata tool schema should expose query/limit")


def test_ffmpeg_pipeline_preserves_metadata_chapters_and_cover_art() -> None:
    service = text("src/core/categories/audio_conversion.py")
    for token in (
        '"-map", "0:a:0"',
        '"-map", "0:v?"',
        '"-map_metadata", "0"',
        '"-map_chapters", "0"',
        '"-c:v", "copy"',
        '"-movflags", "+faststart"',
        "after_library_file_imported",
        "auto_convert_lossless_to_preferred",
        "source file",
    ):
        require(token in service, f"conversion service should include {token}")
    category = text("src/core/categories/definition_backed.py")
    require("AudioConversionService" in category, "definition-backed category should delegate audio conversion")

    audio = load_yaml("config/category-definitions/audio.yaml")
    deps = audio.get("runtime_dependencies") or {}
    require("ffmpeg" in deps, "audio mixin should declare FFmpeg")
    require(deps["ffmpeg"].get("required") is True, "FFmpeg should be a required runtime dependency for conversion")


def test_download_handler_runs_category_post_import_hooks() -> None:
    handler = text("src/core/download_handler.py")
    for phrase in (
        "_run_category_post_import_hooks",
        "after_library_file_imported",
        "download_import_sidecar",
        "reconcile_library_item_from_path",
    ):
        require(phrase in handler, f"download ready flow should include {phrase}")
    base_contract = text("src/core/categories/base_contract.py")
    require("async def after_library_file_imported" in base_contract, "category contract should expose a default post-import hook")


def test_compass_and_setup_expose_book_audio_preferences() -> None:
    panel = text("src/web/static/js/components/settingsPanel.js")
    setup_template = text("src/web/templates/setup.html")
    setup_js = text("src/web/static/js/pages/setup.js")
    for phrase in (
        "Download and conversion preferences",
        "preferred_lossless_format",
        "preferred_audio_format",
        "preferred_ebook_format",
        "pref-category-download-profile-input",
        "Auto-convert lossless audiobook sources",
    ):
        require(phrase in panel, f"settings/Compass category panel should expose {phrase}")
    for phrase in (
        "setup-music-lossless-format",
        "setup-audiobook-format",
        "setup-ebook-format",
        "setup-music-auto-convert",
        "setup-audiobook-auto-convert",
    ):
        require(phrase in setup_template, f"setup template should expose {phrase}")
    require("saveSetupBookAudioPreferences" in setup_js, "setup JS should save book/audio first-run preferences")


def test_profiles_describe_non_surprising_conversion_policy() -> None:
    music = load_yaml("config/category-definitions/music.yaml")
    audiobooks = load_yaml("config/category-definitions/audiobooks.yaml")
    ebooks = load_yaml("config/category-definitions/ebooks.yaml")
    music_profile = music.get("download_profile") or {}
    audio_profile = audiobooks.get("download_profile") or {}
    ebook_profile = ebooks.get("download_profile") or {}
    require(music_profile.get("preferred_lossless_format") == "flac", "music default should keep FLAC rather than surprise-convert")
    require(music_profile.get("auto_convert_lossless_to_preferred") is False, "music auto AAC conversion should be opt-in")
    require(audio_profile.get("auto_convert_lossless_to_preferred") is True, "audiobooks can default to M4B sidecars from lossless sources")
    require("lossy-transcode MP3" in audio_profile.get("conversion_policy", ""), "audiobook policy should avoid MP3->AAC surprises")
    require(ebook_profile.get("avoid_format_conversion") is True, "ebook conversion should not be claimed implemented")


def test_soulseek_review_documents_non_implementation_decision() -> None:
    doc_path = ROOT / "docs/SOULSEEK_RESEARCH_AND_INTEGRATION_REVIEW.md"
    require(doc_path.exists(), "Soulseek research review doc should exist")
    doc = doc_path.read_text(encoding="utf-8")
    for phrase in (
        "Not implemented in Round 118",
        "slskd",
        "Nicotine+",
        "single-source",
        "sharing policy",
        "Soulseek credentials",
        "staging/download directory",
        "do not implement the reverse-engineered protocol",
        "separate source adapter",
    ):
        require(phrase in doc, f"Soulseek doc should mention {phrase}")


if __name__ == "__main__":
    for name in sorted(k for k in globals() if k.startswith("test_")):
        globals()[name]()
        print(f"PASS {name}")
